"""
scrub.py
========
Removes people's details before anything is indexed.

Syllabi name the instructor, their email, phone, office and office hours.
None of that is needed to answer "what's the textbook?" and the app keeps
no data about individual people, so it is removed at ingest time — it never
reaches the index, the prompt or the answer.

Three layers (syllabi get all three; every other source gets the last):
  1. drop whole sections about the instructor/TA/contact/office hours
  2. replace lines that label a person or their contact details
  3. replace emails, phone numbers and titled names ("Dr. Jane Smith")
"""

from __future__ import annotations

import re

from ..guardrails import redact_pii

REMOVED = "[instructor details removed]"

_PEOPLE_SECTION = re.compile(
    r"\b(instructor|professor|lecturer|faculty|teaching assistants?|\bta\b|contact|office hours?|"
    r"about (me|your instructor)|course staff)\b",
    re.IGNORECASE,
)
_PEOPLE_LINE = re.compile(
    r"^\s*[-*•]?\s*(instructor|professor|prof\.?|lecturer|teaching assistant|ta|grader|"
    r"office( hours| location)?|e-?mail|phone|telephone|contact|name|course coordinator)"
    r"(\s*\([^)]*\))?\s*[:\-–]",
    re.IGNORECASE,
)
_TITLED_NAME = re.compile(
    r"\b(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx)\.?\s+(?:[A-Z][a-zA-Z'\-]+\.?\s?){1,3}"
)
_URL_PERSON = re.compile(r"https?://\S*(?:~|/people/|/faculty/|/staff/)\S*", re.IGNORECASE)


def is_people_section(heading: str) -> bool:
    return bool(_PEOPLE_SECTION.search(heading or ""))


def scrub_contacts(text: str) -> str:
    """Emails and phone numbers (all sources)."""
    return redact_pii(text)


def scrub_people(text: str) -> tuple[str, int]:
    """Syllabus text with person-identifying lines and names removed. Returns (text, removals)."""
    removed = 0
    out: list[str] = []
    for line in text.splitlines():
        if _PEOPLE_LINE.match(line):
            removed += 1
            if not out or out[-1] != REMOVED:
                out.append(REMOVED)
            continue
        new, n = _TITLED_NAME.subn("the instructor ", line)
        new, m = _URL_PERSON.subn("[link removed]", new)
        removed += n + m
        out.append(re.sub(r"\s{2,}", " ", new).rstrip())
    text = redact_pii("\n".join(out))
    return text, removed
