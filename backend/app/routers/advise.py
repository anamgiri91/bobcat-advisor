"""
routers/advise.py
=================
POST /api/advise         — course recommendations as JSON
POST /api/advise/stream  — Server-Sent Events: each agent's progress, every
                           page the researcher visits, the fact-check report,
                           audit, schedule, then the streamed advising memo

Both run the seven-agent pipeline in app/advising/pipeline.py. Advising
requests share the chat rate limit: each one can fetch up to WEB_MAX_PAGES
pages and make a few LLM calls.
"""

from __future__ import annotations

import contextvars
import json
import queue
import threading
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..advising import pipeline
from ..schemas import AdviseRequest
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
    with start_trace():
        return pipeline.advise(payload.model_dump())


_SENTINEL = object()


def _events(raw: dict) -> Iterator[dict]:
    """Run the pipeline in one worker thread (so its trace context survives
    across yields) and relay its events."""
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            with start_trace() as trace:
                for event in pipeline.run(raw):
                    if event["type"] == "done":
                        event["trace"] = trace.to_dict()
                    q.put(event)
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=contextvars.copy_context().run, args=(worker,), daemon=True).start()
    while (event := q.get()) is not _SENTINEL:
        yield event


@router.post("/stream")
def advise_stream(payload: AdviseRequest, request: Request):
    _guard(request)
    raw = payload.model_dump()

    def sse():
        for event in _events(raw):
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
