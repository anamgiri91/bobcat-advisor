"""
retrieve.py
===========
Public retrieval API, kept stable for scripts and the eval harness.

The implementation now lives in:
  index.py              hybrid dense + BM25 search with RRF and optional rerank
  knowledge/corpus.py   data-derived professor/course registry (replaced the
                        hand-maintained PROF_CANONICAL dict and course regex)

History: the earlier regex-based version (Fixes A–G: professor extraction,
CS-prefixed course regex, score threshold, word-boundary comparison signals,
named-professor pinning) is documented in docs/problems.md. Each of those
fixes is subsumed here: entity extraction is registry-based with fuzzy
matching, comparison retrieval guarantees per-professor quotas by
construction, and the agent router (app/agents/router.py) decides intent
instead of a keyword list.
"""

from __future__ import annotations

import re

from ..config import settings
from ..knowledge.corpus import registry
from .index import RetrievedChunk, SearchFilters, get_index

__all__ = ["RetrievedChunk", "retrieve", "retrieve_balanced", "is_comparison_query"]

_COMPARISON_PATTERNS = [
    r"\b(best|better|worst|easiest|hardest|compare|comparison|versus|vs\.?)\b",
    r"\brecommend (a|which|someone|me)\b",
    r"\bwhich (\w+ )?(professor|prof|one)\b",
    r"\bwho (should|is better|is the best)\b",
    r"\bshould i take\b",
    r"\binstead of\b",
    r"\bdifference between\b",
]


def is_comparison_query(query: str) -> bool:
    """
    Rule-based comparison detection — the router's offline fallback.
    Two named professors is itself a comparison signal ("Koh or Lehr?").
    """
    q = query.lower()
    if len(registry().match_professors(query)) >= 2:
        return True
    return any(re.search(p, q) for p in _COMPARISON_PATTERNS)


def retrieve(
    query: str,
    top_k: int = settings.DEFAULT_TOP_K,
    db_path: str | None = None,
    source_filter: str | None = None,
    **search_kwargs,
) -> list[RetrievedChunk]:
    """Single-entity search: filters on any professor/course named in the query."""
    reg = registry()
    profs = reg.match_professors(query)
    courses = reg.match_courses(query)
    filters = SearchFilters(
        professors=profs[:1] or None,
        courses=courses or None,
        sources=[source_filter] if source_filter else None,
    )
    return get_index(db_path).search(query, k=top_k, filters=filters, **search_kwargs)


def retrieve_balanced(
    query: str,
    db_path: str | None = None,
    source_filter: str | None = None,
    **search_kwargs,
) -> list[RetrievedChunk]:
    """Comparison search: per-professor quota plus the catalog entry."""
    reg = registry()
    profs = reg.match_professors(query) or None
    courses = reg.match_courses(query) or None
    ix = get_index(db_path)
    chunks = ix.balanced(query, profs, courses,
                         sources=[source_filter] if source_filter else None, **search_kwargs)
    for course in courses or []:
        cat = ix.catalog_chunk(course)
        if cat and cat["id"] not in {c["id"] for c in chunks}:
            chunks.append(cat)
    return chunks
