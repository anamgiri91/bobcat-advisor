"""
routers/advise.py
=================
POST /api/advise         — course recommendations as JSON
POST /api/advise/stream  — Server-Sent Events: each agent's progress, every
                           page the researcher visits, the fact-check report,
                           audit, schedule, then the streamed advising memo
POST /api/advise/whatif  — graduation date under up to 4 scenarios (switch
                           major, add a minor, fail a course, change load)

Both run the seven-agent pipeline in app/advising/pipeline.py. Advising
requests share the chat rate limit: each one can fetch up to WEB_MAX_PAGES
pages and make a few LLM calls.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..advising import pipeline
from ..advising.whatif import what_if
from ..schemas import AdviseRequest, WhatIfRequest
from ..services import sse
from ..services.protection import client_key, rate_limiter
from ..tracing import start_trace

router = APIRouter(prefix="/api/advise", tags=["advise"])


def _guard(request: Request) -> None:
    allowed, retry_after = rate_limiter.allow(client_key(request))
    if not allowed:
        raise HTTPException(429, "Too many requests — please wait a moment.",
                            headers={"Retry-After": str(int(retry_after) + 1)})


@router.post("")
def advise(payload: AdviseRequest, request: Request) -> dict:
    _guard(request)
    with start_trace(kind="advise") as trace:
        result = pipeline.advise(payload.model_dump())
        trace.attributes["mode"] = result.get("mode") or ""
    return result


@router.post("/whatif")
def whatif(payload: WhatIfRequest, request: Request) -> dict:
    _guard(request)
    with start_trace(kind="whatif") as trace:
        result = what_if(payload.profile.model_dump(),
                         [s.model_dump(exclude_none=True) for s in payload.scenarios])
        result["trace"] = {"total_ms": round(trace.elapsed_ms), "spans": len(trace.spans)}
    return result


@router.post("/stream")
def advise_stream(payload: AdviseRequest, request: Request):
    _guard(request)
    raw = payload.model_dump()
    return sse.response(sse.relay(lambda: pipeline.run(raw), kind="advise"))
