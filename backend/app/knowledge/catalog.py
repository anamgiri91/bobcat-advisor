"""
catalog.py
==========
Parses the official course catalog into structured Course records and a
prerequisite graph.

Why structure it? Vector search can find "the CS 3360 catalog entry", but it
can't answer "I've passed CS 2308 and CS 2318 — what can I take next?" That
needs set logic over prerequisites, which is deterministic and must never be
hallucinated. The CatalogAgent and PlannerAgent call these functions as
tools; the LLM only explains the result.

Prerequisites are stored in conjunctive normal form (an AND of OR-groups):

  "CS 2308 and [CS 2318 or EE 3320] both with grades of "C" or better"
    -> [["CS2308"], ["CS2318", "EE3320"]]

A group that also admits a non-course alternative (ACT score, instructor
approval) is marked `conditional`: it is satisfiable without the course, so
the planner treats it as met but tells the student about the condition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import settings

COURSE_CODE = re.compile(r"\b([A-Z]{2,4})\s?(\d{4}[A-Z]?)\b")
_GRADE_CLAUSE = re.compile(
    r"\(?(?:all|both)?\s*with\s+(?:a\s+)?grades?\s+of\s+\"?[A-D]\"?\s+o[rf]\s+better\)?",
    re.IGNORECASE,
)


@dataclass
class PrereqGroup:
    """One AND-term: satisfied by ANY of `courses` (or by `condition`)."""
    courses: list[str]
    conditional: bool = False
    raw: str = ""


@dataclass
class Course:
    code: str                     # "CS3358"
    title: str                    # "Data Structures and Algorithms"
    description: str
    prereq_text: str = ""
    prereqs: list[PrereqGroup] = field(default_factory=list)
    other_requirements: list[str] = field(default_factory=list)

    @property
    def level(self) -> int:
        return int(self.code[2])

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "prereq_text": self.prereq_text,
            "prereqs": [
                {"any_of": g.courses, "conditional": g.conditional} for g in self.prereqs
            ],
            "other_requirements": self.other_requirements,
        }


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on ` and ` / ` or ` that are not inside [...] brackets."""
    parts, depth, buf, i = [], 0, [], 0
    token = f" {sep} "
    while i < len(text):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if depth == 0 and text.startswith(token, i):
            parts.append("".join(buf))
            buf = []
            i += len(token)
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_prereqs(text: str) -> tuple[list[PrereqGroup], list[str]]:
    """Parse a prerequisite sentence into CNF groups + non-course requirements."""
    cleaned = _GRADE_CLAUSE.sub("", text).strip().rstrip(".")
    groups: list[PrereqGroup] = []
    other: list[str] = []

    for term in _split_top_level(cleaned, "and"):
        codes = [f"{d}{n}" for d, n in COURSE_CODE.findall(term)]
        # Alternatives inside this term, e.g. "[A or B] or [ACT ...]"
        alternatives = _split_top_level(term.strip("[]"), "or")
        has_non_course_alt = any(not COURSE_CODE.search(a) for a in alternatives)
        if codes:
            groups.append(PrereqGroup(
                courses=list(dict.fromkeys(codes)),
                conditional=has_non_course_alt,
                raw=term,
            ))
        else:
            other.append(term.strip("[] "))
    return groups, other


def parse_catalog(path: Path) -> dict[str, Course]:
    text = path.read_text(encoding="utf-8-sig")
    courses: dict[str, Course] = {}
    for block in re.split(r"\n-{5,}\n", text):
        block = block.strip()
        header = re.match(r"^CS\s?(\d{4})\.\s*(.+?)\.\s*$", block.split("\n", 1)[0])
        if not header:
            continue
        code = f"CS{header.group(1)}"
        body = block.split("\n", 1)[1].strip() if "\n" in block else ""
        prereq_match = re.search(r"Prerequisites?:\s*(.+)$", body, re.MULTILINE)
        prereq_text = prereq_match.group(1).strip() if prereq_match else ""
        groups, other = parse_prereqs(prereq_text) if prereq_text else ([], [])
        description = body[: prereq_match.start()].strip() if prereq_match else body
        courses[code] = Course(code, header.group(2).strip(), description,
                               prereq_text, groups, other)
    return courses


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Course]:
    return parse_catalog(Path(settings.DOCUMENTS_DIR) / "official" / "coursecatalog.txt")


# ---------------------------------------------------------------------------
# Graph queries (these are the agent "tools")
# ---------------------------------------------------------------------------

def get_course(code: str) -> Course | None:
    return load_catalog().get(code)


def prereq_chain(code: str, _seen: set[str] | None = None) -> dict:
    """
    Full recursive prerequisite tree for a course, e.g. for CS3360:
      {code: CS3360, requires: [{any_of: [{code: CS2318, requires: [...]}, ...]}]}
    Non-CS courses (MATH, EE) are leaves: we only have the CS catalog.
    """
    seen = _seen or set()
    course = get_course(code)
    node: dict = {"code": code, "title": course.title if course else None, "requires": []}
    if not course or code in seen:
        return node
    seen = seen | {code}
    for group in course.prereqs:
        node["requires"].append({
            "any_of": [prereq_chain(c, seen) for c in group.courses],
            "conditional": group.conditional,
        })
    return node


@lru_cache(maxsize=1)
def _reverse_graph() -> dict[str, tuple[str, ...]]:
    """code -> courses that list it as a prerequisite (built once; the catalog is static)."""
    rev: dict[str, set[str]] = {}
    for c in load_catalog().values():
        for g in c.prereqs:
            for p in g.courses:
                rev.setdefault(p, set()).add(c.code)
    return {k: tuple(sorted(v)) for k, v in rev.items()}


def unlocks(code: str) -> list[str]:
    """Courses that list `code` anywhere in their prerequisites."""
    return list(_reverse_graph().get(code, ()))


@lru_cache(maxsize=1024)
def descendants(code: str) -> frozenset[str]:
    """Every course with `code` somewhere in its prerequisite chain."""
    seen: set[str] = set()
    frontier = [code]
    while frontier:
        for nxt in _reverse_graph().get(frontier.pop(), ()):
            if nxt not in seen and nxt != code:
                seen.add(nxt)
                frontier.append(nxt)
    return frozenset(seen)


def is_eligible(code: str, completed: set[str]) -> tuple[bool, list[list[str]], list[str]]:
    """
    (eligible, missing_groups, conditions) for a course given completed codes.
    Conditional groups count as met; their conditions are returned so the
    student is told about them.
    """
    course = get_course(code)
    if not course:
        return False, [], []
    missing, conditions = [], list(course.other_requirements)
    for g in course.prereqs:
        if any(c in completed for c in g.courses):
            continue
        if g.conditional:
            conditions.append(g.raw)
            continue
        # Only the CS catalog is modelled. Students rarely list gen-eds
        # (MATH/ENG/COMM), so an all-non-CS group is surfaced as something to
        # verify rather than treated as a blocker.
        if not any(c.startswith("CS") for c in g.courses):
            conditions.append(f"one of {', '.join(g.courses)}")
            continue
        missing.append(g.courses)
    return not missing, missing, conditions


def expand_completed(completed: set[str]) -> set[str]:
    """
    Infer courses a student must already have passed: passing CS2308 implies
    CS1428. Only single-option, non-conditional groups are inferred —
    "[CS2318 or EE3320]" doesn't say which one the student took.
    """
    out = set(completed)
    frontier = list(completed)
    while frontier:
        course = get_course(frontier.pop())
        if not course:
            continue
        for g in course.prereqs:
            if len(g.courses) == 1 and not g.conditional and g.courses[0] not in out:
                out.add(g.courses[0])
                frontier.append(g.courses[0])
    return out


def eligible_courses(completed: set[str]) -> list[dict]:
    """
    Every catalog course the student can take next: not yet completed, all
    course prerequisites met. Courses with no parsed CS prerequisites (co-op,
    research) are skipped — they're gated by GPA/approval, not coursework —
    as are intro courses once the student has passed one.
    """
    # Once a student has any 1000-level CS course, the other intro courses
    # (non-major literacy/survey courses) aren't useful next steps.
    past_intro = any(code.startswith("CS1") for code in completed)
    out = []
    for c in load_catalog().values():
        if c.code in completed:
            continue
        if not c.prereqs and (c.level > 1 or past_intro):
            continue
        ok, _, conditions = is_eligible(c.code, completed)
        if ok:
            out.append({"code": c.code, "title": c.title, "conditions": conditions,
                        "newly_unlocked": bool(c.prereqs)})
    return out
