"""
cleaner.py
==========
Normalises chunk metadata before embedding. One pass today:

  _pass_normalise_course   "CS 3358", "1428", "HONORSCS1428" -> "CS3358"

Each pass returns the (possibly modified) chunk list and writes a summary
into the shared `report` dict. No file I/O happens here.
"""

import re


def clean_chunks(chunks: list[dict]) -> tuple[list[dict], dict]:
    report: dict = {}
    chunks = _pass_normalise_course(chunks, report)
    return chunks, report


# Free-text course names mapped to codes.
COURSE_NAME_ALIASES = {
    "ASSEMBLY": "CS2318",
    "DATASTRUCTURES": "CS3358",
}


def normalise_course(raw: str) -> tuple[str, list[str]]:
    """
    Map a course string to (primary_code, all_codes).

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
    in course_raw, and every code is listed in `courses`
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
