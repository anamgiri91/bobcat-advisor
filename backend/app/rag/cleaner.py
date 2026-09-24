"""
cleaner.py
==========
Responsible for one thing: taking a list of raw chunks produced by
chunker.py and making them safe and balanced for embedding.

Five cleaning passes, applied in order by clean_chunks():

  Pass 1  _pass_junk_filter       Drop chunks that are scraper noise
                                  (junk keywords, or body under 5 words)
  Pass 2  _pass_truncation_flag   Flag chunks that end mid-sentence
  Pass 3  _pass_short_flag        Flag chunks under 50 words
  Pass 4  _pass_fill_missing_date Fill missing date fields with "unknown"
  Pass 5  _pass_normalise_course  Normalise "CS 3358", "1428", "CS ASSEMBLY"… → "CS3358"

CHANGES
-------
- Junk filter word minimum lowered from 16 → 5.
  The old threshold was dropping real short reviews like
  "Very very bad professor don't take his class" (8 words).

- Professor cap pass removed.
  The TF-IDF diversity selection was dropping similar reviews to keep
  the index under 100 per professor. With ~77 unique chunks total we
  need every review, including ones that express the same sentiment,
  because volume matters for retrieval confidence.

Each pass returns the (possibly modified) chunk list and writes a
summary into the shared `report` dict.

No file I/O happens here. This module is purely in-memory transforms.
"""

import re

JUNK_SIGNALS = [
    "nvidia",
    "wwdc",
    "macos",
    "apple",
    "image slip",
    "tagline",
    "bill that comes",
]


def _body(text: str) -> str:
    # chunker.py Fix 1: chunk["text"] is now body-only; the pipe-delimited
    # header has been removed at ingest time. This helper is kept for
    # compatibility — the split returns the full text when no blank line
    # is found, which is the correct behaviour for header-free chunks.
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) > 1 else text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


def clean_chunks(chunks: list[dict]) -> tuple[list[dict], dict]:
    report: dict = {}

    chunks = _pass_junk_filter(chunks, report)
    chunks = _pass_truncation_flag(chunks, report)
    chunks = _pass_short_flag(chunks, report)
    chunks = _pass_fill_missing_date(chunks, report)
    chunks = _pass_normalise_course(chunks, report)

    return chunks, report


def _pass_junk_filter(chunks: list[dict], report: dict) -> list[dict]:
    kept: list[dict] = []
    dropped: list[str] = []

    for chunk in chunks:
        if chunk["metadata"].get("chunk_type") != "review":
            kept.append(chunk)
            continue

        body = _body(chunk["text"])
        body_lower = body.lower()

        is_junk_keyword = any(signal in body_lower for signal in JUNK_SIGNALS)

        if _word_count(body) < 5 or is_junk_keyword:
            dropped.append(chunk["text"][:80])
        else:
            kept.append(chunk)

    report["junk_dropped"] = len(dropped)
    report["junk_examples"] = dropped[:5]

    return kept


def _pass_truncation_flag(chunks: list[dict], report: dict) -> list[dict]:
    flagged = 0

    for chunk in chunks:
        if chunk["metadata"].get("chunk_type") != "review":
            continue

        body = _body(chunk["text"])

        if body and body[-1] not in ".!?\"'":
            chunk["metadata"]["possibly_truncated"] = True
            flagged += 1

    report["truncated_flagged"] = flagged

    return chunks


def _pass_short_flag(chunks: list[dict], report: dict) -> list[dict]:
    flagged = 0

    for chunk in chunks:
        if chunk["metadata"].get("chunk_type") != "review":
            continue

        body = _body(chunk["text"])

        if _word_count(body) < 50:
            chunk["metadata"]["short_review"] = True
            flagged += 1

    report["short_flagged"] = flagged

    return chunks


def _pass_fill_missing_date(chunks: list[dict], report: dict) -> list[dict]:
    filled = 0

    for chunk in chunks:
        if chunk["metadata"].get("chunk_type") == "review":
            if not chunk["metadata"].get("date"):
                chunk["metadata"]["date"] = "unknown"
                filled += 1

    report["date_filled"] = filled

    return chunks


# Free-text course names students typed into review sites, mapped to codes.
COURSE_NAME_ALIASES = {
    "ASSEMBLY": "CS2318",
    "DATASTRUCTURES": "CS3358",
}


def normalise_course(raw: str) -> tuple[str, list[str]]:
    """
    Map a scraped course string to (primary_code, all_codes).

      "CS 3358"      -> ("CS3358", ["CS3358"])
      "1428"         -> ("CS1428", ["CS1428"])
      "HONORSCS1428" -> ("CS1428", ["CS1428"])
      "CS3358004"    -> ("CS3358", ["CS3358"])       section number dropped
      "CS23183358"   -> ("CS2318", ["CS2318", "CS3358"])
      "CS ASSEMBLY"  -> ("CS2318", ["CS2318"])
      "MATH 2471"    -> ("MATH2471", ["MATH2471"])
      "CS WHATEVER"  -> ("", [])                     unknowable, left blank

    Anything unrecognised is returned unchanged so nothing is silently lost.
    """
    if not isinstance(raw, str):
        return "", []
    s = re.sub(r"[\s\-]+", "", raw.upper())
    if s in ("", "UNKNOWN"):
        return "", []

    prefix_match = re.match(r"^(?:HONORS)?([A-Z]*?)(\d+)(\w*)$", s)
    if prefix_match:
        prefix = prefix_match.group(1) or "CS"
        digits = prefix_match.group(2)
        if prefix == "CS" and len(digits) >= 8 and len(digits) % 4 == 0:
            codes = [f"CS{digits[i:i + 4]}" for i in range(0, len(digits), 4)]
            return codes[0], codes
        if len(digits) >= 4:
            code = f"{prefix}{digits[:4]}"
            return code, [code]
        # "CS230", "CS53": truncated, can't be trusted
        return "", []

    name = s.removeprefix("CS")
    if name in COURSE_NAME_ALIASES:
        code = COURSE_NAME_ALIASES[name]
        return code, [code]
    if s.startswith("CS"):
        return "", []
    return raw, [raw]


def _pass_normalise_course(chunks: list[dict], report: dict) -> list[dict]:
    """
    Normalise every course code to "CS3358" form. The original string is kept
    in course_raw, and multi-course reviews list every code in `courses`
    (pipe-delimited, because Chroma metadata values must be scalars).
    """
    normalised = 0

    for chunk in chunks:
        meta = chunk["metadata"]
        raw = meta.get("course", "")
        primary, codes = normalise_course(raw)

        meta["course_raw"] = raw
        meta["course"] = primary
        meta["courses"] = "|".join(codes)
        if primary != raw:
            normalised += 1

    report["courses_normalised"] = normalised

    return chunks
