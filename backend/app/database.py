"""
database.py
============
SQLAlchemy engine + session management for the Postgres-backed
chat history / feedback / analytics store.

Note: this database has nothing to do with the RAG vector index.
Embeddings still live in ChromaDB (see app/rag/retrieve.py). Postgres here
is a normal relational store for conversations, so the app can show chat
history, compute analytics (most-asked-about professors, latency, etc.),
and collect user feedback on answer quality.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

# SQLite (used by the test suite) needs cross-thread access because the
# streaming endpoint persists the turn from a worker thread.
_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True,
                       connect_args=_connect_args)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
