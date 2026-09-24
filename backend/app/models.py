"""
models.py
=========
Postgres tables for chat history, generated answers, and user feedback.

Schema
------
conversations   One row per chat session (created when the first
                question is asked). `title` is derived from the first
                question so a history sidebar has something to show.

messages        Every user question and every assistant answer, in order.
                Assistant messages store the retrieved source list and
                generation latency so the app can surface RAG-specific
                analytics (which is the whole point of logging this to a
                real relational DB instead of just printing to stdout).

feedback        Thumbs up/down against a specific assistant message.
                One-to-one; re-voting overwrites the previous value.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# JSONB on Postgres (same as migration 0001), plain JSON elsewhere so the
# test suite can run against SQLite without a Postgres server.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)

    # Assistant-only fields (null for user messages)
    source_filter: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieved_chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Agent pipeline observability (migration 0002)
    intent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    answer_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)  # llm|extractive|canned|no_evidence
    verifier_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    feedback: Mapped[Feedback | None] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("message_id", name="uq_feedback_message"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    helpful: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message: Mapped[Message] = relationship(back_populates="feedback")
