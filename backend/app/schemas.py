"""
schemas.py
==========
Pydantic models for request/response validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
    source_filter: str | None = None  # "rmp" | "coursicle" | "reddit" | "official" | None


class Citation(BaseModel):
    n: int
    kind: str
    label: str
    agent: str
    chunk_id: str | None = None
    snippet: str


class AskResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    sources: list[str]
    citations: list[Citation] = []
    latency_ms: int
    retrieved_chunk_count: int
    intent: str | None = None
    mode: str | None = None               # llm | extractive | canned | no_evidence
    verifier_pass_rate: float | None = None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sources: list[str] | None = None
    latency_ms: int | None = None
    retrieved_chunk_count: int | None = None
    intent: str | None = None
    answer_mode: str | None = None
    verifier_pass_rate: float | None = None
    created_at: datetime
    helpful: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    messages: list[MessageOut]

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    message_id: uuid.UUID
    helpful: bool


class FeedbackResponse(BaseModel):
    message_id: uuid.UUID
    helpful: bool
