"""
pipeline.py
===========
Career-path recommendations: seven agents, one event stream.

    career goal + courses passed
         │
    ┌────▼──────────┐ maps the goal to a defined career (rules, then LLM
    │Career analyst │ picking from the list) and its core/helpful skills
    └────┬──────────┘
    ┌────▼──────────┐ which TXST courses teach each skill, from the official
    │ Course mapper │ catalog text; ranks them, adds gateway prerequisites,
    └────┬──────────┘ marks each done / eligible now / later
    ┌────▼──────────┐ skills the catalog doesn't cover (or only mentions),
    │  Gap finder   │ and covered core skills worth going deeper on
    └────┬──────────┘
    ┌────▼──────────┐ web search per gap (when configured) + curated seeds
    │ Resource scout│ from providers it knows
    └────┬──────────┘
    ┌────▼──────────┐ opens every link: live? on topic? dead links and
    │ Link checker  │ off-topic pages are dropped
    └────┬──────────┘
    ┌────▼──────────┐ a short roadmap from the facts above (LLM, or a
    │    Mentor     │ template without one)
    └────┬──────────┘
    ┌────▼──────────┐ every course code in the roadmap must be one it was
    │   Verifier    │ given; lines that aren't are removed
    └───────────────┘

Only the analyst's fallback and the mentor use an LLM. Everything that
decides (skills, courses, eligibility, which links survive) is
deterministic and tested.

Events: agent, career, skills, courses, search, browse, resources, memo,
verification, done (always last) or error.
"""

from __future__ import annotations

import contextvars
import json
import queue
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from .. import llm
from ..advising.profile import normalise_codes
from ..advising.web import Browser
from ..config import settings
from ..tracing import span
from . import mapping, scout
from .paths import allowed_domains, resolve

AGENTS = ["career_analyst", "course_mapper", "gap_finder", "scout", "link_checker", "mentor", "verifier"]
MAX_DEEPER = 3
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="career")


class CareerError(ValueError):
    """The request can't be answered (e.g. no matching career); shown to the student."""


def _targets(cov: list[mapping.SkillCoverage]) -> list[tuple[str, str]]:
    """Gaps first (core before helpful), then a few covered core skills to go deeper on."""
    gaps = [(s.id, "gap") for s in cov if s.coverage in ("gap", "partial")]
    gaps.sort(key=lambda t: next(s.importance != "core" for s in cov if s.id == t[0]))
    deeper = [(s.id, "deeper") for s in cov if s.coverage == "covered" and s.importance == "core"
              and s.id != "programming"][:MAX_DEEPER]
    return gaps + deeper


# ---------------------------------------------------------------------------
# Mentor + verifier
# ---------------------------------------------------------------------------

def _facts(career, cov, courses, groups, experience) -> dict:
    next_up = [c for c in courses if c["status"] == "eligible"][:4]
    later = [c for c in courses if c["status"] == "later"][:4]
    return {
        "career": career.title,
        "covered": [s.name for s in cov if s.coverage == "covered"],
        "gaps": [s.name for s in cov if s.coverage != "covered"],
        "take_now": [{"code": c["code"], "title": c["title"], "unlocks": len(c.get("unlocks", [])),
                      "skills": c["skills"]} for c in next_up],
        "later": [{"code": c["code"], "title": c["title"], "needs": c["missing"]} for c in later],
        "outside": [{"title": r["title"], "provider": r["provider"], "for": r["for_skill"]}
                    for r in groups["gap"][:8]],
        "certifications": [{"title": r["title"], "provider": r["provider"]}
                           for r in groups["certification"][:2]],
        "experience": [{"code": e["code"], "title": e["title"]} for e in experience[:2]],
    }


def _named(r: dict) -> str:
    """"Title (Provider)", without repeating a provider that's in the title."""
    return r["title"] if r["provider"].lower() in r["title"].lower() else f"{r['title']} ({r['provider']})"


def template_memo(f: dict) -> str:
    lines = []
    if f["take_now"]:
        first = f["take_now"][0]
        why = (f"it unlocks {first['unlocks']} courses on this path" if first["unlocks"]
               else f"it teaches {', '.join(first['skills'][:2]).lower()}")
        rest = ", ".join(c["code"] for c in f["take_now"][1:])
        lines.append(f"**Next term:** {first['code']} ({first['title']}): {why}."
                     + (f" You're also eligible for {rest}." if rest else ""))
    if f["later"]:
        lines.append("**After that:** " + "; ".join(
            f"{c['code']} {c['title']}" + (f" (needs {', '.join(c['needs'])})" if c["needs"] else "")
            for c in f["later"]) + ".")
    if f["outside"]:
        first_per_skill = {}
        for r in f["outside"]:
            first_per_skill.setdefault(r["for"], r)
        lines.append("**Outside class:** the CS catalog doesn't fully cover these, so learn them on your own: "
                     + "; ".join(f"{skill}: {_named(r)}"
                                 for skill, r in list(first_per_skill.items())[:4]) + ".")
    if f["experience"]:
        lines.append("**Get experience:** " + " or ".join(
            f"{e['code']} ({e['title']})" for e in f["experience"]) + " counts for credit.")
    if f["certifications"]:
        lines.append("**Optional certification:** " + "; ".join(_named(c) for c in f["certifications"])
            + ". Check current cost and requirements on the provider's site.")
    return "\n\n".join(lines)


def _llm_memo(f: dict) -> str:
    return llm.chat([
        {"role": "system", "content":
            "You are a college career mentor for a Texas State CS student. Write a short roadmap "
            "(at most 170 words) with bold labels: Next term, After that, Outside class, Get "
            "experience, Optional certification (skip a label with no facts). Use ONLY the facts "
            "given. Name TXST courses by the exact codes given and outside resources by the exact "
            "titles given. No URLs, no prices, no course codes that aren't in the facts."},
        {"role": "user", "content": json.dumps(f)},
    ], agent="career.mentor", max_tokens=420, temperature=0.3, timeout_s=20)


_CODE = re.compile(r"\b([A-Z]{2,4})\s?(\d{4})\b")


def verify_memo(text: str, allowed_codes: set[str]) -> tuple[str, dict]:
    """Drop sentences naming a course code the pipeline didn't supply, and any URL."""
    removed: list[str] = []
    kept_paras = []
    for para in text.split("\n\n"):
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        good = []
        for sent in sentences:
            codes = {a + b for a, b in _CODE.findall(sent)}
            if codes - allowed_codes or re.search(r"https?://", sent):
                removed.append(sent)
            else:
                good.append(sent)
        if good and any(re.sub(r"\*\*[^*]+\*\*:?", "", g).strip() for g in good):
            kept_paras.append(" ".join(good))
    checked = len(_CODE.findall(text))
    return "\n\n".join(kept_paras), {"course_mentions_checked": checked, "removed": removed}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _check_live(candidates, browser: Browser, events: queue.Queue) -> Iterator[dict]:
    ctx = contextvars.copy_context()
    future = _pool.submit(ctx.run, scout.check, candidates, browser)
    deadline = time.monotonic() + settings.WEB_TIMEOUT_S * 4 + 5
    while not future.done() and time.monotonic() < deadline:
        try:
            yield events.get(timeout=0.1)
        except queue.Empty:
            pass
    while not events.empty():
        yield events.get_nowait()
    try:
        kept, dropped = future.result(timeout=max(0.0, deadline - time.monotonic()))
    except Exception as e:
        kept, dropped = [], [{"url": "", "title": "", "reason": f"link check failed: {type(e).__name__}"}]
    yield {"type": "_result", "kept": kept, "dropped": dropped}


def run(raw: dict) -> Iterator[dict]:
    """The pipeline as an event stream. Must be called inside tracing.start_trace()."""
    t = time.perf_counter()

    def elapsed() -> int:
        nonlocal t
        ms, t = (time.perf_counter() - t) * 1000, time.perf_counter()
        return round(ms)

    completed = set(normalise_codes(" ".join(raw.get("completed") or [])))
    in_progress = set(normalise_codes(" ".join(raw.get("in_progress") or [])))

    # -- career analyst -----------------------------------------------------
    yield {"type": "agent", "agent": "career_analyst", "status": "start"}
    with span("agent.career_analyst"):
        res = resolve(raw.get("career") or "")
    if res.career is None:
        raise CareerError(res.note)
    career = res.career
    yield {"type": "career", "career": career.to_dict(), "method": res.method, "note": res.note}
    yield {"type": "agent", "agent": "career_analyst", "status": "done", "ms": elapsed(),
           "summary": f"{career.title}: {len(career.core)} core, {len(career.helpful)} helpful skills"}

    # -- course mapper ------------------------------------------------------
    yield {"type": "agent", "agent": "course_mapper", "status": "start"}
    with span("agent.course_mapper"):
        cov = mapping.coverage(career)
        courses = mapping.recommend_courses(career, cov, completed, in_progress)
        experience = mapping.experience(completed | in_progress)
    skills_d = [s.to_dict() for s in cov]
    counts = {k: sum(s.coverage == k for s in cov) for k in ("covered", "partial", "gap")}
    yield {"type": "courses", "courses": courses, "experience": experience}
    yield {"type": "agent", "agent": "course_mapper", "status": "done", "ms": elapsed(),
           "summary": f"{sum(c['status'] != 'done' for c in courses)} TXST courses, "
                      f"{sum(c['status'] == 'eligible' for c in courses)} available now"}

    # -- gap finder -----------------------------------------------------------
    yield {"type": "agent", "agent": "gap_finder", "status": "start"}
    targets = _targets(cov)
    yield {"type": "skills", "skills": skills_d, "coverage": counts}
    yield {"type": "agent", "agent": "gap_finder", "status": "done", "ms": elapsed(),
           "summary": f"{counts['covered']} covered, {counts['partial']} partly, {counts['gap']} not in the catalog"}

    # -- resource scout -----------------------------------------------------
    yield {"type": "agent", "agent": "scout", "status": "start"}
    searches: list[dict] = []
    with span("agent.scout") as s:
        candidates = scout.gather(targets, career.id, career.title,
                                  include_certifications=raw.get("include_certifications", True),
                                  free_only=raw.get("free_only", False), on_search=searches.append)
        s.attributes.update(candidates=len(candidates), searches=len(searches))
    for q in searches:
        yield {"type": "search", **q}
    method = "web search + curated list" if searches else "curated list (no search key configured)"
    yield {"type": "agent", "agent": "scout", "status": "done", "ms": elapsed(),
           "summary": f"{len(candidates)} candidates from {method}"}

    # -- link checker -------------------------------------------------------
    yield {"type": "agent", "agent": "link_checker", "status": "start"}
    events: queue.Queue = queue.Queue()
    browser = Browser(max_pages=settings.CAREER_MAX_PAGES, allowed_domains=allowed_domains(),
                      on_visit=lambda v: events.put({"type": "browse", **v.to_dict()}))
    kept, dropped = [], []
    with span("agent.link_checker"):
        for ev in _check_live(candidates[: settings.CAREER_MAX_PAGES], browser, events):
            if ev["type"] == "_result":
                kept, dropped = ev["kept"], ev["dropped"]
            else:
                yield ev
    groups = scout.select(kept)
    verified = sum(c.check.get("status") == "verified" for c in kept)
    yield {"type": "resources", "resources": groups, "dropped": dropped}
    yield {"type": "agent", "agent": "link_checker", "status": "done", "ms": elapsed(),
           "summary": f"{verified} verified live, {len(kept) - verified} not checkable, {len(dropped)} dropped"}

    # -- mentor ---------------------------------------------------------------
    yield {"type": "agent", "agent": "mentor", "status": "start"}
    facts = _facts(career, cov, courses, groups, experience)
    memo, mode, error = template_memo(facts), "template", None
    if llm.is_available():
        try:
            memo, mode = _llm_memo(facts), "llm"
        except Exception as e:                    # keep the template; say why
            error = f"LLM unavailable ({type(e).__name__}); used the template"
    yield {"type": "agent", "agent": "mentor", "status": "done", "ms": elapsed(), "error": error,
           "summary": "roadmap written" + (" (template)" if mode == "template" else "")}

    # -- verifier -------------------------------------------------------------
    yield {"type": "agent", "agent": "verifier", "status": "start"}
    allowed = {c["code"] for c in courses} | {e["code"] for e in experience}
    for c in courses:
        allowed |= {code for m in c["missing"] for code in re.findall(r"[A-Z]{2,4}\d{4}", m)}
    memo, verification = verify_memo(memo, allowed)
    if not memo.strip():
        memo, mode = template_memo(facts), "template"
    yield {"type": "memo", "text": memo, "mode": mode}
    yield {"type": "verification", **verification}
    yield {"type": "agent", "agent": "verifier", "status": "done", "ms": elapsed(),
           "summary": f"{verification['course_mentions_checked']} course mentions checked, "
                      f"{len(verification['removed'])} removed"}

    yield {"type": "done", "career": career.to_dict(), "method": res.method, "note": res.note,
           "skills": skills_d, "coverage": counts, "courses": courses, "experience": experience,
           "resources": groups, "dropped": dropped, "visits": [v.to_dict() for v in browser.visits],
           "searches": searches, "memo": memo, "mode": mode, "verification": verification}


def recommend(raw: dict) -> dict:
    """Non-streaming: the final `done` event."""
    done: dict = {}
    for ev in run(raw):
        if ev["type"] == "done":
            done = ev
    return done
