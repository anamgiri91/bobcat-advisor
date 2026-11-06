"""
routers/advise.py
=================
POST /api/advise         — course recommendations as JSON
POST /api/advise/stream  — Server-Sent Events: each agent's progress, every
                           page the researcher visits, the fact-check report,
                           audit, schedule, then the streamed advising memo
POST /api/advise/whatif  — graduation date under up to 4 scenarios (switch
                           major, add a minor, fail a course, change load)
POST /api/advise/import  — a degree audit / transcript (PDF or pasted text)
                           -> courses, hours, GPA for the form. Read in
                           memory, never stored, logged or traced.
POST /api/advise/prep-sheet   — one-page advising prep sheet (PDF)
GET  /api/advise/calendar.ics — the planned term's deadlines as a calendar

Both run the seven-agent pipeline in app/advising/pipeline.py. Advising
requests share the chat rate limit: each one can fetch up to WEB_MAX_PAGES
pages and make a few LLM calls.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..advising import pipeline, prep, transcript
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


_TOO_LARGE = f"The file is larger than {transcript.MAX_BYTES // 1_000_000} MB."


@router.post("/import")
async def import_audit(request: Request) -> JSONResponse:
    """The body is the PDF itself (Content-Type: application/pdf) or the audit's
    text (text/plain). Personal data: nothing here is stored, logged or traced
    except counts."""
    _guard(request)
    try:
        if int(request.headers.get("content-length") or 0) > transcript.MAX_BYTES:
            raise HTTPException(413, _TOO_LARGE)
    except ValueError:
        pass
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > transcript.MAX_BYTES:
            raise HTTPException(413, _TOO_LARGE)
    if not body.strip():
        raise HTTPException(422, "The upload is empty.")
    with start_trace(kind="import") as trace:
        try:
            result = await run_in_threadpool(transcript.read, bytes(body),
                                             request.headers.get("content-type", ""))
        except transcript.TranscriptError as e:
            trace.attributes["outcome"] = "unreadable"
            raise HTTPException(422, str(e)) from None
        trace.attributes.update(outcome="ok", source=result.source, courses=len(result.courses))
    return JSONResponse(result.to_dict(), headers={"Cache-Control": "no-store"})


def _filename(term: str, ext: str) -> str:
    return f"advising-prep-{re.sub(r'[^a-z0-9]+', '-', term.lower()).strip('-')}.{ext}"


@router.post("/prep-sheet")
def prep_sheet(payload: AdviseRequest, request: Request) -> Response:
    _guard(request)
    with start_trace(kind="prep_sheet"):
        sheet = prep.build(payload.model_dump())
        pdf = prep.render_pdf(sheet)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_filename(sheet.term, "pdf")}"',
        "Cache-Control": "no-store"})


@router.get("/calendar.ics")
def calendar(term: str = Query(..., pattern=r"^(Fall|Spring|Summer) 20\d\d$")) -> Response:
    items = prep.deadlines(term)
    if not items:
        raise HTTPException(404, f"No upcoming academic calendar dates are loaded for {term} yet. "
                                 "Check the Registrar's academic calendar.")
    return Response(prep.calendar_ics(items, term), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="txst-deadlines-'
                                                     f'{term.lower().replace(" ", "-")}.ics"'})
