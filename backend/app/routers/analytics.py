"""
routers/analytics.py
=====================
GET /api/analytics        — headline numbers for the header bar
GET /api/analytics/agents — pipeline observability computed from stored traces:
                            latency percentiles, per-agent span timings, token
                            usage, verifier pass rate, answer modes, intents,
                            feedback by intent

Everything is computed from the Postgres chat log (messages.trace etc.), so
"how faithful are answers in production?" is a query, not a guess.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Feedback, Message
from ..services.protection import answer_cache

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(p * len(values)))], 1)


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

    avg_pass = db.scalar(
        select(func.avg(Message.verifier_pass_rate)).where(Message.verifier_pass_rate.is_not(None))
    )

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
        "verifier_pass_rate": round(avg_pass, 3) if avg_pass is not None else None,
    }


@router.get("/agents")
def agent_analytics(limit: int = 500, db: Session = Depends(get_db)):
    rows = db.execute(
        select(Message.latency_ms, Message.intent, Message.answer_mode,
               Message.verifier_pass_rate, Message.total_tokens, Message.trace,
               Feedback.helpful)
        .join(Feedback, Feedback.message_id == Message.id, isouter=True)
        .where(Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
    span_ms: dict[str, list[float]] = defaultdict(list)
    llm_calls, cache_hits = [], 0
    for r in rows:
        t = r.trace or {}
        cache_hits += bool(t.get("cache_hit"))
        if t.get("llm_calls") is not None:
            llm_calls.append(t["llm_calls"])
        for s in t.get("spans", []):
            span_ms[s["name"]].append(s["duration_ms"])

    feedback_by_intent: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r.helpful is not None:
            feedback_by_intent[r.intent or "unknown"]["helpful" if r.helpful else "not_helpful"] += 1

    pass_rates = [r.verifier_pass_rate for r in rows if r.verifier_pass_rate is not None]
    tokens = [r.total_tokens for r in rows if r.total_tokens]
    return {
        "answers": len(rows),
        "latency_ms": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95),
                       "max": max(latencies) if latencies else None},
        "spans": {
            name: {"count": len(v), "p50_ms": _pct(v, 0.5), "p95_ms": _pct(v, 0.95)}
            for name, v in sorted(span_ms.items())
        },
        "tokens": {"avg": round(statistics.mean(tokens)) if tokens else None,
                   "total": sum(tokens)},
        "avg_llm_calls": round(statistics.mean(llm_calls), 2) if llm_calls else None,
        "verifier": {"answers_checked": len(pass_rates),
                     "avg_pass_rate": round(statistics.mean(pass_rates), 3) if pass_rates else None,
                     "fully_supported": sum(1 for p in pass_rates if p == 1.0)},
        "answer_modes": dict(Counter(r.answer_mode or "unknown" for r in rows)),
        "intents": dict(Counter(r.intent or "unknown" for r in rows)),
        "feedback_by_intent": {k: dict(v) for k, v in feedback_by_intent.items()},
        "cache": {"hits_in_log": cache_hits, "process_hits": answer_cache.hits,
                  "process_misses": answer_cache.misses},
    }
