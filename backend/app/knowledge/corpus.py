"""
corpus.py
=========
In-memory view of the chunk corpus (data/chunks.jsonl) plus the course
registry used to recognise course mentions ("data structures", "3358",
"CS 2308") in questions.
"""

from __future__ import annotations

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


@dataclass
class EntityRegistry:
    course_titles: dict[str, str] = field(default_factory=dict)
    course_aliases: dict[str, str] = field(default_factory=dict)

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


def _build_registry() -> EntityRegistry:
    catalog = load_catalog()
    course_titles = {code: c.title for code, c in catalog.items()}
    course_aliases = {c.title.lower(): code for code, c in catalog.items()}
    course_aliases.update(COURSE_NICKNAMES)
    return EntityRegistry(course_titles=course_titles, course_aliases=course_aliases)


@lru_cache(maxsize=1)
def registry() -> EntityRegistry:
    return _build_registry()
