"""
routers/chat.py
================
POST /api/chat/ask — the core endpoint. Runs the RAG pipeline (ChromaDB
retrieval + Groq generation, unchanged from the original project) and logs
the turn to Postgres: creates a conversation on the first message, stores
both the user question and the assistant answer as Message rows, and
records latency + retrieved chunk count for analytics.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Conversation, Message
from ..schemas import AskRequest, AskResponse
from ..rag.generate import answer_question_api

router = APIRouter(prefix="/api/chat", tags=["chat"])

VALID_SOURCE_FILTERS = {None, "rmp", "coursicle", "reddit", "official"}


def _make_title(question: str) -> str:
    q = question.strip()
    return q if len(q) <= 60 else q[:57] + "..."


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    if payload.source_filter not in VALID_SOURCE_FILTERS:
        raise HTTPException(400, f"Invalid source_filter: {payload.source_filter}")

    # Get or create the conversation
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found")
    else:
        conversation = Conversation(title=_make_title(payload.question))
        db.add(conversation)
        db.flush()  # assigns conversation.id without committing yet

    # Log the user's question
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.question,
        source_filter=payload.source_filter,
    )
    db.add(user_message)

    # Run the RAG pipeline
    start = time.perf_counter()
    try:
        result = answer_question_api(
            query=payload.question,
            db_path=settings.CHROMA_DB_PATH,
            top_k=settings.DEFAULT_TOP_K,
            source_filter=payload.source_filter,
        )
    except EnvironmentError as e:
        # e.g. GROQ_API_KEY missing
        raise HTTPException(503, str(e))
    latency_ms = int((time.perf_counter() - start) * 1000)

    # Log the assistant's answer
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=result["answer"],
        sources=result["sources"],
        latency_ms=latency_ms,
        retrieved_chunk_count=result["chunk_count"],
        source_filter=payload.source_filter,
    )
    db.add(assistant_message)
    db.commit()

    return AskResponse(
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        answer=result["answer"],
        sources=result["sources"],
        latency_ms=latency_ms,
        retrieved_chunk_count=result["chunk_count"],
    )
