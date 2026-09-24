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


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


PROVIDERS: dict[str, dict[str, str]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        # gemini-2.5-* are listed but 404 ("no longer available to new users");
        # 3.7/3.8-flash returned 503 under load when these were chosen.
        "model": "gemini-3.6-flash",
        "fallback": "gemini-3.5-flash-lite",
        "fast": "gemini-3.6-flash",
        # Default thinking spent ~1.5k of a 1.6k max_tokens budget and
        # truncated answers (finish_reason=length) in ~9s; "low" finished in
        # ~6s, "minimal" in ~4s.
        "reasoning_effort": "low",
        "fast_reasoning_effort": "minimal",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "fallback": "openai/gpt-oss-20b",
        "fast": "openai/gpt-oss-20b",
        "reasoning_effort": "low",
        "fast_reasoning_effort": "low",
    },
}


def _provider() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    if explicit in PROVIDERS:
        return explicit
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return "gemini"


def _llm_env(suffix: str, key: str) -> str:
    """LLM_<SUFFIX>, then the legacy GROQ_<SUFFIX> (Groq only), then the provider default."""
    provider = _provider()
    legacy = os.environ.get(f"GROQ_{suffix}") if provider == "groq" else None
    return os.environ.get(f"LLM_{suffix}") or legacy or PROVIDERS[provider][key]


class Settings:
    # Postgres — chat history, feedback, analytics (NOT vector storage)
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://bobcat:bobcat@localhost:5432/bobcat_advisor",
    )

    # ChromaDB — persistent vector store for RAG retrieval
    CHROMA_DB_PATH: str = os.environ.get("CHROMA_DB_PATH", "data/chroma_db")
    DATA_DIR: str = os.environ.get("DATA_DIR", "data")
    DOCUMENTS_DIR: str = os.environ.get("DOCUMENTS_DIR", "documents")

    # LLM provider. Both providers expose an OpenAI-compatible chat API, so one
    # HTTP backend (app/llm.py) serves either; switching is configuration.
    # "auto" picks Gemini when GEMINI_API_KEY is set, else Groq.
    GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY") or None
    GROQ_API_KEY: str | None = os.environ.get("GROQ_API_KEY") or None
    LLM_PROVIDER: str = _provider()
    LLM_API_KEY: str | None = GEMINI_API_KEY if LLM_PROVIDER == "gemini" else GROQ_API_KEY
    LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", PROVIDERS[LLM_PROVIDER]["base_url"])

    # Model IDs are env vars so a provider-side deprecation is a config change,
    # not a deploy. Availability depends on the key, not the public model page
    # (Gemini lists 2.5 models that 404 for new keys) — probe with
    # GET /api/health?deep=true after changing them.
    # Synthesis: the one call where answer quality matters most.
    LLM_MODEL: str = _llm_env("MODEL", "model")
    # Used when a model 404s (removed / no access), 429s (rate limited), 5xx /
    # times out (overloaded), or fails JSON validation. Rate limits are per
    # model on both providers, so a different model genuinely buys headroom.
    LLM_FALLBACK_MODEL: str = _llm_env("FALLBACK_MODEL", "fallback")
    # Router + verifier: short structured outputs, latency matters more.
    LLM_FAST_MODEL: str = _llm_env("FAST_MODEL", "fast")
    # Reasoning models spend max_tokens on hidden thinking, so effort is a
    # latency and truncation control, not just a quality knob (see
    # PROVIDERS for the measurements). Synthesis vs. the fast path (router,
    # verifier, taggers) are tuned separately. Empty = don't send the field.
    LLM_REASONING_EFFORT: str = _llm_env("REASONING_EFFORT", "reasoning_effort")
    LLM_FAST_REASONING_EFFORT: str = _llm_env("FAST_REASONING_EFFORT", "fast_reasoning_effort")
    # Gemini flash latency measured 3–28s for the same small call under load.
    LLM_TIMEOUT_S: float = float(os.environ.get("LLM_TIMEOUT_S", "30"))
    # Router and verifier have deterministic fallbacks (rules / ship
    # unverified), so they get a tight deadline and no model fallback: under
    # load, two 30s timeouts on an optional step doubled answer latency.
    LLM_FAST_TIMEOUT_S: float = float(os.environ.get("LLM_FAST_TIMEOUT_S", "10"))

    # On a 429 from every model, wait the provider's suggested retry delay (if at most
    # this long) and retry once. Short for users; evals raise it to pace
    # themselves against the free tier's tokens-per-minute limit.
    LLM_MAX_RATE_LIMIT_WAIT_S: float = float(os.environ.get("LLM_MAX_RATE_LIMIT_WAIT_S", "8"))

    # Per-request budget. Multi-agent pipelines multiply LLM calls; on a
    # rate-limited key an unbounded plan would burn the quota for everyone.
    MAX_LLM_CALLS_PER_REQUEST: int = int(os.environ.get("MAX_LLM_CALLS_PER_REQUEST", "5"))
    MAX_TOKENS_PER_REQUEST: int = int(os.environ.get("MAX_TOKENS_PER_REQUEST", "16000"))

    # Retrieval policy (tuned with evals/run_eval.py — see docs/EVALUATION.md)
    DEFAULT_TOP_K: int = int(os.environ.get("DEFAULT_TOP_K", "8"))
    RERANKER_ENABLED: bool = _bool("RERANKER_ENABLED", False)
    INCLUDE_SHORT_REVIEWS: bool = _bool("INCLUDE_SHORT_REVIEWS", False)

    # Agent pipeline
    VERIFIER_ENABLED: bool = _bool("VERIFIER_ENABLED", True)

    # Abuse protection
    RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20"))
    ANSWER_CACHE_SIZE: int = int(os.environ.get("ANSWER_CACHE_SIZE", "256"))

    # CORS — the deployed frontend origin(s), comma-separated
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]


settings = Settings()
