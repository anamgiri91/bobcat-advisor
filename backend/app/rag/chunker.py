"""
chunker.py
==========
Splits the official course catalog (documents/official/*.txt) into one
chunk per course entry. Each chunk is a dict with keys: id, text, metadata.

Only the official catalog is ingested. Chunk ids are content hashes, so an
unchanged entry keeps its id and embed.py only embeds what changed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from pathlib import Path

# ingest.py imports these two names
SUBDIR_STRATEGY: dict[str, str] = {
    "official": "catalog",
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
                "course": course,
                "date": "unknown",
            },
        }


CHUNKER_MAP: dict[str, Callable[[Path, str], Iterable[dict]]] = {
    "catalog": chunk_catalog,
}
