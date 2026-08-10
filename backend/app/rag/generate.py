"""
generate.py
===========
Generation layer for the Bobcat RAG pipeline.

Takes retrieved chunks and a user query, builds a grounded prompt,
calls the Groq API, and returns the answer + programmatically
extracted source list.

KEY DESIGN DECISIONS
--------------------
1. Grounding is enforced in the system prompt — the LLM is explicitly
   told it may ONLY use the provided context and must refuse to answer
   if the context is insufficient.

2. Source attribution is PROGRAMMATIC — sources are extracted from
   chunk metadata BEFORE the LLM is called, not extracted from the
   LLM output. This guarantees sources are always shown and always
   accurate, regardless of whether the model mentions them.

3. The LLM never sees the source list we build — it only sees the
   context text. This prevents the model from fabricating or
   reordering sources.

4. For comparison queries ("best professor for X"), the context is
   grouped by professor so the LLM sees all reviews for each professor
   together, rather than interleaved chunks that make comparison hard.

FIXES APPLIED
-------------
Fix 1: Removed duplicate COMPARISON_SIGNALS list and _is_comparison_query()
       function. Both now live exclusively in retrieve.py — generate.py
       imports them from there. Previously the two lists were out of sync
       (generate.py had "difference between professors"; retrieve.py had
       "difference between" + "should i take"), causing routing mismatches.

Fix 2: _build_grouped_context() source_label now checks ALL chunks for a
       professor, not just the first. If a professor has chunks from both
       rmp and coursicle, the label correctly reads "RateMyProfessors +
       Coursicle" instead of whichever source happened to appear first.

Setup:
    pip install groq python-dotenv
    Add GROQ_API_KEY=your_key to your .env file
"""

from __future__ import annotations

import os
from collections import defaultdict
from dotenv import load_dotenv

# _is_comparison_query is the single source of truth in retrieve.py.
# It must NOT be re-defined here — fix for the duplicate bug.
from .retrieve import RetrievedChunk, _is_comparison_query

load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL      = "llama-3.3-70b-versatile"
MAX_TOKENS = 1024
TOP_K      = 5

NO_INFO_RESPONSE = (
    "I couldn't find enough information in the retrieved sources "
    "to answer that question."
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are TXST Professor Advisor, a helpful assistant for \
Texas State University CS students choosing courses and professors.

STRICT GROUNDING RULES:
1. Answer ONLY using information from the CONTEXT provided. Never use prior knowledge.
2. If context lacks enough information, say exactly:
   "I couldn't find enough information in the retrieved sources to answer that question."
3. Never invent, guess, or assume professor traits, grading policies, or difficulty.
4. If reviews conflict (one says easy, another hard), acknowledge both views.
5. Do not use phrases like "generally" or "typically" — cite specific review content.

RESPONSE QUALITY RULES:
6. For "best professor" or comparison questions:
   - Summarise what reviewers say about EACH professor separately
   - Cover: teaching style, exam difficulty, workload, grading, office hours
   - Note the number of reviews you are drawing from
   - Give a concrete recommendation at the end based ONLY on the review patterns
   - Do NOT just say "reviews are mixed" — that is unhelpful. Synthesise the pattern.
7. Keep answers focused and specific. Name professors directly.
8. Use a friendly, direct tone suited for a student making a course decision."""


# ---------------------------------------------------------------------------
# Context builder  (groups by professor for comparison queries)
# ---------------------------------------------------------------------------

def _build_context(chunks: list[RetrievedChunk], query: str) -> str:
    """
    Format retrieved chunks into a context block for the prompt.

    For comparison queries ("best professor for CS 1428"), groups chunks
    by professor so the LLM sees all reviews for each professor together.
    This makes synthesising across professors much easier for the model.

    For non-comparison queries, presents chunks in retrieval order.
    """
    if not chunks:
        return "No context retrieved."

    if _is_comparison_query(query):
        return _build_grouped_context(chunks)
    else:
        return _build_flat_context(chunks)


def _build_flat_context(chunks: list[RetrievedChunk]) -> str:
    """Standard numbered list of chunks in retrieval order."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        meta       = chunk["metadata"]
        source_dir = meta.get("source_dir", "unknown")
        professor  = meta.get("professor", "")
        course     = meta.get("course", "")

        if source_dir == "rmp":
            label = f"[RateMyProfessors — {professor}, {course}]"
        elif source_dir == "coursicle":
            label = f"[Coursicle — {professor}, {course}]"
        elif source_dir == "reddit":
            label = "[Reddit r/txstate]"
        elif source_dir == "official":
            label = f"[TXST Catalog — {course}]"
        else:
            label = "[Unknown source]"

        parts.append(f"--- Review {i} {label} ---\n{chunk['text']}")

    return "\n\n".join(parts)


def _build_grouped_context(chunks: list[RetrievedChunk]) -> str:
    """
    Group chunks by professor for comparison queries.

    Instead of:
      Review 1: Seaman chunk
      Review 2: Gholoom chunk
      Review 3: Seaman chunk
      ...

    Produces:
      === PROFESSOR: Jill Seaman (3 reviews) ===
      Review 1: ...
      Review 2: ...

      === PROFESSOR: Husain Gholoom (2 reviews) ===
      Review 3: ...
      ...

    This grouping dramatically improves LLM synthesis quality because
    the model can process all evidence for one professor before moving
    to the next, rather than mentally interleaving competing voices.

    Fix 2: source_label is now derived from ALL chunks for a professor,
    not just the first. A professor whose chunks come from both rmp and
    coursicle gets the label "RateMyProfessors + Coursicle" rather than
    whichever source happened to appear first in the result list.
    """
    # Group by professor, preserving insertion order
    by_prof: dict[str, list[RetrievedChunk]] = defaultdict(list)
    non_review: list[RetrievedChunk] = []

    for chunk in chunks:
        prof = chunk["metadata"].get("professor", "")
        if prof:
            by_prof[prof].append(chunk)
        else:
            non_review.append(chunk)

    parts = []
    review_num = 1

    for prof, prof_chunks in by_prof.items():
        # Fix 2: check all source_dirs for this professor, not just the first chunk
        source_dirs = {c["metadata"].get("source_dir", "") for c in prof_chunks}
        if source_dirs == {"rmp"}:
            source_label = "RateMyProfessors"
        elif source_dirs == {"coursicle"}:
            source_label = "Coursicle"
        else:
            source_label = "RateMyProfessors + Coursicle"

        parts.append(
            f"{'='*60}\n"
            f"PROFESSOR: {prof}  ({len(prof_chunks)} reviews from {source_label})\n"
            f"{'='*60}"
        )

        for chunk in prof_chunks:
            # Show only the body (not the pipe-delimited header) to avoid
            # the LLM confusing metadata fields with review content
            body_parts = chunk["text"].split("\n\n", 1)
            body = body_parts[1].strip() if len(body_parts) > 1 else chunk["text"]

            meta   = chunk["metadata"]
            course = meta.get("course", "")
            date   = meta.get("date", "")
            grade  = meta.get("grade", "")

            detail_parts = []
            if course:
                detail_parts.append(f"Course: {course}")
            if date and date != "unknown":
                detail_parts.append(f"Date: {date}")
            if grade and grade not in ("N/A", ""):
                detail_parts.append(f"Grade received: {grade}")
            detail = " | ".join(detail_parts)

            parts.append(f"  Review {review_num} [{detail}]:\n  {body}")
            review_num += 1

        parts.append("")  # blank line between professors

    # Append any non-review chunks (catalog, reddit) at the end
    for chunk in non_review:
        parts.append(f"--- Additional context ---\n{chunk['text']}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Source formatting  (programmatic — not LLM-generated)
# ---------------------------------------------------------------------------

def format_sources(chunks: list[RetrievedChunk]) -> str:
    """
    Build a formatted source list directly from chunk metadata.
    Runs BEFORE the LLM call — the LLM cannot affect this output.
    """
    seen:    set[str]  = set()
    sources: list[str] = []

    for chunk in chunks:
        meta        = chunk["metadata"]
        source_dir  = meta.get("source_dir", "unknown")
        professor   = meta.get("professor", "")
        course      = meta.get("course", "")
        date        = meta.get("date", "")
        source_file = meta.get("source_file", "")

        if source_dir == "rmp":
            date_str = f", {date}" if date and date != "unknown" else ""
            label = f"RateMyProfessors — {professor} · {course}{date_str}"
        elif source_dir == "coursicle":
            year  = meta.get("year_level", "")
            major = meta.get("major", "")
            extra = f" ({year}, {major})" if year and major else ""
            label = f"Coursicle — {professor} · {course}{extra}"
        elif source_dir == "reddit":
            name  = source_file.replace(".txt", "").replace("_", " ").title()
            label = f"Reddit r/txstate — {name}"
        elif source_dir == "official":
            label = f"TXST Course Catalog — {course}"
        else:
            label = f"Unknown source — {source_file}"

        if label not in seen:
            seen.add(label)
            sources.append(label)

    if not sources:
        return "No sources retrieved."

    return "\n".join(f"{i+1}. {s}" for i, s in enumerate(sources))


def format_sources_list(chunks: list[RetrievedChunk]) -> list[str]:
    """
    Same dedup/labelling logic as format_sources(), but returns a plain
    list of strings instead of a numbered block of text. This is what the
    API/frontend consume — the frontend renders its own numbering/UI.
    """
    text = format_sources(chunks)
    if text == "No sources retrieved.":
        return []
    # Strip the "N. " prefix added by format_sources()
    lines = text.split("\n")
    return [line.split(". ", 1)[1] if ". " in line else line for line in lines]


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_answer(
    query:  str,
    chunks: list[RetrievedChunk],
) -> str:
    """
    Call Groq with the retrieved context and return a grounded answer.
    """
    if not chunks:
        return NO_INFO_RESPONSE

    try:
        from groq import Groq
    except ImportError:
        raise ImportError("groq not installed — run: pip install groq")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY is not set.\n"
            "Add it to your .env file: GROQ_API_KEY=your_key_here\n"
            "Get a free key at: https://console.groq.com"
        )

    client  = Groq(api_key=api_key)
    context = _build_context(chunks, query)

    user_message = f"""CONTEXT:
{context}

QUESTION: {query}

Instructions:
- Answer using ONLY the reviews in the CONTEXT above.
- For "best professor" questions: summarise each professor's reviews separately,
  then give a clear recommendation based on the patterns you see.
- Be specific. Name professors. Quote or paraphrase actual review content.
- If context is insufficient, say exactly:
  "I couldn't find enough information in the retrieved sources to answer that question."
"""

    response = client.chat.completions.create(
        model      = MODEL,
        max_tokens = MAX_TOKENS,
        messages   = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    )

    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Top-level orchestrator  (called by app.py)
# ---------------------------------------------------------------------------

def answer_question(
    query:         str,
    db_path:       str = "data/chroma_db",
    top_k:         int = TOP_K,
    source_filter: str | None = None,
) -> tuple[str, str]:
    """
    Full pipeline: retrieve → format sources → generate answer.
    Sources are built from metadata before the LLM is called.

    source_filter: optional ChromaDB source_dir value to restrict retrieval
      "rmp"      → RateMyProfessors only
      "coursicle" → Coursicle only
      "reddit"   → Reddit only
      "official" → course catalog only
      None       → all sources (default)
    """
    from .retrieve import retrieve, retrieve_balanced

    if _is_comparison_query(query):
        chunks = retrieve_balanced(query, db_path=db_path, source_filter=source_filter)
    else:
        chunks = retrieve(query, top_k=top_k, db_path=db_path, source_filter=source_filter)

    sources = format_sources(chunks)
    answer  = generate_answer(query, chunks)

    return answer, sources


def answer_question_api(
    query:         str,
    db_path:       str = "data/chroma_db",
    top_k:         int = TOP_K,
    source_filter: str | None = None,
) -> dict:
    """
    Same pipeline as answer_question(), but returns a dict shaped for the
    FastAPI layer: a plain source list (not pre-numbered text) plus the
    retrieved chunk count, so the API can log it to Postgres.
    """
    from .retrieve import retrieve, retrieve_balanced

    if _is_comparison_query(query):
        chunks = retrieve_balanced(query, db_path=db_path, source_filter=source_filter)
    else:
        chunks = retrieve(query, top_k=top_k, db_path=db_path, source_filter=source_filter)

    return {
        "answer": generate_answer(query, chunks),
        "sources": format_sources_list(chunks),
        "chunk_count": len(chunks),
    }