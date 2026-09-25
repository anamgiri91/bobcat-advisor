"""
dates.py
========
Turns calendar lines ("Last day to drop a class | Oct. 28") into dated
facts, stored as data (data/kb_dates.jsonl) rather than left in prose.

Why: a retrieved paragraph can't tell the model that its date is last
year's. A fact with an ISO date and a term can: the calendar agent compares
it with today and marks it past or upcoming, and prefers the next upcoming
one. The synthesizer only states dates from these facts.

A year is never guessed. It comes from the line itself, or from a term
("Fall 2026") or academic year ("2026-2027") in the section's headings or
page title; a line with no inferable year is skipped.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
             r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_WORDS = re.compile(
    rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?(?:,?\s*(20\d\d))?\b",
    re.IGNORECASE,
)
_DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d|\d\d)\b")
_WEEKDAY = re.compile(r"\b(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?\.?,?\s*", re.IGNORECASE)
_TERM = re.compile(r"\b(fall|spring|summer|winter)\s+(?:semester\s+|session\s+)?(20\d\d)\b", re.IGNORECASE)
_ACAD_YEAR = re.compile(r"\b(20\d\d)\s*[-–/]\s*(?:20)?(\d\d)\b")


@dataclass
class DateFact:
    event: str
    date: str                 # ISO yyyy-mm-dd
    term: str                 # "Fall 2026" or ""
    url: str
    section: str
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _context(context: str) -> tuple[str, int | None, tuple[int, int] | None]:
    """(term label, term year, academic year span) from headings/title."""
    term = _TERM.search(context)
    acad = _ACAD_YEAR.search(context)
    span = None
    if acad:
        start = int(acad.group(1))
        end = int(acad.group(2)) + (start // 100) * 100
        if end == start + 1:
            span = (start, end)
    if term:
        return f"{term.group(1).title()} {term.group(2)}", int(term.group(2)), span
    return "", None, span


def _year_for(month: int, explicit: str | None, term_year: int | None,
              span: tuple[int, int] | None) -> int | None:
    if explicit:
        y = int(explicit)
        return y + 2000 if y < 100 else y
    if term_year:
        return term_year
    if span:  # academic year: Aug-Dec in the first year, Jan-Jul in the second
        return span[0] if month >= 8 else span[1]
    return None


def extract_dates(text: str, context: str, url: str, fetched_at: str) -> list[DateFact]:
    """Dated facts from a section's lines. `context` = heading path + page title."""
    term_label, term_year, span = _context(context)
    facts: list[DateFact] = []
    for line in text.splitlines():
        # A term named in the line is usually part of the event ("Registration
        # for Spring 2027 opens | Nov. 2" happens in fall), so the section's
        # term decides the year; the line's own term is only a fallback.
        line_term, line_year, line_span = _context(line)
        m = _DATE_WORDS.search(line)
        if m:
            month = _MONTHS[m.group(1)[:3].lower()]
            day, year_s = int(m.group(2)), m.group(3)
        else:
            n = _DATE_NUM.search(line)
            if not n:
                continue
            month, day, year_s = int(n.group(1)), int(n.group(2)), n.group(3)
        year = _year_for(month, year_s, term_year or line_year, span or line_span)
        if year is None:
            continue
        try:
            date = dt.date(year, month, day)
        except ValueError:
            continue
        event = (line[: m.start()] + line[m.end():]) if m else _DATE_NUM.sub("", line)
        event = _WEEKDAY.sub("", event)
        event = re.sub(r"\s*\|\s*", " ", event).strip(" -–:|,.;")
        event = re.sub(r"\s+", " ", event)
        if len(event) < 4:
            continue
        facts.append(DateFact(event=event[:200], date=date.isoformat(),
                              term=term_label or line_term, url=url,
                              section=context[:200], fetched_at=fetched_at))
    return facts
