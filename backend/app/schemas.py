"""
schemas.py
==========
Pydantic models for request/response validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
    source_filter: str | None = None  # "official" | None (only the catalog is indexed)


class Citation(BaseModel):
    n: int
    kind: str
    label: str
    agent: str
    chunk_id: str | None = None
    url: str | None = None
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


# ---------------------------------------------------------------------------
# Course recommendations (advising pipeline)
# ---------------------------------------------------------------------------

class AdviseRequest(BaseModel):
    major: str = Field("Computer Science", max_length=80)
    degree: str = Field("BS", max_length=6)
    year: str = Field("freshman", max_length=20)          # freshman..senior, or 1-4
    semester: str = Field("Fall", max_length=10)          # term being planned
    catalog_year: str | None = Field(None, max_length=9)  # "2025-2026"
    completed: list[str] = Field(default_factory=list, max_length=80)
    in_progress: list[str] = Field(default_factory=list, max_length=12)
    gpa: float | None = Field(None, ge=0, le=4)
    target_credits: int = Field(15, ge=3, le=21)
    interests: list[str] = Field(default_factory=list, max_length=8)
    career_goal: str = Field("", max_length=200)
    minor: str = Field("", max_length=80)
    notes: str = Field("", max_length=1000)
    term_year: int | None = Field(None, ge=2000, le=2100)
    # Timetable preferences
    preferred_days: str = Field("", max_length=14)        # "MWF"
    earliest_start: str | None = Field(None, max_length=8) # "09:00"
    latest_end: str | None = Field(None, max_length=8)
    busy: list[str] = Field(default_factory=list, max_length=10)   # ["TR 12:00-17:00"]
    modality: str | None = Field(None, max_length=10)


class Scenario(BaseModel):
    type: Literal["switch_major", "add_minor", "fail_course", "change_load"]
    major: str | None = Field(None, max_length=80)
    degree: str | None = Field(None, max_length=6)
    minor: str | None = Field(None, max_length=80)
    course: str | None = Field(None, max_length=12)
    target_credits: int | None = Field(None, ge=3, le=21)


class WhatIfRequest(BaseModel):
    profile: AdviseRequest
    scenarios: list[Scenario] = Field(..., min_length=1, max_length=4)
