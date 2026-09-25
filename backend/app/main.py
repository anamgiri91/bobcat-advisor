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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import llm
from .config import settings
from .database import Base, engine
from .routers import advise, analytics, chat, feedback, history, knowledge

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
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="Bobcat Advisor API",
    description="Multi-agent advisor for TXST CS courses, grounded in the official catalog.",
    version="3.0.0",
    lifespan=lifespan,
)

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


@app.get("/api/health")
def health(deep: bool = False):
    """
    ?deep=true makes one tiny real call per configured model (costs quota) —
    the only reliable check that the key can use them. Use it after changing
    LLM_*_MODEL, not as the platform's liveness probe.
    """
    out = {
        "status": "ok",
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
