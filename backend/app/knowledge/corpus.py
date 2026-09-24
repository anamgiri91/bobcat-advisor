"""
corpus.py
=========
In-memory view of the chunk corpus (data/chunks.jsonl) plus the entity
registry derived from it.

The registry replaces the hand-maintained PROF_CANONICAL dict: professors
and their aliases are discovered from the data, so adding a new review file
and re-running ingest is all it takes to support a new professor.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import settings
from .catalog import load_catalog


@lru_cache(maxsize=1)
def load_chunks() -> list[dict]:
    path = Path(settings.DATA_DIR) / "chunks.jsonl"
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def chunk_courses(meta: dict) -> list[str]:
    """All normalised course codes a chunk is about (see cleaner.normalise_course)."""
    courses = meta.get("courses")
    if courses:
        return courses.split("|")
    return [meta["course"]] if meta.get("course") else []


# Colloquial course names students actually use. Catalog titles are matched
# automatically; these cover the nicknames the catalog doesn't contain.
COURSE_NICKNAMES = {
    "data structures": "CS3358",
    "ds": "CS3358",
    "assembly": "CS2318",
    "architecture": "CS3339",
    "comp arch": "CS3339",
    "os": "CS4328",
    "operating systems": "CS4328",
    "networks": "CS4310",
    "networking": "CS4310",
    "software engineering": "CS3398",
    "soft eng": "CS3398",
    "oop": "CS3354",
    "object oriented": "CS3354",
    "ethics": "CS2315",
    "automata": "CS3378",
    "theory of computation": "CS3378",
    "compilers": "CS4318",
    "databases": "CS4332",
    "database": "CS4332",
    "machine learning": "CS4347",
    "computer vision": "CS4337",
    "parallel programming": "CS4380",
    "unix": "CS4350",
    "foundations 1": "CS1428",
    "foundations i": "CS1428",
    "cs1": "CS1428",
    "foundations 2": "CS2308",
    "foundations ii": "CS2308",
    "cs2": "CS2308",
    "intro to programming": "CS1428",
    "security": "CS4371",
    "graphics": "CS4388",
    "algorithms": "CS4355",
}

_STOP_TOKENS = {"the", "and", "for", "about", "with", "what", "professor", "prof", "doctor"}


@dataclass
class EntityRegistry:
    professors: list[str]
    # alias (lowercase) -> canonical name
    prof_aliases: dict[str, str] = field(default_factory=dict)
    # professor -> {course: review_count}
    prof_courses: dict[str, dict[str, int]] = field(default_factory=dict)
    course_titles: dict[str, str] = field(default_factory=dict)
    course_aliases: dict[str, str] = field(default_factory=dict)
    short_last_names: dict[str, str] = field(default_factory=dict)

    def match_professors(self, text: str) -> list[str]:
        """
        Canonical professors mentioned in `text`, in order of appearance.
        Exact alias matches first; then a fuzzy pass for typos
        ("Burtcher" -> Martin Burtscher) on longer tokens only, so short
        common words never fuzzy-match a name.
        """
        q = text.lower()
        found: dict[str, int] = {}
        for alias in sorted(self.prof_aliases, key=len, reverse=True):
            m = re.search(rf"\b{re.escape(alias)}(?:'s|s)?\b", q)
            if m and self.prof_aliases[alias] not in found:
                found[self.prof_aliases[alias]] = m.start()

        # Short last names ("Li") only count after a title ("Professor Li") or
        # when capitalised in the original text — never as a lowercase word.
        for last, prof in self.short_last_names.items():
            m = re.search(rf"\b(?:professor|prof\.?|dr\.?|doctor)\s+{re.escape(last)}\b", q) \
                or re.search(rf"\b{re.escape(last.capitalize())}\b", text)
            if m:
                found.setdefault(prof, m.start())

        single_aliases = [a for a in self.prof_aliases if " " not in a and len(a) >= 5]
        for tok_match in re.finditer(r"[a-z]{5,}", q):
            tok = tok_match.group(0)
            if tok in _STOP_TOKENS or tok in self.prof_aliases:
                continue
            close = difflib.get_close_matches(tok, single_aliases, n=1, cutoff=0.84)
            if close:
                name = self.prof_aliases[close[0]]
                found.setdefault(name, tok_match.start())
        return sorted(found, key=found.get)

    def match_courses(self, text: str) -> list[str]:
        q = text.lower()
        found: dict[str, int] = {}
        for m in re.finditer(r"\b(cs|math|ee|comm|eng|phil)\s?-?(\d{4})\b", q):
            found.setdefault(f"{m.group(1).upper()}{m.group(2)}", m.start())
        # Bare 4-digit numbers count only if they are a real catalog course
        # ("3358"), never years like "2024".
        for m in re.finditer(r"(?<![\w.])(\d{4})(?!\w|\.\d)", q):
            code = f"CS{m.group(1)}"
            if code in self.course_titles:
                found.setdefault(code, m.start())
        for alias in sorted(self.course_aliases, key=len, reverse=True):
            m = re.search(rf"\b{re.escape(alias)}\b", q)
            if m:
                found.setdefault(self.course_aliases[alias], m.start())
        return sorted(found, key=found.get)

    def is_known_professor(self, name: str) -> bool:
        return name in self.prof_courses


def _build_registry(chunks: list[dict]) -> EntityRegistry:
    prof_courses: dict[str, dict[str, int]] = {}
    for c in chunks:
        prof = c["metadata"].get("professor", "")
        if not prof or prof.lower() == "unknown":
            continue
        counts = prof_courses.setdefault(prof, {})
        for course in chunk_courses(c["metadata"]):
            counts[course] = counts.get(course, 0) + 1

    # Aliases: full name, last name, and first name when unambiguous.
    # Very short names ("Li", "Koh" is fine at 3) are only matched in full
    # to avoid "li" matching inside ordinary sentences.
    aliases: dict[str, str] = {}
    short_last: dict[str, str] = {}
    first_names: dict[str, list[str]] = {}
    for prof in prof_courses:
        parts = prof.lower().split()
        aliases[prof.lower()] = prof
        if len(parts) >= 2:
            last = parts[-1]
            if len(last) >= 3:
                aliases[last] = prof
            else:
                short_last[last] = prof
            first_names.setdefault(parts[0], []).append(prof)
    for first, profs in first_names.items():
        if len(profs) == 1 and len(first) >= 4:
            aliases.setdefault(first, profs[0])

    catalog = load_catalog()
    course_titles = {code: c.title for code, c in catalog.items()}
    course_aliases = {c.title.lower(): code for code, c in catalog.items()}
    course_aliases.update(COURSE_NICKNAMES)

    return EntityRegistry(
        professors=sorted(prof_courses),
        prof_aliases=aliases,
        prof_courses=prof_courses,
        course_titles=course_titles,
        course_aliases=course_aliases,
        short_last_names=short_last,
    )


@lru_cache(maxsize=1)
def registry() -> EntityRegistry:
    return _build_registry(load_chunks())
