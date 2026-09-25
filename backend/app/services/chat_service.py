"""
chat_service.py
===============
Glue between the HTTP layer and the agent pipeline: conversation lookup,
history loading, the answer cache, running the orchestrator inside a
trace, and persisting the turn (with its trace) to Postgres.

Streaming runs the pipeline in a worker thread that pushes events onto a
queue. A plain sync generator can't be used directly: Starlette advances
it with a fresh contextvars copy on every next(), which would silently
drop the per-request trace between events.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
import uuid
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..agents import orchestrator
from ..database import SessionLocal
from ..guardrails import redact_pii
from ..models import Conversation, Message
from ..tracing import start_trace
from .protection import answer_cache

log = logging.getLogger("bobcat.chat")

HISTORY_TURNS = 6


class ConversationNotFound(LookupError):
    pass


def _make_title(question: str) -> str:
    q = question.strip().replace("\n", " ")
    return q if len(q) <= 60 else q[:57] + "..."


def load_history(db: Session, conversation_id: uuid.UUID | None) -> list[dict]:
    if conversation_id is None:
        return []
    if db.get(Conversation, conversation_id) is None:
        raise ConversationNotFound(str(conversation_id))
    rows = db.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_TURNS)
    ).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def persist_turn(
    db: Session,
    conversation_id: uuid.UUID | None,
    question: str,
    source_filter: str | None,
    done: dict,
    latency_ms: int,
) -> tuple[uuid.UUID, uuid.UUID]:
    if conversation_id is None:
        conversation = Conversation(title=_make_title(question))
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id

    # Store the question PII-redacted: logs are for analytics, not people's
    # phone numbers.
    db.add(Message(conversation_id=conversation_id, role="user",
                   content=redact_pii(question), source_filter=source_filter))
    verification = done.get("verification") or {}
    claims = verification.get("claims") or {}
    trace = done.get("trace") or {}
    assistant = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=done.get("answer", ""),
        sources=[s["label"] for s in done.get("sources", [])],
        latency_ms=latency_ms,
        retrieved_chunk_count=sum(1 for s in done.get("sources", []) if s["kind"] == "catalog"),
        source_filter=source_filter,
        intent=(done.get("plan") or {}).get("intent"),
        answer_mode=done.get("mode"),
        verifier_pass_rate=claims.get("pass_rate"),
        total_tokens=trace.get("total_tokens"),
        trace={**trace, "plan": done.get("plan"), "verification": verification,
               "cache_hit": done.get("cache_hit", False)},
    )
    db.add(assistant)
    db.commit()
    return conversation_id, assistant.id


def safe_persist(db: Session, conversation_id: uuid.UUID | None, question: str,
                 source_filter: str | None, done: dict, latency_ms: int
                 ) -> tuple[uuid.UUID, uuid.UUID, bool]:
    """Save the turn; if the database fails, the student still gets the answer
    (with `saved: false`) instead of a 500 for work that already succeeded."""
    try:
        conv_id, msg_id = persist_turn(db, conversation_id, question, source_filter, done, latency_ms)
        return conv_id, msg_id, True
    except SQLAlchemyError:
        db.rollback()
        log.exception("couldn't save chat turn; returning the answer unsaved")
        return conversation_id or uuid.uuid4(), uuid.uuid4(), False


def _run_pipeline(question: str, history: list[dict], source_filter: str | None,
                  emit) -> dict:
    """Run the orchestrator (or serve from cache), calling emit(event) for each event."""
    cache_key = answer_cache.key(question, source_filter)
    if not history:
        cached = answer_cache.get(cache_key)
        if cached:
            done = {**cached, "cache_hit": True, "trace": {"total_tokens": 0, "llm_calls": 0}}
            emit({"type": "plan", "plan": cached.get("plan")})
            emit({"type": "sources", "sources": cached.get("sources", []),
                  "agent_data": cached.get("agent_data", {})})
            emit({"type": "token", "text": cached["answer"]})
            return done

    with start_trace(kind="chat") as trace:
        done: dict = {}
        for event in orchestrator.run(question, history, source_filter):
            if event["type"] == "done":
                done = event
            else:
                emit(event)
        trace.attributes.update(intent=(done.get("plan") or {}).get("intent") or "",
                                mode=done.get("mode") or "")
    done["trace"] = trace.to_dict()

    # Cache only complete, LLM-written answers (not degraded fallbacks).
    if not history and done.get("mode") in ("llm", "canned"):
        answer_cache.put(cache_key, {k: v for k, v in done.items() if k != "trace"})
    return done


def answer_sync(db: Session, question: str, conversation_id: uuid.UUID | None,
                source_filter: str | None) -> dict:
    history = load_history(db, conversation_id)
    t0 = time.perf_counter()
    done = _run_pipeline(question, history, source_filter, emit=lambda e: None)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    conv_id, msg_id, saved = safe_persist(db, conversation_id, question, source_filter, done, latency_ms)
    return {**done, "conversation_id": conv_id, "message_id": msg_id, "latency_ms": latency_ms,
            "saved": saved}


_SENTINEL = object()


def answer_stream(question: str, conversation_id: uuid.UUID | None,
                  source_filter: str | None, history: list[dict]) -> Iterator[dict]:
    """Yield pipeline events; the last one is `done` with ids and latency."""
    q: queue.Queue = queue.Queue()

    def worker():
        t0 = time.perf_counter()
        try:
            done = _run_pipeline(question, history, source_filter, emit=q.put)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            db = SessionLocal()
            try:
                conv_id, msg_id, saved = safe_persist(db, conversation_id, question,
                                                      source_filter, done, latency_ms)
            finally:
                db.close()
            trace = done.get("trace") or {}
            q.put({
                "type": "done",
                "conversation_id": str(conv_id),
                "message_id": str(msg_id),
                "saved": saved,
                "answer": done.get("answer"),
                "mode": done.get("mode"),
                "verification": done.get("verification"),
                "latency_ms": latency_ms,
                "trace": {"total_tokens": trace.get("total_tokens"),
                          "llm_calls": trace.get("llm_calls"),
                          "spans": [{"name": s["name"], "duration_ms": s["duration_ms"]}
                                    for s in trace.get("spans", [])]},
                "cache_hit": done.get("cache_hit", False),
            })
        except Exception as e:
            q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=contextvars.copy_context().run, args=(worker,), daemon=True).start()
    while True:
        event = q.get()
        if event is _SENTINEL:
            return
        yield event
