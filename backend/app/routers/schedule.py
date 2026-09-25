"""
routers/schedule.py
===================
Structured, LLM-free endpoints over the class schedule store.

  GET  /api/schedule/terms                      terms with section data
  GET  /api/schedule/sections?course=&term=     sections (no instructor data is stored)
  GET  /api/offerings/{code}                    "usually offered in ..." with counts
  POST /api/timetable                           clash-free timetables (OR-Tools CP-SAT)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..advising.profile import normalise_codes
from ..structured import schedule as sched
from ..structured.offerings import compute_offerings
from ..structured.timetable import Preferences, build_timetable

router = APIRouter(prefix="/api", tags=["schedule"])


def _code(value: str) -> str:
    codes = normalise_codes(value)
    if not codes:
        raise HTTPException(422, f"Not a course code: {value!r}")
    return codes[0]


@router.get("/schedule/terms")
def schedule_terms():
    return {"terms": sched.terms()}


@router.get("/schedule/sections")
def schedule_sections(course: str, term: str = Query(..., max_length=20)):
    code = _code(course)
    return {"course": code, "term": term,
            "sections": [{**s.to_dict(), "id": s.id, "full": s.is_full,
                          "times": [m.label() for m in s.meetings]}
                         for s in sched.sections_for(code, term)]}


@router.get("/offerings/{course}")
def offering(course: str):
    code = _code(course)
    o = compute_offerings().get(code)
    if o is None:
        raise HTTPException(404, f"No schedule history for {code}")
    return o.to_dict()


class TimetableRequest(BaseModel):
    term: str = Field(..., max_length=20)
    courses: list[str] = Field(..., min_length=1, max_length=8)
    preferred_days: str = Field("", max_length=14)
    earliest_start: str | None = Field(None, max_length=8)
    latest_end: str | None = Field(None, max_length=8)
    busy: list[str] = Field(default_factory=list, max_length=10)
    modality: str | None = Field(None, max_length=10)
    include_full: bool = False
    options: int = Field(3, ge=1, le=5)


@router.post("/timetable")
def timetable(payload: TimetableRequest):
    if payload.term not in sched.terms():
        raise HTTPException(404, f"No section data loaded for {payload.term}")
    courses = list(dict.fromkeys(_code(c) for c in payload.courses))
    prefs = Preferences.from_raw(payload.model_dump())
    result = build_timetable(courses, payload.term, prefs, k=payload.options)
    return {**result.to_dict(), "preferences": prefs.describe()}
