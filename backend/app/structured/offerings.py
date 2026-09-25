"""
offerings.py
============
Which terms each course is usually offered in, worked out from every stored
schedule term: "offered in 4 of 4 Falls, 0 of 4 Springs".

It is history, not a promise, so it's always phrased as "usually offered"
with the counts behind it, and it only constrains a plan when the evidence
is clear: a season observed at least MIN_SEASON_OBSERVATIONS times with the
course never offered in it, while it was offered in another season.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .schedule import load_sections, store_path, term_sort_key

SEASONS = ("Fall", "Spring", "Summer")
MIN_SEASON_OBSERVATIONS = 2


@dataclass
class Offering:
    course: str
    offered: dict[str, int]         # season -> terms the course had sections
    observed: dict[str, int]        # season -> terms of schedule data we have
    terms: list[str]                # terms it was offered, oldest first

    @property
    def usual_seasons(self) -> list[str]:
        return [s for s in SEASONS if self.offered.get(s)]

    def not_usually_offered(self, season: str) -> bool:
        return (self.observed.get(season, 0) >= MIN_SEASON_OBSERVATIONS
                and self.offered.get(season, 0) == 0 and any(self.offered.values()))

    def label(self) -> str:
        parts = [f"{self.offered.get(s, 0)} of {self.observed[s]} {s}s"
                 for s in SEASONS if self.observed.get(s)]
        seasons = self.usual_seasons
        head = (f"usually offered in {' and '.join(seasons)}" if seasons else "not seen in any term")
        return f"{head} ({', '.join(parts)} with schedule data)"

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label(), "usual_seasons": self.usual_seasons}


def _season(term: str) -> str | None:
    m = re.match(r"(Fall|Spring|Summer)\b", term or "", re.IGNORECASE)
    return m.group(1).title() if m else None


_memo: dict = {"key": None, "value": {}}


def compute_offerings() -> dict[str, Offering]:
    """Per-course offering history; recomputed only when the schedule store changes."""
    path = store_path()
    key = (str(path), path.stat().st_mtime) if path.exists() else None
    if key == _memo["key"]:
        return _memo["value"]
    _memo["key"], _memo["value"] = key, _compute()
    return _memo["value"]


def _subject(course: str) -> str:
    return re.match(r"[A-Z]+", course).group(0) if re.match(r"[A-Z]+", course) else ""


def _compute() -> dict[str, Offering]:
    sections = [s for s in load_sections() if _season(s.term)]
    # A term counts as observed for a course only if that term's data covers
    # the course's subject (fetching CS for a term says nothing about MATH).
    subject_terms: dict[str, set[str]] = {}
    by_course: dict[str, set[str]] = {}
    for s in sections:
        subject_terms.setdefault(_subject(s.course), set()).add(s.term)
        by_course.setdefault(s.course, set()).add(s.term)
    out = {}
    for course, course_terms in by_course.items():
        seen = subject_terms[_subject(course)]
        observed = {season: sum(1 for t in seen if _season(t) == season) for season in SEASONS}
        offered = {season: sum(1 for t in course_terms if _season(t) == season) for season in SEASONS}
        out[course] = Offering(course, offered, observed, sorted(course_terms, key=term_sort_key))
    return out


def offering(course: str) -> Offering | None:
    return compute_offerings().get(course)
