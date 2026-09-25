"""
audit.py
========
The degree-audit agent: matches the student's courses against the
fact-checked requirements. Pure set logic, no LLM.

  - each requirement is done / in progress / remaining
  - elective pools count hours, and a course is never counted twice (one
    already used for a named requirement doesn't also fill a pool)
  - hours completed / remaining against the program total, and a rough
    number of terms left at the student's target load
  - "behind the suggested plan": courses the catalog's four-year plan puts
    before the student's current term that they haven't taken

Hours use the live catalog when a course page was read, else TXST's
numbering convention (second digit = credit hours), and are labelled as an
estimate: transfer credit, AP and repeats aren't visible here, which is why
the advisor always points the student to their official degree audit.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from ..tracing import span
from .factcheck import CONFLICT, FactCheckReport
from .profile import TERMS, StudentProfile, credit_hours
from .research import ResearchResult

_YEAR_WORDS = {"first": 1, "freshman": 1, "second": 2, "sophomore": 2, "third": 3, "junior": 3,
               "fourth": 4, "senior": 4}


@dataclass
class RequirementStatus:
    id: str
    section: str
    label: str
    options: list[str]
    hours: int
    status: str                       # done | in_progress | remaining
    satisfied_by: str | None = None
    conflict: bool = False


@dataclass
class PoolStatus:
    id: str
    section: str
    rule: str
    hours_required: int | None
    hours_done: int
    satisfied_by: list[str]
    options_left: list[str]
    status: str                       # done | remaining
    conflict: bool = False

    @property
    def hours_left(self) -> int:
        return max(0, (self.hours_required or 0) - self.hours_done)


@dataclass
class AuditResult:
    available: bool
    program: str | None = None
    catalog_year: str | None = None
    requirements: list[RequirementStatus] = field(default_factory=list)
    pools: list[PoolStatus] = field(default_factory=list)
    hours_completed: int = 0
    hours_in_progress: int = 0
    total_hours: int | None = None
    hours_remaining: int | None = None
    terms_remaining: int | None = None
    behind_plan: list[str] = field(default_factory=list)
    not_in_program: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> list[RequirementStatus]:
        return [r for r in self.requirements if r.status == "remaining"]

    def counts(self) -> dict:
        return {
            "done": sum(r.status == "done" for r in self.requirements),
            "in_progress": sum(r.status == "in_progress" for r in self.requirements),
            "remaining": len(self.remaining),
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pools"] = [{**asdict(p), "hours_left": p.hours_left} for p in self.pools]
        d["counts"] = self.counts()
        return d

    def to_text(self) -> str:
        if not self.available:
            return ("DEGREE AUDIT: unavailable — no verified degree requirements. "
                    f"Estimated hours completed: {self.hours_completed}.")
        c = self.counts()
        lines = [f"DEGREE AUDIT for {self.program} ({self.catalog_year or 'catalog year unknown'}), "
                 "computed from the fact-checked requirements:",
                 f"- Named requirements: {c['done']} done, {c['in_progress']} in progress, "
                 f"{c['remaining']} remaining"]
        hours = f"- Hours (estimate): {self.hours_completed} completed"
        if self.hours_in_progress:
            hours += f", {self.hours_in_progress} in progress"
        if self.total_hours:
            hours += f", of {self.total_hours} required; about {self.hours_remaining} to go"
        if self.terms_remaining:
            hours += f" (~{self.terms_remaining} more terms at the target load)"
        lines.append(hours)
        if self.remaining:
            lines.append("- Remaining requirements: " + "; ".join(
                f"{r.label}" + (" [sources disagree]" if r.conflict else "") for r in self.remaining))
        for p in self.pools:
            if p.status != "done":
                lines.append(f"- {p.section}: {p.rule} — {p.hours_done} of "
                             f"{p.hours_required or '?'} hours done")
        if self.behind_plan:
            lines.append("- Behind the catalog's suggested plan on: " + ", ".join(self.behind_plan))
        if self.not_in_program:
            lines.append("- Completed courses not matched to a listed requirement (may count as "
                         "core/general or free electives): " + ", ".join(self.not_in_program))
        lines += [f"- Note: {n}" for n in self.notes]
        return "\n".join(lines)


def _year_of(label: str) -> int | None:
    m = re.search(r"\d", label)
    if m:
        return int(m.group(0))
    return next((v for k, v in _YEAR_WORDS.items() if k in label.lower()), None)


def course_hours(code: str, research: ResearchResult, default: int | None = None) -> int:
    if code in research.courses and research.courses[code].hours:
        return research.courses[code].hours
    return default or credit_hours(code)


def audit(profile: StudentProfile, research: ResearchResult, report: FactCheckReport) -> AuditResult:
    with span("agent.auditor") as s:
        completed, in_progress = set(profile.completed), set(profile.in_progress)
        result = AuditResult(
            available=research.found,
            hours_completed=sum(course_hours(c, research) for c in completed),
            hours_in_progress=sum(course_hours(c, research) for c in in_progress),
        )
        if not research.found:
            result.notes.append("Degree requirements couldn't be verified online, so only "
                                "prerequisite-based planning is possible.")
            s.attributes["available"] = False
            return result

        prog = research.program
        result.program = prog.name if prog else f"{profile.major} ({profile.degree})"
        result.catalog_year = prog.catalog_year if prog else None
        used: set[str] = set()
        for r in research.requirements:
            done = next((o for o in r.options if o in completed), None)
            doing = next((o for o in r.options if o in in_progress), None)
            status = "done" if done else "in_progress" if doing else "remaining"
            if done or doing:
                used.add(done or doing)
            result.requirements.append(RequirementStatus(
                id=r.id, section=r.section, label=r.label(), options=r.options,
                hours=r.hours or course_hours(r.options[0], research), status=status,
                satisfied_by=done or doing, conflict=report.status(r.id) == CONFLICT))

        for p in research.pools:
            took = [o for o in p.options if o in (completed | in_progress) and o not in used]
            used.update(took)
            hours_done = sum(course_hours(c, research) for c in took)
            met = hours_done >= p.hours_required if p.hours_required is not None else bool(took)
            result.pools.append(PoolStatus(
                id=p.id, section=p.section, rule=p.rule, hours_required=p.hours_required,
                hours_done=hours_done, satisfied_by=took,
                options_left=[o for o in p.options if o not in completed | in_progress],
                status="done" if met else "remaining", conflict=report.status(p.id) == CONFLICT))

        result.not_in_program = sorted((completed | in_progress) - used)
        if prog and prog.total_hours:
            result.total_hours = prog.total_hours
            result.hours_remaining = max(0, prog.total_hours - result.hours_completed
                                         - result.hours_in_progress)
            result.terms_remaining = math.ceil(result.hours_remaining / max(profile.target_credits, 1))

        # Where the catalog's four-year plan expects the student to be by now.
        term_idx = TERMS.index(profile.semester) if profile.semester in TERMS else 0
        required = {o for r in research.requirements for o in r.options}
        for tp in research.sequence:
            y = _year_of(tp.year)
            t = TERMS.index(tp.term) if tp.term in TERMS else 0
            if y is None or (y, t) >= (profile.year_index, term_idx):
                continue
            for item in tp.items:
                if item in required and item not in completed | in_progress:
                    req = next(r for r in research.requirements if item in r.options)
                    if not any(o in completed | in_progress for o in req.options):
                        if item not in result.behind_plan:
                            result.behind_plan.append(item)
        s.attributes.update(remaining=len(result.remaining), behind=len(result.behind_plan))
    return result
