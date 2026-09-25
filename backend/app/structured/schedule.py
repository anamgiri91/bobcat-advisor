"""
schedule.py
===========
The class schedule as a structured store: one row per section, never text
chunks. A timetable is a constraint problem over days and times, which a
search index can't answer; tools query this table instead.

A Section has: term, course, section, CRN, title, meetings (days + start/end
minutes), modality, campus, seats (capacity/open/waitlist). Instructor
columns are recognised only so they can be skipped: the app keeps no data
about individual people.

The live schedule site's format is unknown until it's reachable, so the
parser maps columns by header names (CRN, Days, Time, Seats, ...) rather
than position. That handles most HTML schedule tables and CSV exports; a
site that needs a form POST or an API gets a small adapter once checked.

Storage: data/schedule_sections.jsonl (all terms), loaded once and cached
by file mtime.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..advising.profile import normalise_codes
from ..advising.web import ParsedPage, Table
from ..config import settings

DAY_ORDER = "MTWRFSU"
_DAY_TOKENS = [("TH", "R"), ("TU", "T"), ("SA", "S"), ("SU", "U"), ("M", "M"), ("T", "T"),
               ("W", "W"), ("R", "R"), ("F", "F"), ("S", "S"), ("U", "U")]
_TERM = re.compile(r"\b(fall|spring|summer)\s+(20\d\d)\b", re.IGNORECASE)


@dataclass
class Meeting:
    days: str             # subset of "MTWRFSU", in order
    start: int            # minutes after midnight
    end: int

    def overlaps(self, other: Meeting) -> bool:
        return bool(set(self.days) & set(other.days)) and self.start < other.end and other.start < self.end

    def label(self) -> str:
        return f"{self.days} {fmt_time(self.start)}-{fmt_time(self.end)}"


@dataclass
class Section:
    term: str                          # "Fall 2026"
    course: str                        # "CS3358"
    section: str                       # "001"
    crn: str = ""
    title: str = ""
    meetings: list[Meeting] = field(default_factory=list)   # empty = online/asynchronous/TBA
    modality: str = ""                 # in person | online | hybrid | ""
    campus: str = ""
    seats_total: int | None = None
    seats_open: int | None = None
    waitlist: int | None = None

    @property
    def id(self) -> str:
        return f"{self.course}.{self.section}"

    @property
    def is_full(self) -> bool:
        return self.seats_open is not None and self.seats_open <= 0

    def conflicts(self, other: Section) -> bool:
        return any(a.overlaps(b) for a in self.meetings for b in other.meetings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Section:
        return cls(**{**d, "meetings": [Meeting(**m) for m in d.get("meetings", [])]})


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

def fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


_DAY_NAMES = re.compile(r"THURSDAY|THURS|THUR|THU|TUESDAY|TUES|TUE|MONDAY|MON|WEDNESDAY|WED|"
                        r"FRIDAY|FRI|SATURDAY|SAT|SUNDAY|SUN")
_DAY_NAME_CODES = {"THU": "TH", "TUE": "TU", "MON": "M", "WED": "W", "FRI": "F", "SAT": "SA",
                   "SUN": "SU"}


def parse_days(text: str) -> str:
    """'MWF' / 'M W F' / 'TuTh' / 'TTh' / 'Mon, Wed' -> ordered subset of MTWRFSU ('' if not days)."""
    s = re.sub(r"[^A-Za-z]", "", text or "").upper()
    s = _DAY_NAMES.sub(lambda m: _DAY_NAME_CODES[m.group(0)[:3]], s)
    days, i = set(), 0
    while i < len(s):
        for tok, day in _DAY_TOKENS:
            if s.startswith(tok, i):
                days.add(day)
                i += len(tok)
                break
        else:
            return ""   # not a days field (e.g. "TBA", "ONLINE")
    return "".join(d for d in DAY_ORDER if d in days)


def parse_time(text: str) -> int | None:
    """'10:30 am', '2:00 PM', '1030', '14:00' -> minutes after midnight."""
    m = re.fullmatch(r"(\d{1,4})(?::(\d{2}))?\s*(?:([ap])\.?\s*m\.?)?", (text or "").strip(), re.IGNORECASE)
    if not m:
        return None
    digits, ampm = m.group(1), (m.group(3) or "").lower()
    if m.group(2) is None and len(digits) >= 3:
        h, mins = divmod(int(digits), 100)
    else:
        h, mins = int(digits), int(m.group(2) or 0)
    if ampm == "p" and h < 12:
        h += 12
    if ampm == "a" and h == 12:
        h = 0
    if not 0 <= h <= 23 or not 0 <= mins <= 59:
        return None
    return h * 60 + mins


def parse_time_range(text: str) -> tuple[int, int] | None:
    """'10:00 am-11:20 am', '1000-1120', '2:00-3:15 pm' -> (start, end) minutes."""
    parts = re.split(r"\s*[-–]\s*", (text or "").strip())
    if len(parts) != 2:
        return None
    a, b = parts
    ampm_b = re.search(r"[ap]\.?\s*m\.?", b, re.IGNORECASE)
    if ampm_b and not re.search(r"[ap]\.?\s*m\.?", a, re.IGNORECASE):
        a = f"{a} {ampm_b.group(0)}"
    start, end = parse_time(a), parse_time(b)
    if start is None or end is None:
        return None
    if end <= start and ampm_b and ampm_b.group(0).lower().startswith("p") and start >= 12 * 60:
        start -= 12 * 60            # "11:00-12:20 pm" read as 11 pm
    return (start, end) if end > start else None


def _int(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else None


def parse_modality(text: str) -> str:
    t = (text or "").lower()
    if "hybrid" in t or "blend" in t:
        return "hybrid"
    if "online" in t or "internet" in t or "distance" in t or "web" in t:
        return "online"
    if t.strip():
        return "in person"
    return ""


# ---------------------------------------------------------------------------
# Header-mapped tables (HTML or CSV)
# ---------------------------------------------------------------------------

# Column name -> field. Checked in order; the first matching keyword wins.
_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
    ("instructor", ("instructor", "professor", "faculty", "teacher")),   # recognised to be skipped
    ("crn", ("crn", "call number", "class nbr", "class number")),
    ("subject", ("subject", "subj")),
    ("number", ("course number", "crse", "catalog nbr", "cat nbr", "number")),
    ("course", ("course",)),
    ("section", ("section", "sec")),
    ("title", ("title",)),
    ("days", ("days", "day")),
    ("time", ("time",)),
    ("waitlist", ("wait", "wl")),
    ("seats_open", ("remaining", "avail", "open", "rem")),
    ("enrolled", ("enrolled", "enrl", "actual", "act")),
    ("seats_total", ("capacity", "cap", "max", "limit", "seats")),
    ("modality", ("instructional method", "modality", "method", "mode", "delivery")),
    ("campus", ("campus",)),
]


def map_columns(headers: list[str]) -> dict[int, str]:
    out: dict[int, str] = {}
    used: set[str] = set()
    for i, h in enumerate(headers):
        h = re.sub(r"\s+", " ", (h or "").strip().lower())
        if not h:
            continue
        for fld, keys in _COLUMNS:
            if fld in used and fld != "instructor":
                continue
            if any(k == h or re.search(rf"\b{re.escape(k)}\b", h) for k in keys):
                out[i] = fld
                used.add(fld)
                break
    return out


def rows_to_sections(headers: list[str], rows: list[list[str]], term: str) -> list[Section]:
    cols = map_columns(headers)
    fields = set(cols.values())
    if not ({"days", "time"} <= fields and ({"course"} <= fields or {"subject", "number"} <= fields)):
        return []
    sections: list[Section] = []
    for cells in rows:
        rec = {fld: (cells[i].strip() if i < len(cells) else "") for i, fld in cols.items()
               if fld != "instructor"}
        code = ""
        if rec.get("subject") and rec.get("number"):
            code = normalise_codes(f"{rec['subject']} {rec['number']}")
        elif rec.get("course"):
            code = normalise_codes(rec["course"])
        code = code[0] if code else ""
        days = parse_days(rec.get("days", ""))
        span = parse_time_range(rec.get("time", ""))
        meeting = Meeting(days, *span) if days and span else None

        # A row without a course/CRN is another meeting of the previous section (e.g. a lab).
        if not code and not rec.get("crn") and sections and meeting:
            sections[-1].meetings.append(meeting)
            continue
        if not code:
            continue
        total, enrolled, open_ = _int(rec.get("seats_total", "")), _int(rec.get("enrolled", "")), \
            _int(rec.get("seats_open", ""))
        if open_ is None and total is not None and enrolled is not None:
            open_ = total - enrolled
        sections.append(Section(
            term=term, course=code, section=re.sub(r"\s+", "", rec.get("section", "")) or "?",
            crn=rec.get("crn", ""), title=rec.get("title", "")[:120],
            meetings=[meeting] if meeting else [],
            modality=parse_modality(rec.get("modality", "")) or ("online" if not meeting else ""),
            campus=rec.get("campus", "")[:60], seats_total=total, seats_open=open_,
            waitlist=_int(rec.get("waitlist", "")),
        ))
    return sections


def _table_headers(table: Table) -> tuple[list[str], list[list[str]]]:
    rows = table.rows
    head_idx = next((i for i, r in enumerate(rows) if any(c.tag == "th" for c in r.cells)), 0)
    if not rows:
        return [], []
    headers = [c.text for c in rows[head_idx].cells]
    return headers, [[c.text for c in r.cells] for r in rows[head_idx + 1:]]


def sections_from_page(page: ParsedPage, term: str | None = None) -> list[Section]:
    """Every schedule-like table on a page. The term comes from the argument or the page."""
    if term is None:
        m = _TERM.search(f"{page.title} {page.text[:2000]}")
        term = f"{m.group(1).title()} {m.group(2)}" if m else ""
    out: list[Section] = []
    for table in page.tables:
        headers, rows = _table_headers(table)
        out.extend(rows_to_sections(headers, rows, term))
    return out


def sections_from_csv(text: str, term: str) -> list[Section]:
    reader = list(csv.reader(io.StringIO(text)))
    return rows_to_sections(reader[0], reader[1:], term) if reader else []


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_cache: dict = {"mtime": None, "sections": []}
_lock = threading.Lock()


def store_path() -> Path:
    return Path(settings.DATA_DIR) / "schedule_sections.jsonl"


def save_sections(sections: list[Section], replace_terms: set[str] | None = None) -> None:
    """Write sections, replacing any stored rows for the same terms (other terms kept)."""
    replace_terms = replace_terms if replace_terms is not None else {s.term for s in sections}
    kept = [s for s in load_sections() if s.term not in replace_terms]
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for s in sorted(kept + sections, key=lambda s: (s.term, s.course, s.section)):
            f.write(json.dumps(s.to_dict()) + "\n")


def load_sections() -> list[Section]:
    path = store_path()
    if not path.exists():
        return []
    mtime = (str(path), path.stat().st_mtime)
    with _lock:
        if _cache["mtime"] != mtime:
            _cache["sections"] = [Section.from_dict(json.loads(line))
                                  for line in path.open(encoding="utf-8") if line.strip()]
            _cache["mtime"] = mtime
        return list(_cache["sections"])


def terms() -> list[str]:
    return sorted({s.term for s in load_sections()}, key=term_sort_key)


def sections_for(course: str, term: str) -> list[Section]:
    return [s for s in load_sections() if s.course == course and s.term == term]


def term_sort_key(term: str) -> tuple[int, int]:
    m = _TERM.search(term or "")
    if not m:
        return (0, 0)
    return int(m.group(2)), {"spring": 0, "summer": 1, "fall": 2}[m.group(1).lower()]
