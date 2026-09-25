"""
scheduler.py
============
The schedule-planning agent: picks next term's courses and sketches a
roadmap to graduation. Deterministic; the advisor LLM explains the result
but never chooses courses.

It plays three advisor sub-roles:

  eligibility   a course is only recommended if its prerequisites are met in
                BOTH the bundled catalog snapshot and the live catalog page
                (when they disagree, the stricter reading wins)
  prioritiser   score = required for the degree + how many remaining courses
                it unlocks (critical path) + where the catalog's four-year
                plan puts it + match with the student's interests - a
                penalty for courses well above their year
  workload      fills up to the target credit load, capping the number of
                upper-division (3000/4000-level) courses per term, and fewer
                for a student whose GPA suggests a lighter load

Without verified degree requirements (catalog unreachable), it falls back
to the prerequisite graph alone and says so.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..knowledge import catalog as cat
from ..structured.offerings import compute_offerings
from ..structured.schedule import load_sections
from ..tracing import span
from .audit import AuditResult, _year_of, course_hours
from .factcheck import CONFLICT, FactCheckReport
from .profile import TERMS, StudentProfile, course_level
from .research import ResearchResult

# Upper-division (3000/4000-level) courses allowed per term. A course-level
# proxy for workload: the app keeps no ratings or reviews.
MAX_UPPER_DIVISION = 3
MAX_UPPER_DIVISION_LOW_GPA = 2
MAX_ROADMAP_TERMS = 8

INTEREST_KEYWORDS: dict[str, list[str]] = {
    "ai": ["artificial intelligence", "machine learning", "neural", "intelligent", "learning",
           "vision", "natural language", "data mining"],
    "machine learning": ["machine learning", "learning", "neural", "data mining", "artificial"],
    "data": ["data", "database", "mining", "analytics", "statistic"],
    "security": ["security", "cryptograph", "secure", "forensic", "privacy", "network"],
    "web": ["web", "internet", "network", "cloud", "distributed"],
    "games": ["game", "graphics", "animation", "visual"],
    "graphics": ["graphics", "visual", "image", "game"],
    "systems": ["operating system", "parallel", "distributed", "architecture", "compiler",
                "embedded", "network"],
    "software": ["software", "engineering", "testing", "design", "project"],
    "theory": ["theory", "automata", "algorithm", "computation", "formal", "discrete"],
    "mobile": ["mobile", "android", "app", "wireless"],
    "hardware": ["digital", "hardware", "embedded", "architecture", "assembly", "logic"],
    "research": ["research", "thesis", "honors", "independent"],
}
_INTEREST_ALIASES = {"artificial intelligence": "ai", "ml": "machine learning",
                     "cybersecurity": "security", "cyber": "security", "data science": "data",
                     "databases": "data", "game": "games", "game development": "games",
                     "operating systems": "systems", "software engineering": "software",
                     "web development": "web", "networking": "web"}


def interest_terms(profile: StudentProfile) -> dict[str, list[str]]:
    """{interest as the student wrote it: keywords to look for in course text}."""
    out: dict[str, list[str]] = {}
    sources = list(profile.interests)
    if profile.career_goal:
        sources.append(profile.career_goal)
    for raw in sources:
        key = raw.lower().strip()
        key = _INTEREST_ALIASES.get(key, key)
        terms = INTEREST_KEYWORDS.get(key)
        if terms is None:
            # Unknown interest or a free-text goal: match known topics inside
            # it ("data scientist" -> data), else the words themselves.
            matched = [t for k, ts in INTEREST_KEYWORDS.items()
                       if re.search(rf"\b{re.escape(k)}", key) for t in ts]
            terms = matched or [w for w in re.findall(r"[a-z]{4,}", key)]
        if terms:
            out[raw] = terms
    return out


@dataclass
class Recommendation:
    code: str
    title: str
    hours: int
    kind: str                         # required | elective | eligible
    requirement: str
    group: str = ""                   # requirement/pool id it counts toward
    offering: str = ""                # "usually offered in Fall (4 of 4 Falls, ...)" when known
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    conditions: list[str] = field(default_factory=list)
    conflict: bool = False


@dataclass
class SchedulePlan:
    term: str
    mode: str                         # degree | prerequisites_only
    target_credits: int
    courses: list[Recommendation] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)
    roadmap: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_hours(self) -> int:
        return sum(c.hours for c in self.courses)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_hours"] = self.total_hours
        return d

    def to_text(self) -> str:
        head = (f"RECOMMENDED {self.term.upper()} SCHEDULE ({self.total_hours} of "
                f"{self.target_credits} target hours), computed by the schedule planner"
                + (" from the prerequisite graph only (degree requirements unverified)"
                   if self.mode == "prerequisites_only" else "") + ":")
        lines = [head]
        for c in self.courses:
            line = f"- {c.code} {c.title} ({c.hours} hrs, {c.kind}: {c.requirement})"
            line += " | why: " + "; ".join(c.reasons)
            if c.conditions:
                line += " | check: " + "; ".join(c.conditions)
            lines.append(line)
        for d in self.deferred[:8]:
            lines.append(f"- Not this term: {d['code']} — {d['reason']}")
        lines += [f"- Warning: {w}" for w in self.warnings]
        return "\n".join(lines)

    def roadmap_text(self) -> str:
        if not self.roadmap:
            return "ROADMAP: none computed."
        lines = ["ROADMAP to finish the listed requirements (tentative; assumes each course is "
                 "passed and offered when planned):"]
        for t in self.roadmap:
            lines.append(f"- {t['term']}: {', '.join(t['courses'])} ({t['hours']} hrs)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prerequisites across both sources
# ---------------------------------------------------------------------------

def _eval_groups(groups: list[cat.PrereqGroup], done: set[str], tracks_non_cs: bool
                 ) -> tuple[list[list[str]], list[str]]:
    missing, conditions = [], []
    for g in groups:
        if any(c in done for c in g.courses):
            continue
        if g.conditional:
            conditions.append(g.raw.strip("[] ") or " or ".join(g.courses))
        elif not any(c.startswith("CS") for c in g.courses) and not tracks_non_cs:
            # The student listed no non-CS courses, so we can't tell; ask them to check.
            conditions.append(f"one of {', '.join(g.courses)}")
        else:
            missing.append(g.courses)
    return missing, conditions


def prereq_status(code: str, done: set[str], research: ResearchResult
                  ) -> tuple[bool, list[list[str]], list[str]]:
    """(eligible, missing groups, conditions) using the snapshot AND the live catalog."""
    expanded = cat.expand_completed(done)
    tracks_non_cs = any(not c.startswith("CS") for c in done)
    missing: list[list[str]] = []
    conditions: list[str] = []
    local = cat.get_course(code)
    if local:
        m, c = _eval_groups(local.prereqs, expanded, tracks_non_cs)
        missing += m
        conditions += c + list(local.other_requirements)
    web = research.courses.get(code)
    if web and web.prereq_text:
        groups, other = cat.parse_prereqs(web.prereq_text)
        m, c = _eval_groups(groups, expanded, tracks_non_cs)
        missing += [g for g in m if g not in missing]
        conditions += [x for x in c + other if x not in conditions]
    elif web is None and not local and not code.startswith("CS"):
        conditions.append("prerequisites not checked (course page not read)")
    return not missing, missing, conditions


def _unlock_count(code: str, targets: set[str]) -> int:
    """How many target courses have `code` somewhere in their prerequisite chain."""
    count, frontier, seen = 0, [code], {code}
    while frontier:
        for nxt in cat.unlocks(frontier.pop()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
                count += nxt in targets
    return count


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    code: str
    kind: str
    requirement: str
    group: str                        # requirement/pool id; one pick per requirement
    conflict: bool = False


class _Planner:
    def __init__(self, profile: StudentProfile, research: ResearchResult, audit: AuditResult,
                 report: FactCheckReport):
        self.profile, self.research, self.audit, self.report = profile, research, audit, report
        self.interests = interest_terms(profile)
        # Offering history and the planned term's sections, when schedule data is loaded.
        self.offerings = compute_offerings()
        planned = profile.planned_term
        term_sections = [s for s in load_sections() if s.term == planned]
        self.planned_term = planned
        self.term_courses = {s.course for s in term_sections} if term_sections else None
        self.seq_pos: dict[str, tuple[int, int]] = {}
        for tp in research.sequence:
            y = _year_of(tp.year)
            if y is None:
                continue
            for item in tp.items:
                self.seq_pos.setdefault(item, (y, TERMS.index(tp.term) if tp.term in TERMS else 0))

    def title(self, code: str) -> str:
        if code in self.research.courses:
            return self.research.courses[code].title
        for r in self.research.requirements:
            if code in r.titles:
                return r.titles[code]
        local = cat.get_course(code)
        return local.title if local else code

    def course_text(self, code: str) -> str:
        local = cat.get_course(code)
        web = self.research.courses.get(code)
        return " ".join(x for x in (self.title(code), local.description if local else "",
                                    web.description if web else "") if x).lower()

    def candidates(self, done: set[str], pools_left: dict[str, int]) -> list[_Candidate]:
        out: list[_Candidate] = []
        if self.audit.available:
            for r in self.research.requirements:
                if any(o in done for o in r.options):
                    continue
                for o in r.options:
                    out.append(_Candidate(o, "required", f"{r.section}: {r.label()}", r.id,
                                          self.report.status(r.id) == CONFLICT))
            for p in self.research.pools:
                if pools_left.get(p.id, 0) <= 0:
                    continue
                for o in p.options:
                    if o not in done:
                        out.append(_Candidate(o, "elective", f"{p.section}: {p.rule}", p.id,
                                              self.report.status(p.id) == CONFLICT))
        else:
            for e in cat.eligible_courses(cat.expand_completed(done)):
                out.append(_Candidate(e["code"], "eligible", "prerequisites met", e["code"]))
        return out

    def score(self, cand: _Candidate, targets: set[str], term: tuple[int, int]
              ) -> tuple[float, list[str]]:
        score, reasons = 0.0, []
        if cand.kind == "required":
            score += 10
            reasons.append("required for your degree")
        elif cand.kind == "elective":
            score += 4
            reasons.append("counts toward a remaining elective requirement")
        unlocks = _unlock_count(cand.code, targets)
        if unlocks:
            score += min(2 * unlocks, 8)
            reasons.append(f"prerequisite for {unlocks} course{'s' if unlocks > 1 else ''} you still need")
        pos = self.seq_pos.get(cand.code)
        if pos:
            if pos < term:
                score += 6
                reasons.append("the catalog's four-year plan schedules it earlier; you're behind on it")
            elif pos == term:
                score += 4
                reasons.append("the catalog's four-year plan schedules it for this term")
        text = self.course_text(cand.code)
        # Word-start matching: "vision" must not match "Upper Division".
        hits = [name for name, terms in self.interests.items()
                if any(re.search(rf"\b{re.escape(t)}", text) for t in terms)]
        if hits:
            score += min(4 * len(hits), 8)
            reasons.append(f"matches your interest in {', '.join(hits)}")
        level_gap = course_level(cand.code) - (term[0] + 1)
        if level_gap > 0:
            score -= 4 * level_gap
        return score, reasons

    def plan_term(self, done: set[str], pools_left: dict[str, int], term: tuple[int, int],
                  detailed: bool, season: str | None = None) -> tuple[list[Recommendation], list[dict]]:
        season = season or TERMS[term[1]]
        cands = self.candidates(done, pools_left)
        targets = {c.code for c in cands if c.kind == "required"} or {c.code for c in cands}
        scored, deferred = [], []
        seen: set[str] = set()
        for cand in cands:
            if cand.code in seen:
                continue
            seen.add(cand.code)
            off = self.offerings.get(cand.code)
            if off and off.not_usually_offered(season):
                deferred.append({"code": cand.code, "reason": f"not usually offered in {season}: "
                                 f"{off.label()}"})
                continue
            if detailed and self.term_courses is not None and cand.code not in self.term_courses:
                deferred.append({"code": cand.code,
                                 "reason": f"no sections listed for {self.planned_term}"})
                continue
            ok, missing, conditions = prereq_status(cand.code, done, self.research)
            if not ok:
                deferred.append({"code": cand.code, "reason": "needs " + "; ".join(
                    " or ".join(g) for g in missing)})
                continue
            score, reasons = self.score(cand, targets, term)
            scored.append((score, cand, reasons, conditions))
        scored.sort(key=lambda x: (-x[0], course_level(x[1].code), x[1].code))

        low_gpa = self.profile.gpa is not None and self.profile.gpa < 2.5
        max_upper = MAX_UPPER_DIVISION_LOW_GPA if low_gpa else MAX_UPPER_DIVISION
        picks: list[Recommendation] = []
        hours, upper, groups = 0, 0, set()
        pool_hours = dict(pools_left)
        target = self.profile.target_credits
        for score, cand, reasons, conditions in scored:
            if cand.group in groups:
                continue
            h = course_hours(cand.code, self.research)
            if hours + h > target + (1 if hours <= target - 3 else 0):
                continue
            if cand.kind == "elective" and pool_hours.get(cand.group, 0) <= 0:
                continue
            if course_level(cand.code) >= 3:
                if upper >= max_upper:
                    deferred.append({"code": cand.code, "reason": f"balancing workload: already "
                                     f"{upper} upper-division courses this term"})
                    continue
                upper += 1
            off = self.offerings.get(cand.code)
            rec = Recommendation(code=cand.code, title=self.title(cand.code), hours=h,
                                 kind=cand.kind, requirement=cand.requirement, group=cand.group,
                                 reasons=reasons, offering=off.label() if off else "",
                                 score=score, conditions=conditions, conflict=cand.conflict)
            if detailed:
                if cand.conflict:
                    rec.conditions.append("live catalog and snapshot disagree on this requirement")
            picks.append(rec)
            hours += h
            groups.add(cand.group)
            if cand.kind == "elective":
                pool_hours[cand.group] = pool_hours.get(cand.group, 0) - h
            if hours >= target:
                break
        return picks, deferred


def _next_term(year: int, term: int) -> tuple[int, int]:
    """Fall -> Spring of the same academic year -> next Fall (summers skipped)."""
    return (year, 1) if term == 0 else (year + 1, 0)


def _next_label(term: str) -> str:
    """'Fall 2026' -> 'Spring 2027'; 'Spring 2027' or 'Summer 2027' -> 'Fall 2027' (summers skipped)."""
    season, year = term.split()
    return f"Spring {int(year) + 1}" if season == "Fall" else f"Fall {year}"


def plan_schedule(profile: StudentProfile, research: ResearchResult, audit: AuditResult,
                  report: FactCheckReport) -> SchedulePlan:
    with span("agent.scheduler") as s:
        planner = _Planner(profile, research, audit, report)
        mode = "degree" if audit.available else "prerequisites_only"
        plan = SchedulePlan(term=profile.planned_term, mode=mode, target_credits=profile.target_credits)
        done = set(profile.planning_completed)
        # A pool without an hour count ("Select one of the following") needs one course.
        pools_left = {p.id: p.hours_left if p.hours_required else (0 if p.satisfied_by else 1)
                      for p in audit.pools}
        term = (profile.year_index, TERMS.index(profile.semester) if profile.semester != "Summer" else 1)

        plan.courses, plan.deferred = planner.plan_term(done, pools_left, term, detailed=True,
                                                        season=profile.semester)
        if not plan.courses:
            plan.warnings.append("No eligible courses were found for this term; check prerequisites "
                                 "with your advisor.")
        elif plan.total_hours < min(12, profile.target_credits):
            plan.warnings.append(f"Only {plan.total_hours} hours of degree courses are available "
                                 "this term; fill the rest with core curriculum or minor courses.")
        if mode == "prerequisites_only":
            plan.warnings.append("Degree requirements couldn't be verified from the live catalog; "
                                 "these are CS courses you're eligible for, not a degree audit.")
        if not profile.completed and not profile.in_progress:
            plan.warnings.append("No completed courses were listed, so this assumes you're starting "
                                 "from scratch.")

        # Roadmap: repeat the planner on future terms, assuming each pick is passed.
        if mode == "degree":
            done_sim, left = set(done), dict(pools_left)
            t, label = term, profile.planned_term
            for i in range(MAX_ROADMAP_TERMS):
                picks = (plan.courses if i == 0 else
                         planner.plan_term(done_sim, left, t, False, season=label.split()[0])[0])
                if not picks:
                    break
                plan.roadmap.append({"term": label, "courses": [p.code for p in picks],
                                     "hours": sum(p.hours for p in picks)})
                for p in picks:
                    done_sim.add(p.code)
                    if p.kind == "elective":
                        left[p.group] = left.get(p.group, 0) - p.hours
                t = _next_term(*t)
                label = _next_label(label)
            still = [r.label() for r in research.requirements
                     if not any(o in done_sim for o in r.options)]
            if still:
                plan.warnings.append("The roadmap couldn't place " + ", ".join(still[:6])
                                     + ": they need prerequisites that aren't in the listed "
                                     "requirements (such as core curriculum courses).")
        s.attributes.update(mode=mode, courses=len(plan.courses), hours=plan.total_hours,
                            roadmap_terms=len(plan.roadmap))
    return plan
