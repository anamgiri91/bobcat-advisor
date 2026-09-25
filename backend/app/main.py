"""
main.py
=======
FastAPI entrypoint for the Bobcat Advisor API.

Architecture (see docs/ARCHITECTURE.md)
---------------------------------------
- Agents:      router -> specialists (catalog, search, planner) ->
               synthesizer -> verifier                   (app/agents/)
- Advising:    seven-agent course recommendations over the live TXST
               catalog                                   (app/advising/)
- Retrieval:   hybrid dense + BM25 with RRF over the ChromaDB-persisted
               index                                     (app/rag/index.py)
- Knowledge:   prerequisite graph, course registry
                                                         (app/knowledge/)
- Generation:  Gemini or Groq (OpenAI-compatible HTTP), with fallback, JSON mode, streaming
               and per-request budgets                   (app/llm.py)
- Persistence: Postgres — chat history, feedback, and a trace per answer
               (latency per agent, tokens, verifier pass rate).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import llm, telemetry
from .config import settings
from .database import Base, engine
from .routers import advise, analytics, chat, feedback, history, knowledge, schedule

log = logging.getLogger("bobcat")
_warm = {"ready": False, "error": None}


def _warmup() -> None:
    """
    Load the index, embedding model and registry off the request
    path. Runs in a thread so the health check answers immediately (Render
    kills services whose health check doesn't respond during boot).
    """
    try:
        from .knowledge.corpus import registry
        from .rag.index import embed_query, get_index
        get_index()
        if settings.RETRIEVAL_MODE != "bm25":   # keyword-only mode never loads the model
            embed_query("warmup")
        registry()
        missing = llm.missing_models()
        if missing:
            log.error("Configured %s models not listed for this API key: %s",
                      settings.LLM_PROVIDER, missing)
        _warm["ready"] = True
    except Exception as e:  # surface in /api/health rather than crash the process
        _warm["error"] = f"{type(e).__name__}: {e}"
        log.exception("warmup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables if they don't exist yet.
    # In production, run `alembic upgrade head` instead (see backend/alembic).
    Base.metadata.create_all(bind=engine)
    telemetry.setup()
    threading.Thread(target=_warmup, daemon=True).start()
    yield
    telemetry.shutdown()


app = FastAPI(
    title="Bobcat Advisor API",
    description="Multi-agent advisor for TXST CS courses, grounded in the official catalog.",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def observe(request: Request, call_next):
    """Request id, security headers, and per-route latency for telemetry.
    The route template (/api/courses/{code}) is used, never the raw path,
    so metrics stay low-cardinality."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        route = request.scope.get("route")
        telemetry.record_http(getattr(route, "path", "unmatched"), request.method, status,
                              (time.perf_counter() - t0) * 1000)
    response.headers["X-Request-ID"] = request_id
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(history.router)
app.include_router(feedback.router)
app.include_router(analytics.router)
app.include_router(knowledge.router)
app.include_router(advise.router)
app.include_router(schedule.router)


def _db_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("database health check failed", exc_info=True)
        return False


@app.get("/api/health/live")
def live():
    """Liveness: the process is up. Doesn't touch the database or the LLM."""
    return {"status": "ok"}


@app.get("/api/health")
def health(deep: bool = False):
    """
    ?deep=true makes one tiny real call per configured model (costs quota) —
    the only reliable check that the key can use them. Use it after changing
    LLM_*_MODEL, not as the platform's liveness probe.
    """
    db_ok = _db_ok()
    out = {
        "status": "ok" if db_ok and _warm["error"] is None else "degraded",
        "version": settings.APP_VERSION,
        "database": "ok" if db_ok else "unreachable",
        "telemetry": telemetry.enabled(),
        "index_ready": _warm["ready"],
        "warmup_error": _warm["error"],
        "llm_provider": settings.LLM_PROVIDER,
        "llm_available": llm.is_available(),
        "models": llm.configured_models(),
        # Non-empty means those models will fail every call: fix LLM_*_MODEL.
        "models_unlisted": llm.missing_models(),
        "reranker_enabled": settings.RERANKER_ENABLED,
    }
    if deep and out["llm_available"]:
        out["model_probe"] = llm.probe_models()
    return out
