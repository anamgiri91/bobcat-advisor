"""
calendar.py
===========
Looks up dated facts (data/kb_dates.jsonl) for a question and labels each
one relative to today, so an answer never presents last term's deadline as
the next one.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from ..config import settings
from ..rag.index import tokenize

_cache: dict = {"mtime": None, "facts": []}
_lock = threading.Lock()

# Words that carry no signal for matching a question to a calendar event.
_GENERIC = {"when", "what", "date", "day", "last", "first", "deadline", "is", "the", "semester",
            "term", "class", "course", "txst", "can", "do", "does", "open", "start"}


def load_facts(path: Path | None = None) -> list[dict]:
    path = path or Path(settings.DATA_DIR) / "kb_dates.jsonl"
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    with _lock:
        if _cache["mtime"] != mtime:
            _cache["facts"] = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
            _cache["mtime"] = mtime
        return _cache["facts"]


def lookup(question: str, today: dt.date | None = None, limit: int = 6,
           facts: list[dict] | None = None) -> list[dict]:
    """Facts whose event matches the question, upcoming first (soonest), then most recent past."""
    today = today or dt.date.today()
    facts = load_facts() if facts is None else facts
    q = set(tokenize(question)) - _GENERIC
    if not q:
        return []
    scored = []
    for f in facts:
        ev = set(tokenize(f["event"]))
        overlap = len(q & ev)
        if overlap == 0:
            continue
        date = dt.date.fromisoformat(f["date"])
        days = (date - today).days
        scored.append({**f, "days_from_today": days, "status": "upcoming" if days >= 0 else "past",
                       "_score": overlap})
    best = max((s["_score"] for s in scored), default=0)
    scored = [s for s in scored if s["_score"] == best]
    upcoming = sorted((s for s in scored if s["status"] == "upcoming"), key=lambda s: s["date"])
    past = sorted((s for s in scored if s["status"] == "past"), key=lambda s: s["date"], reverse=True)
    out = (upcoming + past)[:limit]
    for s in out:
        s.pop("_score", None)
    return out
