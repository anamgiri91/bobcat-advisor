"""
chunker.py
==========
Splits raw source files into embeddable chunks.

Each chunk_*() function handles one source type (rmp, coursicle,
catalog, reddit) and yields dicts with keys: id, text, metadata.

FIXES APPLIED
-------------
Fix 1: Review chunk text now stores only the review body.
  Previously _review_chunk() prepended a pipe-delimited metadata header:
    "Professor: Lee Koh | Course: CS2318 | Source: Coursicle\n\n<body>"
  That header was already captured in metadata, so storing it in text
  was pure noise. Worse, it polluted the embedding vector — the model
  wasted dimensions on "Professor:", "Course:", pipe characters, and
  source labels instead of encoding the actual review sentiment.
  The practical consequence was that Coursicle chunks (whose raw files
  also include "Professor: X | Course: Y | Year Level: ..." in the text)
  had inflated cosine distances and were dropping out of retrieve_balanced()
  when score_cutoff filtered them, leaving some professors with zero chunks
  in comparison queries even when 39 of their reviews were in the database.

  Fix: text = body only. All metadata fields remain in the metadata dict.
  embed.py now embeds chunk["text"] directly (no get_body() stripping needed).
  Existing chunks.jsonl must be regenerated and the ChromaDB index rebuilt:
    python ingest.py --documents-dir documents --out data/chunks.jsonl
    rm -rf data/chroma_db
    python embed.py --chunks data/chunks.jsonl --db data/chroma_db

Fix 9: Added Pass 5 — course normalisation.
  The same course appeared as both "CS 3358" (with space) and "CS3358"
  (no space) in chunk metadata, depending on the source file format.
  The retrieve.py $or filter handled this at query time, but it is
  cleaner and safer to normalise at ingest time so the stored data is
  consistent. All course codes are now stored as "CS3358" (no space).
  This also simplifies _build_course_filter — the no-space form and the
  HONORSCS form are still included in the $or for safety, but "CS 3358"
  variants will no longer appear in new ingests.

Each pass returns the (possibly modified) chunk list and writes a
summary into the shared `report` dict.

No file I/O happens here. This module is purely in-memory transforms.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable, Iterable


# ingest.py imports these two names
SUBDIR_STRATEGY: dict[str, str] = {
    "official": "catalog",
    "catalog": "catalog",
    "rmp": "rmp_review",
    "coursicle": "coursicle_review",
    "reddit": "reddit",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace").strip()


def _normalise_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _make_id(text: str) -> str:
    normalised = _normalise_ws(text)
    return hashlib.md5(normalised.encode("utf-8")).hexdigest()


def _clean_value(value: str | None) -> str:
    if value is None:
        return ""
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _canonical_course(course: str | None) -> str:
    course = _clean_value(course)
    if not course:
        return ""

    # CS 1428, CS1428, cs 1428 -> CS1428
    m = re.search(r"\bCS\s*(\d{4}[A-Z]?)\b", course, flags=re.IGNORECASE)
    if m:
        return f"CS{m.group(1).upper()}"

    # HONORS CS 1428 -> HONORSCS1428
    m = re.search(r"\bHONORS\s*CS\s*(\d{4}[A-Z]?)\b", course, flags=re.IGNORECASE)
    if m:
        return f"HONORSCS{m.group(1).upper()}"

    # Bare 4-digit course number
    m = re.fullmatch(r"\d{4}[A-Z]?", course, flags=re.IGNORECASE)
    if m:
        return course.upper()

    return course.upper()


def _infer_course_from_filename(path: Path) -> str:
    stem = path.stem.replace("_", " ").replace("-", " ")
    return _canonical_course(stem)


def _infer_professor_from_filename(path: Path) -> str:
    stem = path.stem.replace("_", " ").replace("-", " ")

    stem = re.sub(r"\bCS\s*\d{4}[A-Z]?\b", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\b\d{4}[A-Z]?\b", "", stem, flags=re.IGNORECASE)
    stem = re.sub(
        r"\b(rmp|coursicle|reviews?|professor|course)\b",
        "",
        stem,
        flags=re.IGNORECASE,
    )

    stem = re.sub(r"\s+", " ", stem).strip()
    return stem.title() if stem else ""


def _split_review_blocks(text: str) -> list[str]:
    text = _normalise_ws(text)
    if not text:
        return []

    # Best case: every review starts with Professor:
    starts = [m.start() for m in re.finditer(r"(?im)^\s*Professor\s*:", text)]
    if starts:
        starts.append(len(text))
        return [
            text[starts[i]:starts[i + 1]].strip()
            for i in range(len(starts) - 1)
        ]

    # Separator fallback
    pieces = re.split(r"(?m)^\s*(?:-{3,}|={3,}|\*{3,})\s*$", text)
    pieces = [p.strip() for p in pieces if p.strip()]
    if len(pieces) > 1:
        return pieces

    # Blank-line fallback
    pieces = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return pieces if pieces else [text]


def _parse_header(block: str) -> tuple[dict[str, str], str]:
    parts = block.split("\n\n", 1)
    header = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""

    fields: dict[str, str] = {}

    # Pipe-style header:
    # Professor: Lee Koh | Course: CS 2318 | Grade: B
    for item in re.split(r"\s*\|\s*", header):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        fields[key] = _clean_value(value)

    # Multi-line key/value header fallback
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        fields.setdefault(key, _clean_value(value))

    if not body:
        body = re.sub(
            r"(?im)^\s*"
            r"(Professor|Course|Quality|Difficulty|Grade|Date|Source|"
            r"Year Level|Major|For Credit|Attendance)\s*:.*$",
            "",
            block,
        )
        body = _normalise_ws(body)
        if not body:
            body = block.strip()

    return fields, body


def _review_chunk(block: str, path: Path, source_dir: str) -> dict:
    fields, body = _parse_header(block)

    professor = (
        fields.get("professor")
        or fields.get("instructor")
        or _infer_professor_from_filename(path)
    )

    course = (
        fields.get("course")
        or fields.get("class")
        or _infer_course_from_filename(path)
    )

    # Fix 1: store only the review body as chunk text.
    # The pipe-delimited header ("Professor: X | Course: Y | Source: Z")
    # was redundant — every field is already in metadata — and polluted
    # the embedding vector with format noise instead of review content.
    # embed.py embeds chunk["text"] directly now that the header is gone.
    #
    # Also strip any trailing "----------" separator that _split_review_blocks
    # may have included at the end of the body when splitting by Professor: markers.
    body = re.sub(r"\n*-{3,}\s*$", "", body).strip()
    text = _normalise_ws(body)
    metadata = {
        "chunk_type": "review",
        "source_dir": source_dir,
        "source": source_dir,
        "source_file": path.name,
        "professor": _clean_value(professor) or "unknown",
        "course": _canonical_course(course) or "unknown",
        "date": _clean_value(fields.get("date")),
        "grade": _clean_value(fields.get("grade")),
        "quality": _clean_value(fields.get("quality") or fields.get("rating")),
        "difficulty": _clean_value(fields.get("difficulty")),
        "year_level": _clean_value(fields.get("year_level")),
        "major": _clean_value(fields.get("major")),
    }

    return {
        "id": _make_id(text),
        "text": text,
        "metadata": metadata,
    }


def chunk_rmp_review(path: Path, source_dir: str = "rmp") -> Iterable[dict]:
    text = _read_text(path)
    for block in _split_review_blocks(text):
        yield _review_chunk(block, path, "rmp")


def chunk_coursicle_review(path: Path, source_dir: str = "coursicle") -> Iterable[dict]:
    text = _read_text(path)
    for block in _split_review_blocks(text):
        yield _review_chunk(block, path, "coursicle")


def _split_catalog_entries(text: str) -> list[str]:
    text = _normalise_ws(text)
    if not text:
        return []

    starts = [
        m.start()
        for m in re.finditer(
            r"(?im)^\s*(?:HONORS\s*)?CS\s*\d{4}[A-Z]?\b",
            text,
        )
    ]

    if len(starts) > 1:
        starts.append(len(text))
        return [
            text[starts[i]:starts[i + 1]].strip()
            for i in range(len(starts) - 1)
        ]

    return [text]


def _catalog_course(entry: str, path: Path) -> str:
    m = re.search(
        r"\b(?:HONORS\s*)?CS\s*(\d{4}[A-Z]?)\b",
        entry,
        flags=re.IGNORECASE,
    )

    if m:
        prefix = (
            "HONORSCS"
            if re.search(r"\bHONORS\s*CS", entry, flags=re.IGNORECASE)
            else "CS"
        )
        return f"{prefix}{m.group(1).upper()}"

    return _infer_course_from_filename(path) or "unknown"


def chunk_catalog(path: Path, source_dir: str = "official") -> Iterable[dict]:
    text = _read_text(path)

    for entry in _split_catalog_entries(text):
        course = _catalog_course(entry, path)
        chunk_text = _normalise_ws(entry)

        yield {
            "id": _make_id(chunk_text),
            "text": chunk_text,
            "metadata": {
                "chunk_type": "catalog",
                "source_dir": "official",
                "source": "official",
                "source_file": path.name,
                "professor": "",
                "course": course,
                "date": "unknown",
            },
        }


def _split_reddit_entries(text: str) -> list[str]:
    text = _normalise_ws(text)
    if not text:
        return []

    pieces = re.split(r"(?m)^\s*(?:-{3,}|={3,}|\*{3,})\s*$", text)
    pieces = [p.strip() for p in pieces if p.strip()]

    if len(pieces) > 1:
        return pieces

    return [text]


def chunk_reddit(path: Path, source_dir: str = "reddit") -> Iterable[dict]:
    text = _read_text(path)

    for i, entry in enumerate(_split_reddit_entries(text), 1):
        course = _catalog_course(entry, path)
        chunk_text = _normalise_ws(entry)

        yield {
            "id": _make_id(f"reddit:{path.name}:{i}\n{chunk_text}"),
            "text": chunk_text,
            "metadata": {
                "chunk_type": "reddit",
                "source_dir": "reddit",
                "source": "reddit",
                "source_file": path.name,
                "professor": "",
                "course": course,
                "date": "unknown",
            },
        }


CHUNKER_MAP: dict[str, Callable[[Path, str], Iterable[dict]]] = {
    "catalog": chunk_catalog,
    "rmp_review": chunk_rmp_review,
    "coursicle_review": chunk_coursicle_review,
    "reddit": chunk_reddit,
}