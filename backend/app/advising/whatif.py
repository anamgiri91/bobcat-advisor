"""
whatif.py
=========
What-if planning: how would switching major, adding a minor, failing a
course or changing the course load move the graduation date?

Each scenario reruns the deterministic part of the advising pipeline
(research -> fact-check -> audit -> schedule/roadmap) on a modified profile
and compares it with the baseline. No LLM is involved, so every number is
reproducible and a comparison costs milliseconds once pages are cached.

Graduation estimate, per plan:
  terms = max(terms the roadmap needs to place every named requirement,
              ceil(hours still needed / target load))
  hours still needed = max(program total - hours earned, hours of the
                           remaining named requirements and electives)
The second term is why the estimate isn't just the roadmap: a degree is
also a total-hours requirement (core curriculum, free electives).

Assumptions are listed on every result (e.g. minor courses may count toward
both the major and the minor; TXST's double-counting limits still apply).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

from ..tracing import span
from .audit import AuditResult, audit, course_hours
from .factcheck import FactCheckReport, fact_check
from .profile import StudentProfile, build_profile, normalise_codes
from .research import Pool, Requirement, ResearchResult, research
from .scheduler import SchedulePlan, _next_label, plan_schedule
from .web import Browser

SCENARIO_TYPES = ("switch_major", "add_minor", "fail_course", "change_load")
MAX_SCENARIOS = 4


@dataclass
class Outcome:
    name: str
    description: str
    program: str | None
    graduation_term: str | None
    terms_remaining: int | None
    hours_remaining: int | None
    requirements_remaining: int
    roadmap: list[dict] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    delta_terms: int | None = None          # vs baseline; + means later
    delta_hours: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Plan -> graduation estimate
# ---------------------------------------------------------------------------

def _remaining_required_hours(result: AuditResult) -> int:
    named = sum(r.hours for r in result.remaining)
    pools = sum(p.hours_left for p in result.pools if p.status != "done")
    return named + pools


def estimate(profile: StudentProfile, result: AuditResult, plan: SchedulePlan,
             name: str, description: str, research_result: ResearchResult | None = None) -> Outcome:
    notes: list[str] = []
    placed = {c for t in plan.roadmap for c in t["courses"]}
    unplaced = [r.label for r in result.remaining if not set(r.options) & placed]
    if not result.available:
        return Outcome(name, description, None, None, None, None, 0,
                       notes=["Degree requirements couldn't be verified online, so no graduation "
                              "estimate is possible."])

    required_left = _remaining_required_hours(result)
    to_total = None
    if result.total_hours:
        to_total = max(0, result.total_hours - result.hours_completed - result.hours_in_progress)
    # A failed attempt earns no credit: its hours are taken again later.
    failed_hours = sum(course_hours(c, research_result or ResearchResult())
                       for t in plan.roadmap for c in t.get("failed", []))
    hours_left = max(required_left, to_total or 0) + failed_hours
    by_load = math.ceil(hours_left / max(profile.target_credits, 1)) if hours_left else 0
    by_roadmap = len(plan.roadmap)
    terms = max(by_load, by_roadmap)
    if unplaced:
        notes.append("The roadmap couldn't place " + ", ".join(unplaced[:5])
                     + " (prerequisites outside the listed requirements); the estimate may be early.")
    if by_load > by_roadmap:
        notes.append(f"Total hours set the pace: about {hours_left} hours to go at "
                     f"{profile.target_credits} per term.")
    term = profile.planned_term
    for _ in range(max(terms - 1, 0)):
        term = _next_label(term)
    return Outcome(
        name=name, description=description, program=result.program,
        graduation_term=term if terms else profile.planned_term,
        terms_remaining=terms, hours_remaining=hours_left,
        requirements_remaining=len(result.remaining), roadmap=plan.roadmap,
        unplaced=unplaced, notes=notes,
    )


# ---------------------------------------------------------------------------
# One plan, deterministic
# ---------------------------------------------------------------------------

@dataclass
class _Run:
    profile: StudentProfile
    research: ResearchResult
    report: FactCheckReport
    audit: AuditResult
    plan: SchedulePlan


def _plan(profile: StudentProfile, browser: Browser, minor: str | None = None,
          fail: set[str] | None = None) -> tuple[_Run | None, list[str]]:
    found = research(profile, browser)
    report, verified = fact_check(found, profile, browser.pages)
    notes: list[str] = []
    if minor:
        major_courses = {o for r in verified.requirements for o in r.options}
        extra, notes = _minor_requirements(minor, profile, browser, major_courses)
        if extra is None:
            return None, notes
        verified = ResearchResult(
            program=verified.program,
            requirements=verified.requirements + extra.requirements,
            pools=verified.pools + extra.pools,
            sequence=verified.sequence,
            courses={**extra.courses, **verified.courses},
            errors=verified.errors + extra.errors,
            method=verified.method,
        )
    result = audit(profile, verified, report)
    return _Run(profile, verified, report, result,
                plan_schedule(profile, verified, result, report, fail)), notes


def _minor_requirements(minor: str, profile: StudentProfile, browser: Browser,
                        major_courses: set[str]) -> tuple[ResearchResult | None, list[str]]:
    """The minor's fact-checked requirements, relabelled 'Minor: ...' with their own ids.
    A requirement the major already names is left out: one course counts toward both."""
    minor_profile = replace(profile, major=f"{minor} minor", degree="MINOR")
    found = research(minor_profile, browser)
    if not found.found:
        return None, [f"Couldn't read the {minor} minor's requirements: "
                      + "; ".join(found.errors[:2] or ["no program page found"])]
    _, verified = fact_check(found, minor_profile, browser.pages)
    reqs = [Requirement(id=f"M{r.id}", section=f"Minor: {r.section}", options=r.options,
                        hours=r.hours, quote=r.quote, source_url=r.source_url, method=r.method,
                        titles=r.titles) for r in verified.requirements
            if not set(r.options) & major_courses]
    pools = [Pool(id=f"M{p.id}", section=f"Minor: {p.section}", rule=p.rule,
                  hours_required=p.hours_required, options=p.options, quote=p.quote,
                  source_url=p.source_url, method=p.method) for p in verified.pools]
    return (ResearchResult(requirements=reqs, pools=pools, courses=verified.courses),
            ["Minor courses are assumed to count toward the major too where they overlap; "
             "TXST's limits on double counting still apply."])


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _describe(s: dict) -> str:
    kind = s.get("type")
    if kind == "switch_major":
        return f"Switch to {s.get('major')} ({(s.get('degree') or 'BS').upper()})"
    if kind == "add_minor":
        return f"Add a {s.get('minor')} minor"
    if kind == "fail_course":
        return f"Fail {s.get('course')} and retake it"
    if kind == "change_load":
        return f"Take {s.get('target_credits')} hours per term"
    return str(kind)


def run_scenario(raw: dict, scenario: dict, browser: Browser) -> Outcome:
    kind = scenario.get("type")
    description = _describe(scenario)
    notes: list[str] = []
    fail: set[str] = set()
    raw = dict(raw)

    if kind == "switch_major":
        raw["major"], raw["degree"] = scenario.get("major") or raw.get("major"), scenario.get("degree") or "BS"
    elif kind == "change_load":
        raw["target_credits"] = scenario.get("target_credits") or raw.get("target_credits")
    elif kind == "fail_course":
        code = (normalise_codes(scenario.get("course") or "") or [""])[0]
        if not code:
            return Outcome("fail_course", description, None, None, None, None, 0,
                           notes=["Not a course code."])
        completed = normalise_codes(raw.get("completed"))
        in_progress = normalise_codes(raw.get("in_progress"))
        if code in completed or code in in_progress:
            raw["completed"] = [c for c in completed if c != code]
            raw["in_progress"] = [c for c in in_progress if c != code]
            notes.append(f"{code} no longer counts as passed; it has to be retaken.")
        else:
            fail = {code}
            notes.append(f"{code} is failed the first time it's planned and retaken later.")

    profile, _ = build_profile(raw)
    minor = (scenario.get("minor") or "").strip() if kind == "add_minor" else None
    run, plan_notes = _plan(profile, browser, minor, fail)
    notes += plan_notes
    if run is None:
        return Outcome(kind or "scenario", description, None, None, None, None, 0, notes=notes)
    out = estimate(profile, run.audit, run.plan, kind or "scenario", description, run.research)
    out.notes = notes + out.notes
    return out


def what_if(raw: dict, scenarios: list[dict], browser: Browser | None = None) -> dict:
    """Baseline plus each scenario, with the graduation shift in terms."""
    browser = browser or Browser(max_pages=40)   # pages are cached across scenarios
    with span("advising.whatif", scenarios=len(scenarios)) as s:
        base_profile, flags = build_profile(raw)
        base, _ = _plan(base_profile, browser)
        baseline = estimate(base_profile, base.audit, base.plan, "baseline", "Your current plan",
                            base.research)
        outcomes = []
        for sc in scenarios[:MAX_SCENARIOS]:
            if sc.get("type") not in SCENARIO_TYPES:
                outcomes.append(Outcome("invalid", str(sc.get("type")), None, None, None, None, 0,
                                        notes=[f"Unknown scenario type; use one of {SCENARIO_TYPES}."]))
                continue
            o = run_scenario(raw, sc, browser)
            if o.terms_remaining is not None and baseline.terms_remaining is not None:
                o.delta_terms = o.terms_remaining - baseline.terms_remaining
                o.delta_hours = (o.hours_remaining or 0) - (baseline.hours_remaining or 0)
            outcomes.append(o)
        s.attributes["scenarios"] = len(outcomes)
    return {"planned_term": base_profile.planned_term, "flags": flags,
            "baseline": baseline.to_dict(), "scenarios": [o.to_dict() for o in outcomes]}
