"""
retrieve.py
===========
Public retrieval API, kept stable for scripts and the eval harness. The
implementation lives in index.py (hybrid dense + BM25 search with RRF and
optional rerank) and knowledge/corpus.py (course registry).
"""

from __future__ import annotations

import re

from ..config import settings
from ..knowledge.corpus import registry
from .index import RetrievedChunk, SearchFilters, get_index

__all__ = ["RetrievedChunk", "retrieve", "is_comparison_query"]

_COMPARISON_PATTERNS = [
    r"\b(better|compare|comparison|versus|vs\.?|or)\b",
    r"\bwhich (\w+ )?(one|course|class)\b",
    r"\bshould i take\b",
    r"\binstead of\b",
    r"\bdifference between\b",
]


def is_comparison_query(query: str) -> bool:
    """Two or more courses plus comparison wording ("CS3358 or CS3360 first?")."""
    if len(registry().match_courses(query)) < 2:
        return False
    q = query.lower()
    return any(re.search(p, q) for p in _COMPARISON_PATTERNS)


def retrieve(
    query: str,
    top_k: int = settings.DEFAULT_TOP_K,
    db_path: str | None = None,
    **search_kwargs,
) -> list[RetrievedChunk]:
    """Catalog search, filtered to any course named in the query."""
    courses = registry().match_courses(query)
    filters = SearchFilters(courses=courses or None)
    return get_index(db_path).search(query, k=top_k, filters=filters, **search_kwargs)
