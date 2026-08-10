"""
routers/history.py
===================
Read-only endpoints backing the chat-history sidebar in the frontend:
  GET /api/history                 recent conversations
  GET /api/history/{conversation_id}  full transcript for one conversation
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Conversation, Feedback, Message
from ..schemas import ConversationDetail, ConversationSummary, MessageOut

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Conversation.id,
            Conversation.title,
            Conversation.created_at,
            func.count(Message.id).label("message_count"),
        )
        .join(Message, Message.conversation_id == Conversation.id, isouter=True)
        .group_by(Conversation.id)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    ).all()

    return [
        ConversationSummary(
            id=r.id, title=r.title, created_at=r.created_at, message_count=r.message_count
        )
        for r in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db)):
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")

    feedback_by_message = {
        f.message_id: f.helpful
        for f in db.query(Feedback).join(Message).filter(
            Message.conversation_id == conversation_id
        )
    }

    messages = [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            sources=m.sources,
            latency_ms=m.latency_ms,
            retrieved_chunk_count=m.retrieved_chunk_count,
            created_at=m.created_at,
            helpful=feedback_by_message.get(m.id),
        )
        for m in conversation.messages
    ]

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        messages=messages,
    )
