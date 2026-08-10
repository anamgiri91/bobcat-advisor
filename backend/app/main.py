"""
main.py
=======
FastAPI entrypoint for the Bobcat Advisor API.

Architecture
------------
- Retrieval:   ChromaDB + sentence-transformers (app/rag/retrieve.py) — unchanged
                from the original Gradio project.
- Generation:  Groq (llama-3.3-70b-versatile)  (app/rag/generate.py)
- Persistence: Postgres, via SQLAlemy models (app/models.py) — stores chat
                history, per-answer metadata (latency, retrieved chunk count,
                source filter), and user feedback. This is what turns the
                project from a script into an application: every question
                asked is durable, queryable, and attributable.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine
from .routers import chat, feedback, history, analytics


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables if they don't exist yet.
    # In production, run `alembic upgrade head` instead (see backend/alembic).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Bobcat Advisor API",
    description="RAG API for TXST CS professor reviews, backed by ChromaDB + Postgres.",
    version="2.0.0",
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


@app.get("/api/health")
def health():
    return {"status": "ok"}
