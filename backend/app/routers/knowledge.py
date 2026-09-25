"""
routers/knowledge.py
=====================
Structured, LLM-free endpoints over the course catalog. They back the
Planner and Advisor views in the frontend and double as the tool surface
for the MCP server.

  GET  /api/courses                       every catalog course
  GET  /api/courses/{code}                catalog entry, prereq tree, unlocks
  POST /api/plan  {"completed": ["CS1428", ...]}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..knowledge import catalog as cat
from ..knowledge.corpus import registry

router = APIRouter(prefix="/api", tags=["knowledge"])


def _resolve_course(code: str) -> str:
    matches = registry().match_courses(code)
    if not matches:
        raise HTTPException(404, f"Unknown course '{code}'")
    return matches[0]


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
