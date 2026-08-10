"""
retrieve.py
===========
Semantic search layer. Given a natural-language query, returns the
top-k most relevant chunks from ChromaDB along with their full metadata.

Three retrieval modes
---------------------
retrieve()            Standard semantic search, top-k=8.
                      Applies professor filter if a professor name is
                      detected in the query.
                      Applies course filter if a course number is detected.

retrieve_balanced()   For comparison queries ("best professor for X").
                      Fetches top-30, caps at 4 per professor, drops
                      chunks with distance > SCORE_THRESHOLD.

_get_catalog_chunk()  For catalog-comparison queries ("how does feedback
                      differ from the official description"), always
                      includes the catalog entry for the queried course
                      regardless of its distance score.

FIXES APPLIED
-------------
Fix A — Professor name extraction:
  Queries like "Burtscher's workload" extract "Martin Burtscher" and add
  a ChromaDB metadata filter, preventing chunks about other professors
  from appearing in results about a specific one.

Fix B — Course number regex:
  Old: r'\\b(\\d{4})\\b' — failed on "CS3358" because there is no word
  boundary between S and 3.
  New: r'[Cc][Ss]\\s*(\\d{4})' — requires CS prefix and handles CS3358
  and CS 3358.

Fix C — Score threshold in retrieve_balanced:
  Drops chunks with distance > SCORE_THRESHOLD before the per-professor cap,
  preventing weak matches from consuming retrieval slots.

Fix D — Safe comparison query detection:
  COMPARISON_SIGNALS includes short words like "or".
  The old code used:
      return any(signal in q for signal in COMPARISON_SIGNALS)

  That was dangerous because "or" matched inside words like "professor".
  This version uses word-boundary regex for single-word signals, so "or"
  only matches as its own word.

Fix E — No professor filter for comparison queries:
  For a query like "Compare Jill Seaman and Husain Gholoom for CS1428",
  retrieval should NOT filter to only Jill Seaman or only Husain Gholoom.
  _extract_professor() now returns None for comparison queries so
  retrieve_balanced() can collect chunks from multiple professors.

Fix F — _collection singleton cached by db_path:
  Previously a single global _collection was cached regardless of which
  db_path was passed. Calling retrieve() with two different db_path values
  would silently use the first path's collection for both calls. Now cached
  in a dict keyed by db_path string.

Fix G — Pin named professors in retrieve_balanced() (defensive retrieval):
  Mirrors _get_catalog_chunk(): if a professor is explicitly named in a
  comparison query, their best chunks are always included regardless of
  the score_cutoff filter. This handles the case where Coursicle chunk
  embeddings score just above SCORE_THRESHOLD and get dropped, leaving a
  named professor with zero results even though 39 of their reviews exist.
  _extract_all_professors() scans the query for ALL named professors.
  _get_named_professor_chunks() bypasses score_cutoff and fetches the top
  cap_per_prof chunks for each named professor using a direct metadata
  filter, then merges them into the balanced pool (deduped by chunk id).

Fix H — Chunk text is now body-only (see chunker.py Fix 1):
  embed.py previously called get_body() to strip the pipe-delimited header
  before encoding. That header has been removed from chunk text at ingest
  time, so retrieve() returns clean body text and generate.py no longer
  needs to split on "\\n\\n" to find the body.

Dependencies:
    pip install chromadb sentence-transformers
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TypedDict

import chromadb
from sentence_transformers import SentenceTransformer


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COLLECTION_NAME = "txstate_cs_reviews"
EMBED_MODEL = "all-MiniLM-L6-v2"

# Standard retrieval result count.
DEFAULT_TOP_K = 8

# For comparison questions, fetch a wider pool first, then balance results
# so one professor with many reviews does not dominate the answer.
COMPARISON_TOP_K = 30
CAP_PER_PROF = 4

# ChromaDB cosine distance threshold.
# Lower = closer/more relevant.
# This cutoff is only used in retrieve_balanced().
SCORE_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Comparison detection
# ---------------------------------------------------------------------------

# This is the single source of truth for comparison detection.
# generate.py imports _is_comparison_query from this file, so do not create
# another comparison signal list inside generate.py.
COMPARISON_SIGNALS = [
    "best",
    "better",
    "worst",
    "compare",
    "recommend",
    "which professor",
    "who should",
    "vs",
    "versus",
    "difference between",
    "different from",
    "differ from",
    "official description",
    "course description",
    "catalog",
    "should i take",

    # Natural student phrasing:
    "or",             # "Is Koh or Lehr better?"
    "better than",    # "Is Seaman better than Gholoom?"
    "instead of",     # "Should I take Li instead of Koh?"
    "alternative",    # "Any alternative to Gholoom?"
    "who is better",  # "Who is better for data structures?"
    "switch",         # "Should I switch from Koh to Seaman?"
    "between",        # "Choosing between Seaman and Gholoom"
    "over",           # "Seaman over Gholoom?"
]


# ---------------------------------------------------------------------------
# Professor canonicalization
# ---------------------------------------------------------------------------

# Map query keywords to the canonical professor names stored in ChromaDB.
# Longer keys are checked first so "Martin Burtscher" matches before
# "Burtscher", and "xiaomin li" matches before any short token.
PROF_CANONICAL: dict[str, str] = {
    "komogortsev": "Oleg Komogortsev",
    "oleg komogortsev": "Oleg Komogortsev",

    "burtscher": "Martin Burtscher",
    "martin burtscher": "Martin Burtscher",

    "gholoom": "Husain Gholoom",
    "husain gholoom": "Husain Gholoom",

    "bhandari": "Keshav Bhandari",
    "keshav bhandari": "Keshav Bhandari",

    "guirguis": "Mina Guirguis",
    "mina guirguis": "Mina Guirguis",

    "seaman": "Jill Seaman",
    "jill seaman": "Jill Seaman",

    "vargas": "Edwin Vargas",
    "edwin vargas": "Edwin Vargas",

    "qasem": "Apan Qasem",
    "apan qasem": "Apan Qasem",

    "lehr": "Ted Lehr",
    "ted lehr": "Ted Lehr",

    "xiaomin li": "Xiaomin Li",

    "koh": "Lee Koh",
    "lee koh": "Lee Koh",
}


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_model: SentenceTransformer | None = None

# Cache collections by db_path so tests/apps using different DB folders do not
# accidentally reuse the first collection loaded.
_collections: dict[str, chromadb.Collection] = {}


def _get_model() -> SentenceTransformer:
    """Load the embedding model once and reuse it across retrieval calls."""
    global _model

    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)

    return _model


def _get_collection(db_path: str = "data/chroma_db") -> chromadb.Collection:
    """
    Return the ChromaDB collection for a given db_path.

    Important:
    The cache key is db_path, not just one global collection. This prevents
    test runs from accidentally using the wrong database.
    """
    if db_path not in _collections:
        client = chromadb.PersistentClient(path=db_path)
        _collections[db_path] = client.get_collection(name=COLLECTION_NAME)

    return _collections[db_path]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class RetrievedChunk(TypedDict):
    id: str
    text: str
    metadata: dict
    score: float


# ---------------------------------------------------------------------------
# Query analysis helpers
# ---------------------------------------------------------------------------

def _is_comparison_query(query: str) -> bool:
    """
    Return True if the query is asking to compare professors/courses.

    Important fix:
    Single-word comparison signals need word boundaries.

    Example problem:
      "professor" contains "or"

    Old behavior:
      "or" in "professor" -> True

    New behavior:
      re.search(r"\\bor\\b", "professor") -> False
      re.search(r"\\bor\\b", "Koh or Lehr") -> True
    """
    q = query.lower()

    for signal in COMPARISON_SIGNALS:
        # Multi-word signals can safely use substring matching.
        # Example: "difference between", "should i take"
        if " " in signal:
            if signal in q:
                return True

        # Single-word signals need word boundaries.
        # This prevents "or" from matching inside "professor".
        else:
            if re.search(rf"\b{re.escape(signal)}\b", q):
                return True

    return False


def _extract_professor(query: str) -> str | None:
    """
    Detect a professor name in the query and return the canonical full name.

    Important fix:
    Do NOT return a professor filter for comparison queries.

    Why:
      "Compare Jill Seaman and Husain Gholoom for CS1428"

    If this function returned "Jill Seaman", retrieve() would only search
    Jill Seaman chunks and accidentally exclude Husain Gholoom. Returning
    None lets retrieve_balanced() gather multiple professors.
    """
    if _is_comparison_query(query):
        return None

    q_lower = query.lower()

    for key in sorted(PROF_CANONICAL.keys(), key=len, reverse=True):
        if key in q_lower:
            return PROF_CANONICAL[key]

    return None


def _extract_all_professors(query: str) -> list[str]:
    """
    Return canonical names for ALL professors mentioned in a query.

    Unlike _extract_professor(), which returns None for comparison queries
    (to avoid filtering to a single professor), this function is used by
    retrieve_balanced() to identify every named professor so their chunks
    can be pinned regardless of score_cutoff.

    Example:
      "How do Jill Seaman and Husain Gholoom compare for CS1428?"
      → ["Jill Seaman", "Husain Gholoom"]

      "Who is better for CS3358, Koh or Lehr?"
      → ["Lee Koh", "Ted Lehr"]

      "Best professor for CS1428?"   ← no names → []
      → []
    """
    q_lower = query.lower()
    found: list[str] = []
    seen: set[str] = set()

    # Check longest keys first so "martin burtscher" matches before "burtscher"
    for key in sorted(PROF_CANONICAL.keys(), key=len, reverse=True):
        if key in q_lower:
            canonical = PROF_CANONICAL[key]
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)

    return found


def _extract_course_number(query: str) -> str | None:
    """
    Extract the 4-digit CS course number from a query.

    Handles:
      CS3358
      CS 3358
      cs2308
      cs 1428

    Does not match bare numbers without a CS prefix, which avoids accidental
    matches on years, addresses, ratings, etc.
    """
    match = re.search(r"[Cc][Ss]\s*(\d{4})", query)
    return match.group(1) if match else None


def _build_course_filter(course_digits: str) -> dict:
    """
    Match course number variants in ChromaDB metadata.

    Your data may contain:
      CS3358
      CS 3358
      3358
      HONORSCS3358

    New ingestion should normalize to CS3358, but keeping variants here makes
    retrieval backward-compatible with older indexed data.
    """
    return {
        "$or": [
            {"course": f"CS {course_digits}"},
            {"course": f"CS{course_digits}"},
            {"course": course_digits},
            {"course": f"HONORSCS{course_digits}"},
        ]
    }


def _build_combined_filter(
    prof: str | None,
    course_digits: str | None,
    source: str | None = None,
) -> dict | None:
    """
    Combine professor, course, and source filters.

    source values match the source_dir field stored in metadata:
      "rmp", "coursicle", "reddit", "official"
    """
    conditions = []

    if prof:
        conditions.append({"professor": prof})

    if course_digits:
        conditions.append(_build_course_filter(course_digits))

    if source:
        conditions.append({"source_dir": source})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ---------------------------------------------------------------------------
# Standard retrieval
# ---------------------------------------------------------------------------

def retrieve(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    db_path: str = "data/chroma_db",
    source_filter: str | None = None,
) -> list[RetrievedChunk]:
    """
    Embed the query and return top-k most similar chunks.

    Automatically applies:
      - professor filter for single-professor queries
      - course filter when a CS course number is detected
      - combined AND filter when both professor and course are present
      - optional source_filter ("rmp", "coursicle", "reddit", "official")

    For comparison queries, _extract_professor() returns None, so retrieval
    does not accidentally filter down to only one professor.
    """
    if not query.strip():
        return []

    model = _get_model()
    collection = _get_collection(db_path)

    prof = _extract_professor(query)
    course_digits = _extract_course_number(query)
    where_filter = _build_combined_filter(prof, course_digits, source_filter)

    query_embedding = model.encode(query).tolist()

    query_kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }

    if where_filter:
        query_kwargs["where"] = where_filter

    results = collection.query(**query_kwargs)

    return [
        RetrievedChunk(
            id=results["ids"][0][i],
            text=doc,
            metadata=meta,
            score=dist,
        )
        for i, (doc, meta, dist) in enumerate(
            zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        )
    ]


def _get_named_professor_chunks(
    prof: str,
    course_digits: str | None,
    db_path: str,
    n: int = CAP_PER_PROF,
) -> list[RetrievedChunk]:
    """
    Fetch the top-n chunks for a specifically named professor, bypassing
    the score_cutoff filter used in retrieve_balanced().

    This is Fix G — the defensive counterpart to _get_catalog_chunk().
    When a student asks "How do Seaman and Gholoom compare?", both names
    are explicit and we must return evidence for both. If one professor's
    chunks happen to score just above SCORE_THRESHOLD (e.g. because their
    Coursicle reviews contained noisy metadata headers before Fix 1), the
    score filter would silently drop them and the LLM would have nothing
    to say about that professor.

    By querying with a metadata filter on professor (and optionally course),
    we guarantee representation for every named professor regardless of
    cosine distance, then let the LLM weigh review quality.

    Parameters
    ----------
    prof         : canonical professor name (from PROF_CANONICAL)
    course_digits: 4-digit course number string, or None
    db_path      : ChromaDB directory
    n            : max chunks to return (default = CAP_PER_PROF)
    """
    collection = _get_collection(db_path)
    where = _build_combined_filter(prof, course_digits)

    # Use the professor name as the query so the most topically relevant
    # chunks for that professor surface first.
    query_embedding = _get_model().encode(prof).tolist()

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"][0]:
            return []

        return [
            RetrievedChunk(
                id=results["ids"][0][i],
                text=doc,
                metadata=meta,
                score=dist,
            )
            for i, (doc, meta, dist) in enumerate(
                zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            )
        ]

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Balanced retrieval for comparison queries
# ---------------------------------------------------------------------------

def retrieve_balanced(
    query: str,
    db_path: str = "data/chroma_db",
    fetch_top_k: int = COMPARISON_TOP_K,
    cap_per_prof: int = CAP_PER_PROF,
    score_cutoff: float = SCORE_THRESHOLD,
    source_filter: str | None = None,
) -> list[RetrievedChunk]:
    """
    Fetch a wide pool, then balance results per professor.

    This is used for comparison questions like:
      "How do Jill Seaman and Husain Gholoom compare for CS1428?"
      "Who is better for CS3358, Koh or Lehr?"
      "Best professor for CS1428?"

    Steps:
      1. Fetch fetch_top_k chunks.
      2. Use course filter if the query mentions a course.
      3. Do NOT apply a single-professor filter because comparison queries
         need evidence from multiple professors.
      4. Drop chunks with distance > score_cutoff.
      5. Group by professor.
      6. Keep at most cap_per_prof chunks per professor.
      7. Pin chunks for any explicitly named professor missing from the pool
         (Fix G — bypasses score_cutoff for named professors).
      8. Pin the catalog chunk if a course number appears.
      9. Append up to 2 reddit chunks.
    """
    pool = retrieve(query, top_k=fetch_top_k, db_path=db_path, source_filter=source_filter)

    if not pool:
        return []

    # Drop weak matches before balancing so bad chunks do not consume slots.
    pool = [chunk for chunk in pool if chunk["score"] <= score_cutoff]

    by_prof: dict[str, list[RetrievedChunk]] = defaultdict(list)
    no_prof: list[RetrievedChunk] = []

    for chunk in pool:
        prof = chunk["metadata"].get("professor", "")

        if prof:
            by_prof[prof].append(chunk)
        else:
            no_prof.append(chunk)

    # Keep only the best chunks for each professor.
    # ChromaDB results are already ordered by distance, so the first chunks
    # in each group are the strongest matches.
    balanced: list[RetrievedChunk] = []

    for prof_chunks in by_prof.values():
        balanced.extend(prof_chunks[:cap_per_prof])

    # Fix G: Pin chunks for every explicitly named professor.
    # If "Gholoom" is in the query but all his chunks scored above
    # score_cutoff and got filtered out, _get_named_professor_chunks()
    # fetches his best chunks directly by metadata filter, bypassing the
    # distance threshold. We only do this for professors with zero
    # representation in the balanced pool so far.
    course_digits = _extract_course_number(query)
    named_profs = _extract_all_professors(query)
    already_represented = {
        chunk["metadata"].get("professor") for chunk in balanced
    }
    seen_ids = {chunk["id"] for chunk in balanced}

    for prof in named_profs:
        if prof not in already_represented:
            pinned = _get_named_professor_chunks(
                prof, course_digits, db_path, n=cap_per_prof
            )
            for chunk in pinned:
                if chunk["id"] not in seen_ids:
                    balanced.append(chunk)
                    seen_ids.add(chunk["id"])

    # Always include official catalog context for course-specific comparisons.
    if course_digits:
        catalog_chunk = _get_catalog_chunk(course_digits, db_path)

        if catalog_chunk and catalog_chunk["id"] not in seen_ids:
            balanced.append(catalog_chunk)
            seen_ids.add(catalog_chunk["id"])

    # Add up to 2 reddit chunks if they appeared in the retrieval pool.
    reddit_chunks = [
        chunk for chunk in no_prof
        if chunk["metadata"].get("chunk_type") == "reddit"
    ]

    for chunk in reddit_chunks[:2]:
        if chunk["id"] not in seen_ids:
            balanced.append(chunk)
            seen_ids.add(chunk["id"])

    return balanced


def _get_catalog_chunk(
    course_digits: str,
    db_path: str = "data/chroma_db",
) -> RetrievedChunk | None:
    """
    Directly fetch the official catalog entry for a course by metadata filter.

    This guarantees the catalog description appears for questions like:
      "How does student feedback about CS1428 differ from the official
       course description?"

    Catalog chunks can have higher semantic distance because official catalog
    language is formal, while student questions are casual. So this function
    pins the catalog chunk by metadata instead of relying only on similarity.
    """
    collection = _get_collection(db_path)
    course_filter = _build_course_filter(course_digits)

    try:
        results = collection.query(
            query_embeddings=[
                _get_model().encode("official course catalog description").tolist()
            ],
            n_results=3,
            where={
                "$and": [
                    {"chunk_type": "catalog"},
                    course_filter,
                ]
            },
            include=["documents", "metadatas", "distances"],
        )

        if results["ids"][0]:
            return RetrievedChunk(
                id=results["ids"][0][0],
                text=results["documents"][0][0],
                metadata=results["metadatas"][0][0],
                score=results["distances"][0][0],
            )

    except Exception:
        # If catalog lookup fails, normal review retrieval should still work.
        pass

    return None