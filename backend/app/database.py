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

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_sqlite = settings.DATABASE_URL.startswith("sqlite")
if _sqlite:
    # SQLite (tests, local runs) needs cross-thread access because the
    # streaming endpoint persists the turn from a worker thread, and a busy
    # timeout so concurrent writers wait instead of failing "database is locked".
    engine = create_engine(settings.DATABASE_URL, future=True,
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")      # readers don't block the writer
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()
else:
    # Postgres: small pool (one worker process on a 512MB plan), recycle
    # connections before managed databases drop idle ones.
    engine = create_engine(settings.DATABASE_URL, future=True, pool_pre_ping=True,
                           pool_size=5, max_overflow=10, pool_recycle=1800, pool_timeout=10)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
