"""
config.py
=========
Centralised app settings, loaded from environment variables (.env in dev,
real env vars in production/Render/Docker).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Postgres — chat history, feedback, analytics (NOT vector storage)
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://bobcat:bobcat@localhost:5432/bobcat_advisor",
    )

    # ChromaDB — vector store for RAG retrieval (unchanged from original project)
    CHROMA_DB_PATH: str = os.environ.get("CHROMA_DB_PATH", "data/chroma_db")

    # Groq LLM
    GROQ_API_KEY: str | None = os.environ.get("GROQ_API_KEY")

    # CORS — the deployed frontend origin(s), comma-separated
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    DEFAULT_TOP_K: int = 5


settings = Settings()
