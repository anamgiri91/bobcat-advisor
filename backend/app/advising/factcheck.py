"""
factcheck.py
============
The fact-checking agent: decides which of the researcher's findings are
trustworthy enough to advise on.

Every fact (a requirement, an elective pool, a course's prerequisites, the
program's catalog year) goes through the same checks:

  official   the source URL is an allowlisted TXST address
  grounded   the fact's quote, and every course code it names, appear on the
             fetched page. For parsed tables this is a consistency check;
             for LLM-extracted facts it is the hallucination guard.
  cross-src  course codes, titles and prerequisites are compared with the
             bundled catalog snapshot (documents/official). Agreement raises
             confidence; disagreement is a CONFLICT the student is told
             about, since one of the two sources is out of date.
  currency   the page's catalog year vs. the student's catalog year.

Outcomes: verified (used), conflict (used, with a warning, and planning
takes the stricter reading), unverified (dropped before the audit and the
advisor ever see it).

It's deterministic on purpose: a second LLM judging the first LLM's
extraction adds cost and a correlated failure mode; string containment and
set comparison don't hallucinate.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..knowledge import catalog as cat
from ..tracing import span
from .profile import StudentProfile
from .research import Pool, Requirement, ResearchResult, WebCourse
from .web import Page, is_allowed

VERIFIED, CONFLICT, UNVERIFIED = "verified", "conflict", "unverified"


@dataclass
class Check:
    id: str
    kind: str                     # requirement | pool | course | program
    claim: str
    status: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class FactCheckReport:
    checks: list[Check] = field(default_factory=list)

    def status(self, fact_id: str) -> str | None:
        return next((c.status for c in self.checks if c.id == fact_id), None)

    def by_status(self, status: str) -> list[Check]:
        return [c for c in self.checks if c.status == status]

    @property
    def summary(self) -> dict:
        n = len(self.checks)
        return {
            "checked": n,
            "verified": len(self.by_status(VERIFIED)),
            "conflicts": len(self.by_status(CONFLICT)),
            "unverified": len(self.by_status(UNVERIFIED)),
            "avg_confidence": round(sum(c.confidence for c in self.checks) / n, 3) if n else None,
        }

    def to_dict(self) -> dict:
        return {"summary": self.summary, "checks": [asdict(c) for c in self.checks]}


def _squash(text: str) -> str:
    """Compare text ignoring case, whitespace and punctuation (table cells lose spacing)."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _grounded(quote: str, codes: list[str], page: Page | None) -> tuple[bool, str]:
    if page is None:
        return False, "the source page wasn't fetched in this session"
    body = _squash(page.text)
    parts = [p for p in (quote or "").split(" | ") if p.strip()]
    if not parts:
        return False, "no supporting quote"
    missing = [p for p in parts if _squash(p) not in body]
    if missing:
        return False, f"quote not found on the page: \"{missing[0][:80]}\""
    absent = [c for c in codes if _squash(c) not in body]
    if absent:
        return False, f"{', '.join(absent)} not found on the page"
    return True, "found verbatim on the official page"


def _similar_titles(a: str, b: str) -> bool:
    ta = set(re.findall(r"[a-z0-9]+", a.lower())) - {"and", "of", "the", "i", "to", "in"}
    tb = set(re.findall(r"[a-z0-9]+", b.lower())) - {"and", "of", "the", "i", "to", "in"}
    if not ta or not tb:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.5


def _page_for(url: str, pages: dict[str, Page]) -> Page | None:
    if url in pages:
        return pages[url]
    return next((p for p in pages.values() if p.url == url), None)


def _check_codes(codes: list[str], titles: dict[str, str], check: Check) -> None:
    """Cross-check CS codes and titles with the local catalog snapshot."""
    for code in codes:
        if not code.startswith("CS"):
            continue
        local = cat.get_course(code)
        if local is None:
            check.reasons.append(f"{code} isn't in the local catalog snapshot (new or renumbered?)")
            check.confidence = min(check.confidence, 0.75)
            continue
        check.sources.append(f"TXST Course Catalog snapshot — {code}")
        if code in titles and not _similar_titles(titles[code], local.title):
            check.status = CONFLICT
            check.confidence = 0.5
            check.reasons.append(f"{code} is \"{titles[code]}\" on the live page but "
                                 f"\"{local.title}\" in the snapshot")


def check_requirement(r: Requirement | Pool, pages: dict[str, Page]) -> Check:
    is_pool = isinstance(r, Pool)
    claim = (f"{r.section}: {r.rule} {', '.join(r.options)}" if is_pool
             else f"{r.section}: {r.label()}" + (f" ({r.hours} hrs)" if r.hours else ""))
    check = Check(id=r.id, kind="pool" if is_pool else "requirement", claim=claim,
                  status=VERIFIED, confidence=0.8 if r.method == "parser" else 0.7,
                  sources=[r.source_url])
    if not is_allowed(r.source_url):
        check.status, check.confidence = UNVERIFIED, 0.0
        check.reasons.append("source is not an official TXST page")
        return check
    ok, why = _grounded(r.quote, r.options, _page_for(r.source_url, pages))
    check.reasons.append(why)
    if not ok:
        check.status, check.confidence = UNVERIFIED, 0.0
        return check
    if r.method == "llm":
        check.reasons.append("extracted by the LLM fallback, quote-checked")
    _check_codes(r.options, getattr(r, "titles", {}), check)
    if check.status == VERIFIED and any(s.startswith("TXST Course Catalog") for s in check.sources):
        check.confidence = max(check.confidence, 0.95 if r.method == "parser" else 0.85)
    return check


def _groups(prereq_text: str) -> set[frozenset[str]]:
    groups, _ = cat.parse_prereqs(prereq_text)
    return {frozenset(g.courses) for g in groups}


def _fmt_groups(groups: set[frozenset[str]]) -> str:
    return "; ".join(" or ".join(sorted(g)) for g in sorted(groups, key=sorted)) or "none"


def check_course(wc: WebCourse, pages: dict[str, Page]) -> Check:
    check = Check(id=f"course:{wc.code}", kind="course",
                  claim=f"{wc.code} {wc.title}; prerequisite: {wc.prereq_text or 'none'}",
                  status=VERIFIED, confidence=0.8, sources=[wc.url])
    ok, why = _grounded(wc.quote, [wc.code], _page_for(wc.url, pages))
    check.reasons.append(why)
    if not ok or not is_allowed(wc.url):
        check.status, check.confidence = UNVERIFIED, 0.0
        return check
    local = cat.get_course(wc.code)
    if local is None:
        if wc.code.startswith("CS"):
            check.reasons.append("not in the local catalog snapshot; live catalog only")
        return check
    check.sources.append(f"TXST Course Catalog snapshot — {wc.code}")
    if not _similar_titles(wc.title, local.title):
        check.status, check.confidence = CONFLICT, 0.5
        check.reasons.append(f"title differs: live \"{wc.title}\" vs snapshot \"{local.title}\"")
    live, snap = _groups(wc.prereq_text), _groups(local.prereq_text)
    if live == snap:
        check.reasons.append("prerequisites match the catalog snapshot")
        if check.status == VERIFIED:
            check.confidence = 0.95
    else:
        check.status, check.confidence = CONFLICT, 0.5
        check.reasons.append(f"prerequisites differ: live catalog requires {_fmt_groups(live)}, "
                             f"snapshot requires {_fmt_groups(snap)}; planning uses both (stricter)")
    return check


def check_program(research: ResearchResult, profile: StudentProfile) -> Check | None:
    prog = research.program
    if prog is None:
        return None
    check = Check(id="program", kind="program",
                  claim=f"{prog.name}, {prog.catalog_year or 'catalog year unknown'}"
                        + (f", {prog.total_hours} total hours" if prog.total_hours else ""),
                  status=VERIFIED, confidence=0.9, sources=[prog.url])
    if not is_allowed(prog.url):
        check.status, check.confidence = UNVERIFIED, 0.0
        check.reasons.append("program page is not an official TXST page")
        return check
    if prog.found_by == "search":
        check.reasons.append("program page found through the catalog search")
        check.confidence = 0.8
    if not prog.catalog_year:
        check.reasons.append("the page doesn't state its catalog year")
        check.confidence = min(check.confidence, 0.7)
    elif profile.catalog_year and profile.catalog_year != prog.catalog_year:
        check.status, check.confidence = CONFLICT, 0.5
        check.reasons.append(f"live page is the {prog.catalog_year} catalog but you follow "
                             f"{profile.catalog_year}; your requirements may differ")
    if prog.total_hours and not 100 <= prog.total_hours <= 140:
        check.reasons.append(f"unusual total of {prog.total_hours} hours")
        check.confidence = min(check.confidence, 0.6)
    return check


def fact_check(research: ResearchResult, profile: StudentProfile,
               pages: dict[str, Page]) -> tuple[FactCheckReport, ResearchResult]:
    """Returns the report and a copy of `research` without unverified facts."""
    with span("agent.fact_checker") as s:
        report = FactCheckReport()
        prog = check_program(research, profile)
        if prog:
            report.checks.append(prog)
        report.checks += [check_requirement(r, pages) for r in research.requirements]
        report.checks += [check_requirement(p, pages) for p in research.pools]
        report.checks += [check_course(c, pages) for c in research.courses.values()]

        keep = {c.id for c in report.checks if c.status != UNVERIFIED}
        verified = ResearchResult(
            program=research.program if (prog is None or prog.status != UNVERIFIED) else None,
            requirements=[r for r in research.requirements if r.id in keep],
            pools=[p for p in research.pools if p.id in keep],
            sequence=research.sequence,
            courses={k: v for k, v in research.courses.items() if f"course:{k}" in keep},
            errors=research.errors,
            notes=research.notes,
            titles=research.titles,
            method=research.method,
        )
        s.attributes.update(report.summary)
    return report, verified
