"""
sources.py
==========
The knowledge-base source registry (sources.json): where each kind of
official TXST page lives, which links the crawler may follow, and how often
it must be refreshed.

Kinds (stored as chunk_type on every chunk):

  policy        undergraduate catalog academic rules (grading, withdrawal,
                repeats, course load, standing, graduation, transfer/AP)
  core          core curriculum component areas
  grad_catalog  graduate catalog pages relevant to undergraduates
  department    CS department pages (advising, research, internships, BS/MS)
  registrar     registration procedures and the academic calendar
  handbook      student handbook, honor code, academic integrity
  syllabus      public course syllabi (instructor details removed)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

KB_KINDS = ("policy", "core", "grad_catalog", "department", "registrar", "handbook", "syllabus")

# Human-readable names for citation labels.
KIND_NAMES = {
    "policy": "TXST Undergraduate Catalog",
    "core": "TXST Core Curriculum",
    "grad_catalog": "TXST Graduate Catalog",
    "department": "TXST Computer Science",
    "registrar": "TXST Registrar",
    "handbook": "TXST Student Handbook",
    "syllabus": "Course syllabus",
}


@dataclass
class SourceSpec:
    id: str
    kind: str
    name: str
    seeds: list[str]
    follow: list[str] = field(default_factory=list)
    max_pages: int = 20
    max_depth: int = 2
    refresh_days: int = 30
    catalog_year: bool = False
    scrub_people: bool = False
    only_courses_with_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KB_KINDS:
            raise ValueError(f"source {self.id}: unknown kind {self.kind!r}")


def load_sources(path: Path | None = None) -> list[SourceSpec]:
    path = path or Path(__file__).with_name("sources.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SourceSpec(**s) for s in data["sources"]]


@lru_cache(maxsize=1)
def sources_by_id() -> dict[str, SourceSpec]:
    return {s.id: s for s in load_sources()}
