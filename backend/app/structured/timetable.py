"""
timetable.py
============
Builds clash-free weekly timetables from the sections store.

Hard constraints (never violated):
  - exactly one section per course
  - no two chosen sections meet at the same time
  - nothing overlaps the student's busy blocks (work, commute)
  - nothing starts before `earliest_start` or ends after `latest_end`
  - optional: modality / campus filters; full sections excluded unless allowed

Soft preferences (minimised, in this order of weight):
  - class days outside the preferred days            (10 per day)
  - days on campus, to keep the week compact          (2 per day)

The solver is OR-Tools CP-SAT, imported only when a timetable is requested
(it adds ~85MB of memory). The exact fallback search (`TIMETABLE_SOLVER=search`,
or when OR-Tools isn't installed) enumerates combinations with conflict
pruning and returns the same optimal timetables for the few courses and
sections a student schedules; the two are cross-checked in tests.

Courses that can't be placed are reported with a reason ("no sections
listed for Spring 2027", "every section overlaps your busy times") and the
rest are still scheduled.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from ..config import settings
from ..tracing import span
from .schedule import DAY_ORDER, Meeting, Section, fmt_time, parse_days, parse_time, sections_for

W_NON_PREFERRED_DAY = 10
W_DAY_ON_CAMPUS = 2
MAX_SEARCH_NODES = 500_000


@dataclass
class Preferences:
    preferred_days: str = ""                 # e.g. "MWF"; empty = no preference
    earliest_start: int | None = None        # minutes after midnight
    latest_end: int | None = None
    busy: list[Meeting] = field(default_factory=list)
    modality: str | None = None              # "in person" | "online" | "hybrid"
    campus: str | None = None
    include_full: bool = False
    compact: bool = True

    @classmethod
    def from_raw(cls, raw: dict | None) -> Preferences:
        raw = raw or {}
        busy = []
        for b in raw.get("busy") or []:
            m = parse_block(b)
            if m:
                busy.append(m)
        days = raw.get("preferred_days") or ""
        return cls(
            preferred_days=parse_days(days if isinstance(days, str) else "".join(days)),
            earliest_start=parse_time(raw["earliest_start"]) if raw.get("earliest_start") else None,
            latest_end=parse_time(raw["latest_end"]) if raw.get("latest_end") else None,
            busy=busy,
            modality=(raw.get("modality") or None),
            campus=(raw.get("campus") or None),
            include_full=bool(raw.get("include_full", False)),
        )

    def describe(self) -> list[str]:
        out = []
        if self.preferred_days:
            out.append(f"prefers {self.preferred_days}")
        if self.earliest_start is not None:
            out.append(f"nothing before {fmt_time(self.earliest_start)}")
        if self.latest_end is not None:
            out.append(f"nothing after {fmt_time(self.latest_end)}")
        out += [f"busy {b.label()}" for b in self.busy]
        if self.modality:
            out.append(f"{self.modality} only")
        return out


def parse_block(text: str) -> Meeting | None:
    """'TR 12:00-17:00', 'MWF 1pm-5pm', 'Sat 9-13' -> Meeting."""
    m = re.match(r"\s*([A-Za-z ,/]+?)\s+(\d[\d:apmAPM. ]*)\s*[-–]\s*(\d[\d:apmAPM. ]*)\s*$", text or "")
    if not m:
        return None
    days = parse_days(m.group(1))
    start, end = _loose_time(m.group(2)), _loose_time(m.group(3))
    if not days or start is None or end is None or end <= start:
        return None
    return Meeting(days, start, end)


def _loose_time(t: str) -> int | None:
    t = t.strip()
    if re.fullmatch(r"\d{1,2}", t):          # "9" -> 9:00
        return int(t) * 60 if int(t) <= 23 else None
    return parse_time(t)


@dataclass
class TimetableOption:
    sections: list[Section]
    penalty: int

    @property
    def days(self) -> str:
        used = {d for s in self.sections for m in s.meetings for d in m.days}
        return "".join(d for d in DAY_ORDER if d in used)

    def to_dict(self) -> dict:
        return {"penalty": self.penalty, "days_on_campus": self.days,
                "sections": [{**s.to_dict(), "id": s.id, "full": s.is_full,
                              "times": [m.label() for m in s.meetings] or ["online / no set time"]}
                             for s in self.sections]}

    def to_text(self) -> str:
        return "; ".join(f"{s.id} ({', '.join(m.label() for m in s.meetings) or 'online, no set time'}"
                         f"{', ' + s.modality if s.modality else ''})" for s in self.sections)


@dataclass
class TimetableResult:
    term: str
    courses: list[str]
    options: list[TimetableOption] = field(default_factory=list)
    unplaced: list[dict] = field(default_factory=list)       # {course, reason}
    solver: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {"term": self.term, "courses": self.courses, "solver": self.solver, "note": self.note,
                "options": [o.to_dict() for o in self.options], "unplaced": self.unplaced}


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------

def _fits(s: Section, p: Preferences) -> str | None:
    """None if the section satisfies the hard constraints, else why not."""
    if s.is_full and not p.include_full:
        return "full"
    if p.modality and s.modality and s.modality != p.modality:
        return f"not {p.modality}"
    if p.campus and s.campus and p.campus.lower() not in s.campus.lower():
        return f"not at {p.campus}"
    for m in s.meetings:
        if p.earliest_start is not None and m.start < p.earliest_start:
            return f"starts before {fmt_time(p.earliest_start)}"
        if p.latest_end is not None and m.end > p.latest_end:
            return f"ends after {fmt_time(p.latest_end)}"
        if any(m.overlaps(b) for b in p.busy):
            return "overlaps your busy times"
    return None


_REASONS = {"full": "every section is full", "overlaps your busy times": "every section overlaps your busy times"}


def candidates(course: str, term: str, p: Preferences,
               pool: list[Section] | None = None) -> tuple[list[Section], str | None]:
    sections = [s for s in (pool if pool is not None else sections_for(course, term))
                if s.course == course and s.term == term]
    if not sections:
        return [], f"no sections listed for {term}"
    ok, why = [], []
    for s in sections:
        reason = _fits(s, p)
        (why.append(reason) if reason else ok.append(s))
    if ok:
        return ok, None
    reasons = sorted(set(why))
    if len(reasons) == 1:
        return [], _REASONS.get(reasons[0], f"every section {reasons[0]}")
    return [], "no section fits your constraints (" + ", ".join(reasons) + ")"


# ---------------------------------------------------------------------------
# Scoring and solvers
# ---------------------------------------------------------------------------

def penalty(chosen: list[Section], p: Preferences) -> int:
    days = {d for s in chosen for m in s.meetings for d in m.days}
    score = W_DAY_ON_CAMPUS * len(days) if p.compact else 0
    if p.preferred_days:
        score += W_NON_PREFERRED_DAY * len(days - set(p.preferred_days))
    return score


def _solve_search(groups: list[list[Section]], p: Preferences, k: int) -> tuple[list[TimetableOption], bool]:
    order = sorted(range(len(groups)), key=lambda i: len(groups[i]))
    found: list[TimetableOption] = []
    nodes = 0
    truncated = False

    def rec(i: int, chosen: list[Section]):
        nonlocal nodes, truncated
        if truncated:
            return
        nodes += 1
        if nodes > MAX_SEARCH_NODES:
            truncated = True
            return
        if i == len(order):
            found.append(TimetableOption(sorted(chosen, key=lambda s: s.course), penalty(chosen, p)))
            return
        for s in groups[order[i]]:
            if not any(s.conflicts(c) for c in chosen):
                rec(i + 1, chosen + [s])

    rec(0, [])
    found.sort(key=lambda o: (o.penalty, [s.id for s in o.sections]))
    return found[:k], truncated


def _solve_cpsat(groups: list[list[Section]], p: Preferences, k: int) -> list[TimetableOption]:
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    flat = [(gi, s) for gi, g in enumerate(groups) for s in g]
    x = [model.NewBoolVar(f"x{i}") for i in range(len(flat))]
    for gi in range(len(groups)):
        model.AddExactlyOne(x[i] for i, (g, _) in enumerate(flat) if g == gi)
    for (i, (gi, a)), (j, (gj, b)) in itertools.combinations(enumerate(flat), 2):
        if gi != gj and a.conflicts(b):
            model.AddBoolOr([x[i].Not(), x[j].Not()])
    day = {d: model.NewBoolVar(f"day_{d}") for d in DAY_ORDER}
    for i, (_, s) in enumerate(flat):
        for d in {d for m in s.meetings for d in m.days}:
            model.AddImplication(x[i], day[d])
    terms = []
    if p.compact:
        terms += [W_DAY_ON_CAMPUS * day[d] for d in DAY_ORDER]
    if p.preferred_days:
        terms += [W_NON_PREFERRED_DAY * day[d] for d in DAY_ORDER if d not in p.preferred_days]
    model.Minimize(sum(terms) if terms else 0)

    options: list[TimetableOption] = []
    for _ in range(k):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        solver.parameters.num_workers = 1          # deterministic results
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        chosen_idx = [i for i in range(len(flat)) if solver.Value(x[i])]
        chosen = sorted((flat[i][1] for i in chosen_idx), key=lambda s: s.course)
        options.append(TimetableOption(chosen, penalty(chosen, p)))
        model.AddBoolOr([x[i].Not() for i in chosen_idx])   # next: a different combination
    options.sort(key=lambda o: o.penalty)
    return options


def _solver_name() -> str:
    choice = settings.TIMETABLE_SOLVER
    if choice == "search":
        return "search"
    try:
        import ortools  # noqa: F401
        return "ortools"
    except ImportError:
        if choice == "ortools":
            raise
        return "search"


def build_timetable(courses: list[str], term: str, prefs: Preferences | None = None, k: int = 3,
                    pool: list[Section] | None = None) -> TimetableResult:
    """Up to k clash-free timetables for `courses` in `term`, best first."""
    p = prefs or Preferences()
    result = TimetableResult(term=term, courses=list(courses))
    with span("tool.timetable", term=term, courses=len(courses)) as sp:
        groups, placed = [], []
        for c in courses:
            cands, reason = candidates(c, term, p, pool)
            if cands:
                groups.append(cands)
                placed.append(c)
            else:
                result.unplaced.append({"course": c, "reason": reason})
        if not groups:
            result.note = "No course could be placed."
            return result

        result.solver = _solver_name()
        if result.solver == "ortools":
            options = _solve_cpsat(groups, p, k)
        else:
            options, truncated = _solve_search(groups, p, k)
            if truncated:
                result.note = "Search stopped early; the timetables shown may not be the best."
        if not options:
            # Explain which pair of courses can't coexist.
            for (ci, gi), (cj, gj) in itertools.combinations(zip(placed, groups, strict=True), 2):
                if all(a.conflicts(b) for a in gi for b in gj):
                    result.note = f"{ci} and {cj} can't both fit: every pair of their sections overlaps."
                    break
            else:
                result.note = "These courses can't all fit together with your constraints."
        result.options = options
        sp.attributes.update(solver=result.solver, options=len(options), unplaced=len(result.unplaced))
    return result
