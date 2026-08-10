"""
routers/analytics.py
=====================
GET /api/analytics — aggregate stats computed directly from the Postgres
chat log. This is the payoff of logging every question to a real
relational store instead of just printing to stdout: we can answer
questions like "what do people actually ask about" without touching the
vector index at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Feedback, Message

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("")
def get_analytics(db: Session = Depends(get_db)):
    total_questions = db.scalar(
        select(func.count(Message.id)).where(Message.role == "user")
    ) or 0

    avg_latency = db.scalar(
        select(func.avg(Message.latency_ms)).where(Message.role == "assistant")
    )

    by_source_filter = db.execute(
        select(Message.source_filter, func.count(Message.id))
        .where(Message.role == "user")
        .group_by(Message.source_filter)
    ).all()

    helpful_counts = db.execute(
        select(Feedback.helpful, func.count(Feedback.id)).group_by(Feedback.helpful)
    ).all()

    return {
        "total_questions": total_questions,
        "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
        "questions_by_source_filter": {
            (sf or "all"): count for sf, count in by_source_filter
        },
        "feedback": {
            "helpful": next((c for h, c in helpful_counts if h is True), 0),
            "not_helpful": next((c for h, c in helpful_counts if h is False), 0),
        },
    }
