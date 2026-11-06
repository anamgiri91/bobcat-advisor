"""
routers/events.py
=================
POST /api/events  {"event": "view_advisor"}  -> 204

Anonymous feature counters, to learn which parts of the app are used: one
row per (day, event) holding a count. Nothing identifies a person: no user
or session id, IP address, user agent or page content is stored. Event
names come from a fixed list. Counts are shown in GET /api/analytics.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import UsageCount
from ..services.protection import RateLimiter, client_key

EVENTS = ("view_chat", "view_advisor", "view_careers", "view_planner",
          "advisor_submit", "advisor_complete", "career_submit", "career_complete",
          "import_pdf", "import_text", "prep_sheet", "calendar", "crn_copy", "whatif_run")

router = APIRouter(prefix="/api/events", tags=["events"])
limiter = RateLimiter(per_minute=60)


class Event(BaseModel):
    event: Literal[EVENTS]  # type: ignore[valid-type]


def bump(db: Session, event: str, day: dt.date) -> None:
    where = (UsageCount.day == day) & (UsageCount.event == event)
    if db.execute(update(UsageCount).where(where).values(count=UsageCount.count + 1)).rowcount:
        db.commit()
        return
    db.add(UsageCount(day=day, event=event, count=1))
    try:
        db.commit()
    except IntegrityError:          # another request created the row first
        db.rollback()
        db.execute(update(UsageCount).where(where).values(count=UsageCount.count + 1))
        db.commit()


@router.post("", status_code=204)
def record(payload: Event, request: Request, db: Session = Depends(get_db)) -> Response:
    if not limiter.allow(client_key(request))[0]:
        raise HTTPException(429, "Too many events.")
    bump(db, payload.event, dt.date.today())
    return Response(status_code=204)
