"""
routers/feedback.py
====================
POST /api/feedback — thumbs up/down on a specific assistant message.
Upserts, so changing your vote overwrites the previous one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Feedback, Message
from ..schemas import FeedbackRequest, FeedbackResponse

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackRequest, db: Session = Depends(get_db)):
    message = db.get(Message, payload.message_id)
    if not message or message.role != "assistant":
        raise HTTPException(404, "Assistant message not found")

    existing = db.query(Feedback).filter_by(message_id=payload.message_id).first()
    if existing:
        existing.helpful = payload.helpful
    else:
        db.add(Feedback(message_id=payload.message_id, helpful=payload.helpful))

    db.commit()
    return FeedbackResponse(message_id=payload.message_id, helpful=payload.helpful)
