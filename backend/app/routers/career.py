"""
routers/career.py
=================
GET  /api/career/paths   the career paths the app knows
POST /api/career         recommendations as JSON
POST /api/career/stream  Server-Sent Events: each agent's progress, every
                         search and link check, then the roadmap

Career requests share the chat rate limit: each can make a few searches
and open up to CAREER_MAX_PAGES pages.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..careers import pipeline
from ..careers.paths import careers
from ..schemas import CareerRequest
from ..services import sse
from ..tracing import start_trace
from .advise import _guard

router = APIRouter(prefix="/api/career", tags=["career"])


@router.get("/paths")
def paths() -> list[dict]:
    return [c.to_dict() for c in careers().values()]


@router.post("")
def career(payload: CareerRequest, request: Request) -> dict:
    _guard(request)
    with start_trace(kind="career") as trace:
        try:
            result = pipeline.recommend(payload.model_dump())
        except pipeline.CareerError as e:
            raise HTTPException(422, str(e)) from e
        trace.attributes["mode"] = result.get("mode") or ""
    return result


@router.post("/stream")
def career_stream(payload: CareerRequest, request: Request):
    _guard(request)
    raw = payload.model_dump()
    return sse.response(sse.relay(lambda: pipeline.run(raw), kind="career",
                                  public_errors=(pipeline.CareerError,)))
