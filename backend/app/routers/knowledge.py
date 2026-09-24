"""
routers/knowledge.py
=====================
Structured, LLM-free endpoints over the knowledge layer. They back the
Professors / Courses views in the frontend and double as the tool surface
for the MCP server.

  GET /api/professors                     list with headline stats
  GET /api/professors/{name}              profile: stats, per-course stats
  GET /api/courses/{code}                 catalog entry, prereq tree, unlocks,
                                          professors who teach it (with stats)
  GET /api/compare?professors=A&professors=B&course=CS3358
  POST /api/plan  {"completed": ["CS1428", ...]}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..knowledge import catalog as cat
from ..knowledge.corpus import registry
from ..knowledge.stats import compute_stats, professors_for_course

router = APIRouter(prefix="/api", tags=["knowledge"])


def _resolve_professor(name: str) -> str:
    matches = registry().match_professors(name)
    if not matches:
        raise HTTPException(404, f"No reviews for professor '{name}'")
    return matches[0]


def _resolve_course(code: str) -> str:
    matches = registry().match_courses(code)
    if not matches:
        raise HTTPException(404, f"Unknown course '{code}'")
    return matches[0]


@router.get("/professors")
def list_professors():
    out = []
    for prof in registry().professors:
        s = compute_stats(prof)
        out.append({
            "name": prof,
            "review_count": s["review_count"],
            "avg_quality": s["avg_quality"],
            "avg_difficulty": s["avg_difficulty"],
            "top_courses": list(s["courses"])[:4],
        })
    return sorted(out, key=lambda p: -p["review_count"])


@router.get("/professors/{name}")
def professor_profile(name: str):
    prof = _resolve_professor(name)
    overall = compute_stats(prof)
    per_course = [compute_stats(prof, c) for c, n in overall["courses"].items()
                  if n >= 2 and c in registry().course_titles]
    return {
        "name": prof,
        "overall": overall,
        "courses": [{**s, "title": registry().course_titles.get(s["course"])} for s in per_course],
    }


@router.get("/courses")
def list_courses():
    return [{"code": c.code, "title": c.title, "has_prereqs": bool(c.prereqs)}
            for c in sorted(cat.load_catalog().values(), key=lambda c: c.code)]


@router.get("/courses/{code}")
def course_detail(code: str):
    course = cat.get_course(_resolve_course(code))
    if not course:
        raise HTTPException(404, f"{code} is not in the catalog")
    return {
        **course.to_dict(),
        "prereq_tree": cat.prereq_chain(course.code),
        "unlocks": cat.unlocks(course.code),
        "stats": compute_stats(None, course.code),
        "professors": [s for s in professors_for_course(course.code) if s["review_count"] >= 2],
    }


@router.get("/compare")
def compare(professors: list[str] = Query(..., min_length=2, max_length=5),
            course: str | None = None):
    code = _resolve_course(course) if course else None
    return {
        "course": code,
        "professors": [compute_stats(_resolve_professor(p), code) for p in professors],
    }


class PlanRequest(BaseModel):
    completed: list[str] = Field(..., min_length=1, max_length=40)


@router.post("/plan")
def plan(payload: PlanRequest):
    codes: set[str] = set()
    for c in payload.completed:
        codes.update(registry().match_courses(c))
    completed = cat.expand_completed(codes)
    return {"completed": sorted(completed), "eligible": cat.eligible_courses(completed)}
