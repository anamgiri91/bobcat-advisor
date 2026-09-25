"""
routers/chat.py
================
POST /api/chat/ask         — full answer as JSON (simple clients, evals)
POST /api/chat/ask/stream  — Server-Sent Events: plan, agent progress,
                             sources, answer tokens, verification, done

Both run the multi-agent pipeline (app/agents/orchestrator.py) and log the
turn — including its trace — to Postgres.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import AskRequest, AskResponse
from ..services import chat_service
from ..services.protection import client_key, rate_limiter

router = APIRouter(prefix="/api/chat", tags=["chat"])

VALID_SOURCE_FILTERS = {None, "official"}


def _guard(payload: AskRequest, request: Request) -> None:
    if payload.source_filter not in VALID_SOURCE_FILTERS:
        raise HTTPException(400, f"Invalid source_filter: {payload.source_filter}")
    allowed, retry_after = rate_limiter.allow(client_key(request))
    if not allowed:
        raise HTTPException(429, "Too many questions — please wait a moment.",
                            headers={"Retry-After": str(int(retry_after) + 1)})


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, request: Request, db: Session = Depends(get_db)) -> AskResponse:
    _guard(payload, request)
    try:
        result = chat_service.answer_sync(db, payload.question, payload.conversation_id,
                                          payload.source_filter)
    except chat_service.ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None
    verification = result.get("verification") or {}
    return AskResponse(
        conversation_id=result["conversation_id"],
        message_id=result["message_id"],
        answer=result["answer"],
        sources=[s["label"] for s in result.get("sources", [])],
        citations=result.get("sources", []),
        latency_ms=result["latency_ms"],
        retrieved_chunk_count=sum(1 for s in result.get("sources", []) if s["kind"] == "catalog"),
        intent=(result.get("plan") or {}).get("intent"),
        mode=result.get("mode"),
        verifier_pass_rate=((verification.get("claims") or {}).get("pass_rate")),
        saved=result.get("saved", True),
    )


@router.post("/ask/stream")
def ask_stream(payload: AskRequest, request: Request, db: Session = Depends(get_db)):
    _guard(payload, request)
    try:
        history = chat_service.load_history(db, payload.conversation_id)
    except chat_service.ConversationNotFound:
        raise HTTPException(404, "Conversation not found") from None

    def sse():
        for event in chat_service.answer_stream(payload.question, payload.conversation_id,
                                                payload.source_filter, history):
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
