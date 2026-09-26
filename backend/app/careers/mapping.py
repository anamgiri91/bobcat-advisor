"""
mapping.py
==========
Which TXST courses teach each skill a career needs, and what the catalog
doesn't cover. Deterministic: a course teaches a skill when one of the
skill's keywords appears (as a whole phrase) in its title or catalog
description, and the matched sentence is kept as evidence.

Coverage per skill:
  covered   a course is named for it (keyword in the title) or two or
            more distinct keywords appear across courses
  partial   a single passing mention in one description
  gap       nothing in the undergraduate CS catalog (outside resources fill it)

Excluded from matching: graduate courses (5000+), courses the catalog says
don't count for CS credit, and placement courses (co-op, internship,
research, independent study) that name no topic. (CS1309 "AI for
Everyone", for example, says it "will not satisfy CS major or minor
requirements", so it isn't recommended for an ML career.) The last are suggested
separately as ways to get experience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..knowledge import catalog as cat
from .paths import Career, skills

EXPERIENCE = {
    "CS4100": "Internship credit for a job in the field",
    "CS4298": "Research with a faculty mentor in the area",
    "CS3190": "Co-op credit for a work placement",
}
_PLACEMENT = {"CS3190", "CS3290", "CS4100", "CS4298", "CS4299", "CS4395"}
# Catalog wording for courses that don't count toward a CS degree.
_NO_CREDIT = ("does not count for computer science credit", "will not satisfy cs major")


@dataclass
class Match:
    code: str
    title: str
    keyword: str
    in_title: bool
    evidence: str                  # the catalog sentence the keyword is in


@dataclass
class SkillCoverage:
    id: str
    name: str
    importance: str                # core | helpful
    coverage: str                  # covered | partial | gap
    matches: list[Match] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "importance": self.importance,
                "coverage": self.coverage, "courses": sorted({m.code for m in self.matches}),
                "note": self.note}


@lru_cache(maxsize=1)
def _eligible_catalog() -> tuple[cat.Course, ...]:
    return tuple(c for c in cat.load_catalog().values()
                 if c.level <= 4 and c.code not in _PLACEMENT and not any(p in c.description.lower() for p in _NO_CREDIT))


@lru_cache(maxsize=512)
def _pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", re.IGNORECASE)


def _sentence(text: str, start: int) -> str:
    left = text.rfind(". ", 0, start)
    left = 0 if left < 0 else left + 2
    right = text.find(". ", start)
    return text[left: right + 1 if right >= 0 else len(text)].strip()


@lru_cache(maxsize=64)
def matches_for(skill_id: str) -> tuple[Match, ...]:
    """Courses whose title or description names the skill (one Match per course)."""
    skill = skills()[skill_id]
    found: list[Match] = []
    for course in _eligible_catalog():
        for kw in skill.keywords:
            pat = _pattern(kw)
            if pat.search(course.title):
                found.append(Match(course.code, course.title, kw, True, course.title))
                break
            m = pat.search(course.description)
            if m:
                found.append(Match(course.code, course.title, kw, False,
                                   _sentence(course.description, m.start())))
                break
    return tuple(found)


def coverage(career: Career) -> list[SkillCoverage]:
    out = []
    for skill_id in (*career.core, *career.helpful):
        skill = skills()[skill_id]
        ms = list(matches_for(skill_id))
        distinct_keywords = {m.keyword for m in ms}
        if any(m.in_title for m in ms) or len(distinct_keywords) >= 2:
            level = "covered"
        elif ms:
            level = "partial"
        else:
            level = "gap"
        note = skill.outside_cs if level == "gap" and skill.outside_cs else ""
        out.append(SkillCoverage(skill_id, skill.name, career.importance(skill_id) or "helpful",
                                 level, ms, note))
    return out


def _status(code: str, done: set[str], in_progress: set[str]) -> tuple[str, list[list[str]]]:
    if code in done:
        return "done", []
    if code in in_progress:
        return "in_progress", []
    ok, missing, _conditions = cat.is_eligible(code, done | in_progress)
    return ("eligible" if ok else "later"), missing


def recommend_courses(career: Career, cov: list[SkillCoverage], completed: set[str],
                      in_progress: set[str], limit: int = 10) -> list[dict]:
    """
    TXST courses ranked by how much of the career they teach (core skills
    count double, a course named for the skill gets a bonus), with each
    one's status for this student.

    Gateways: a prerequisite that's the only way into recommended courses
    (e.g. CS3358 before most 4000-level courses) is added even if it teaches
    none of the career's skills itself, and ranked by how much it unlocks.
    """
    done = cat.expand_completed(completed)
    by_code: dict[str, dict] = {}
    for sc in cov:
        weight = 2 if sc.importance == "core" else 1
        for m in sc.matches:
            entry = by_code.setdefault(m.code, {"code": m.code, "title": m.title, "skills": [],
                                                "evidence": [], "score": 0, "unlocks": []})
            entry["skills"].append(sc.name)
            entry["score"] += weight + (1 if m.in_title else 0)
            if not m.in_title and m.evidence not in entry["evidence"]:
                entry["evidence"].append(m.evidence)

    gates: dict[str, list[str]] = {}
    for code, entry in by_code.items():
        entry["status"], missing = _status(code, done, in_progress)
        entry["missing"] = [" or ".join(g) for g in missing]
        for group in missing:
            if len(group) == 1:
                gates.setdefault(group[0], []).append(code)
    for code, unlocked in gates.items():
        course = cat.get_course(code)
        if course is None:
            continue
        entry = by_code.get(code)
        if entry is None:
            status, missing = _status(code, done, in_progress)
            if status != "eligible" and len(unlocked) < 2:
                continue            # a distant prerequisite for one course: the course says so already
            entry = by_code[code] = {"code": code, "title": course.title, "skills": [], "evidence": [],
                                     "score": 0, "status": status,
                                     "missing": [" or ".join(g) for g in missing]}
        entry["unlocks"] = sorted(unlocked)

    order = {"eligible": 0, "in_progress": 1, "later": 2, "done": 3}
    ranked = sorted(by_code.values(),
                    key=lambda e: (order[e["status"]], -(e["score"] + 2 * len(e["unlocks"])), e["code"]))
    todo = [e for e in ranked if e["status"] != "done"][:limit]
    return todo + [e for e in ranked if e["status"] == "done"]


def experience(completed: set[str]) -> list[dict]:
    done = cat.expand_completed(completed)
    out = []
    for code, why in EXPERIENCE.items():
        course = cat.get_course(code)
        if course and code not in done:
            out.append({"code": code, "title": course.title, "why": why})
    return out


# ---------------------------------------------------------------------------
# Topic questions in the chat ("How do I learn AI?")
# ---------------------------------------------------------------------------

def topic_courses(skill_ids: list[str], limit: int = 4) -> list[str]:
    """TXST courses for the topic: the main skill's courses first; within a
    skill, courses named for it before passing mentions, lower level first."""
    ranked: list[tuple[int, int, int, str]] = []
    for rank, sid in enumerate(skill_ids):
        for m in matches_for(sid):
            ranked.append((rank, 0 if m.in_title else 1, cat.get_course(m.code).level, m.code))
    out: list[str] = []
    for *_, code in sorted(ranked):
        if code not in out:
            out.append(code)
    return out[:limit]


def _path(codes: list[str]) -> list[str]:
    """Prerequisites to reach these courses, lowest level first (the first
    listed option of each "one of" group; non-CS courses kept as named)."""
    seen: set[str] = set()

    def walk(code: str) -> None:
        course = cat.get_course(code)
        for g in course.prereqs if course else []:
            if g.conditional or not g.courses:
                continue
            first = g.courses[0]
            if first not in seen:
                seen.add(first)
                walk(first)
    for c in codes:
        walk(c)
    return sorted(seen - set(codes), key=lambda c: (re.search(r"\d", c).group(0), c))


def _outside_matches(skill_ids: list[str]) -> tuple[list[str], list[str]]:
    """Catalog courses on the topic that aren't recommended: graduate level,
    and courses that don't count toward a CS degree."""
    eligible = {c.code for c in _eligible_catalog()}
    grad, no_credit = [], []
    for course in cat.load_catalog().values():
        if course.code in eligible or course.code in _PLACEMENT:
            continue
        text = f"{course.title} {course.description}"
        if any(_pattern(kw).search(text) for sid in skill_ids for kw in skills()[sid].keywords):
            (grad if course.level >= 5 else no_credit).append(f"{course.code} {course.title}")
    return grad, no_credit


def topic_summary(skill_ids: list[str], codes: list[str]) -> str:
    """The computed answer to "how do I learn X at TXST?", as evidence text."""
    names = ", ".join(skills()[s].name.lower() for s in skill_ids[:3])
    lines = [f"TXST COURSES FOR {names.upper()} (computed from the catalog):"]
    for code in codes:
        course = cat.get_course(code)
        prereqs = [" or ".join(g.courses) for g in course.prereqs if not g.conditional]
        lines.append(f"- {code} {course.title}" + (f" — needs {', '.join(prereqs)}" if prereqs else ""))
    path = _path(codes)
    if path:
        lines.append("- Path to get there: " + " → ".join(path) + " → then the courses above.")
    grad, no_credit = _outside_matches(skill_ids)
    if grad:
        lines.append("- Graduate level (5000+): " + "; ".join(grad) + ".")
    if no_credit:
        lines.append("- Doesn't count toward a CS degree: " + "; ".join(no_credit) + ".")
    gaps = [skills()[s].name for s in skill_ids if not matches_for(s)]
    if gaps:
        lines.append("- Not taught in the undergraduate CS catalog: " + ", ".join(gaps) + ".")
    lines.append("- Outside class: the Careers tab lists checked online courses and certifications "
                 "for this area.")
    return "\n".join(lines)
