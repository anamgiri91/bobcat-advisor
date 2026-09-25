"""
profile.py
==========
The intake agent: turns the form a student fills in into a normalised
StudentProfile, and flags things an advisor would ask about before
recommending anything (a GPA under 2.0, an overload, a classification that
doesn't match the hours on record).

It's deterministic on purpose. Every later agent keys off these fields, so a
misread course code here would silently corrupt the audit and the schedule;
a regex is auditable, an LLM extraction isn't.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

YEARS = ("freshman", "sophomore", "junior", "senior")
_YEAR_ALIASES = {
    "1": "freshman", "first": "freshman", "freshman": "freshman", "fr": "freshman",
    "2": "sophomore", "second": "sophomore", "sophomore": "sophomore", "so": "sophomore",
    "3": "junior", "third": "junior", "junior": "junior", "jr": "junior",
    "4": "senior", "fourth": "senior", "senior": "senior", "sr": "senior", "5": "senior",
}
TERMS = ("Fall", "Spring", "Summer")

# Any TXST subject code: "CS 2308", "cs2308", "MATH 2471", "ENG 1310".
_CODE = re.compile(r"\b([A-Za-z]{2,4})\s?-?(\d{4}[A-Za-z]?)\b")
# Words that look like subject codes but aren't ("the 2024 catalog").
_NOT_SUBJECTS = {"THE", "IN", "AND", "FOR", "OF", "TO", "YEAR", "FALL", "SPRING", "SUMMER"}

# Typical TXST classification by earned hours; used only to flag mismatches.
# Minimum earned hours per classification.
_CLASSIFICATION_HOURS = {"freshman": 0, "sophomore": 30, "junior": 60, "senior": 90}


def normalise_codes(values: list[str] | str | None) -> list[str]:
    """['cs 2308', 'MATH2471, eng1310'] -> ['CS2308', 'MATH2471', 'ENG1310'] (order kept)."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for v in values:
        for subj, num in _CODE.findall(v or ""):
            subj = subj.upper()
            if subj in _NOT_SUBJECTS:
                continue
            code = f"{subj}{num.upper()}"
            if code not in out:
                out.append(code)
    return out


def normalise_catalog_year(value) -> str | None:
    """'2025-26', '2025/2026', '2025' -> '2025-2026'; anything else -> None."""
    m = re.search(r"(20\d\d)(?:\s*[-/–]\s*(?:20)?(\d\d))?", str(value or ""))
    if not m:
        return None
    start = int(m.group(1))
    if m.group(2) and int(m.group(2)) != (start + 1) % 100:
        return None
    return f"{start}-{start + 1}"


def credit_hours(code: str) -> int:
    """TXST numbering: the second digit is the credit hours (CS 1428 = 4, MATH 2471 = 4)."""
    m = re.search(r"\d(\d)\d\d", code)
    hours = int(m.group(1)) if m else 3
    return hours if 1 <= hours <= 6 else 3


def course_level(code: str) -> int:
    m = re.search(r"(\d)\d{3}", code)
    return int(m.group(1)) if m else 1


@dataclass
class StudentProfile:
    major: str = "Computer Science"
    degree: str = "BS"
    year: str = "freshman"
    semester: str = "Fall"                     # the term being planned
    catalog_year: str | None = None            # e.g. "2025-2026"
    completed: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    gpa: float | None = None
    target_credits: int = 15
    interests: list[str] = field(default_factory=list)
    career_goal: str = ""
    minor: str = ""
    notes: str = ""
    term_year: int | None = None               # year of the planned term; default: the next one
    # Timetable preferences (app/structured/timetable.py)
    preferred_days: str = ""                   # e.g. "MWF"
    earliest_start: str | None = None          # "09:00"
    latest_end: str | None = None
    busy: list[str] = field(default_factory=list)   # "TR 12:00-17:00"
    modality: str | None = None                # "in person" | "online" | "hybrid"

    @property
    def planned_term(self) -> str:
        """'Spring 2027': the named season in term_year, or its next occurrence."""
        if self.term_year:
            return f"{self.semester} {self.term_year}"
        today = dt.date.today()
        starts = {"Spring": 1, "Summer": 6, "Fall": 8}
        year = today.year if today.month < starts[self.semester] else today.year + 1
        return f"{self.semester} {year}"

    @property
    def year_index(self) -> int:
        return YEARS.index(self.year) + 1

    @property
    def planning_completed(self) -> list[str]:
        """Courses treated as done when planning next term (in-progress assumed passed)."""
        return list(dict.fromkeys(self.completed + self.in_progress))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["year_index"] = self.year_index
        d["planned_term"] = self.planned_term
        return d

    def summary(self) -> str:
        lines = [
            f"Major: {self.major} ({self.degree})" + (f", minor in {self.minor}" if self.minor else ""),
            f"Classification: {self.year}; planning the {self.planned_term} term",
            f"Completed: {', '.join(self.completed) or 'none listed'}",
        ]
        if self.in_progress:
            lines.append(f"In progress (assumed passed for planning): {', '.join(self.in_progress)}")
        if self.gpa is not None:
            lines.append(f"GPA: {self.gpa:.2f}")
        lines.append(f"Target load: {self.target_credits} credit hours")
        if self.catalog_year:
            lines.append(f"Catalog year: {self.catalog_year}")
        if self.interests:
            lines.append(f"Interests: {', '.join(self.interests)}")
        if self.career_goal:
            lines.append(f"Career goal: {self.career_goal}")
        if self.notes:
            lines.append(f"Student notes: {self.notes}")
        return "\n".join(lines)


def _term_year(value) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 2000 <= year <= 2100 else None


def build_profile(raw: dict) -> tuple[StudentProfile, list[str]]:
    """Normalise raw input. Returns (profile, flags for the advisor to raise)."""
    flags: list[str] = []

    year_raw = (str(raw.get("year") or "").lower().split() or ["freshman"])[0]
    year = _YEAR_ALIASES.get(year_raw) or _YEAR_ALIASES.get(re.sub(r"(st|nd|rd|th)$", "", year_raw))
    if year is None:
        flags.append(f"Unrecognised classification '{raw.get('year')}'; assumed freshman.")
        year = "freshman"

    semester = str(raw.get("semester") or "Fall").strip().title()
    if semester not in TERMS:
        semester = "Fall"

    notes = str(raw.get("notes") or "")[:1000]
    completed = normalise_codes(raw.get("completed"))
    # Course codes mentioned in free-text notes ("I already passed MATH 2471")
    # are NOT silently added: the student may be naming courses they plan to
    # take. They're surfaced so the advisor can ask.
    mentioned = [c for c in normalise_codes(notes) if c not in completed]
    if mentioned:
        flags.append(f"Your notes mention {', '.join(mentioned)}; add them to completed courses "
                     "if you've passed them.")
    in_progress = [c for c in normalise_codes(raw.get("in_progress")) if c not in completed]

    gpa = raw.get("gpa")
    try:
        gpa = float(gpa) if gpa not in (None, "") else None
    except (TypeError, ValueError):
        gpa = None
    if gpa is not None and not 0.0 <= gpa <= 4.0:
        flags.append(f"GPA {gpa} is outside 0.0-4.0 and was ignored.")
        gpa = None

    try:
        target = int(raw.get("target_credits") or 15)
    except (TypeError, ValueError):
        target = 15
    target = max(3, min(target, 21))

    interests = raw.get("interests") or []
    if isinstance(interests, str):
        interests = re.split(r"[,;/]", interests)
    interests = [i.strip()[:40] for i in interests if i and i.strip()][:8]

    profile = StudentProfile(
        major=(str(raw.get("major") or "Computer Science").strip()[:80]) or "Computer Science",
        degree=(str(raw.get("degree") or "BS").strip().upper().replace(".", "")[:6]) or "BS",
        year=year,
        semester=semester,
        catalog_year=normalise_catalog_year(raw.get("catalog_year")),
        completed=completed,
        in_progress=in_progress,
        gpa=gpa,
        target_credits=target,
        interests=interests,
        career_goal=str(raw.get("career_goal") or "").strip()[:200],
        minor=str(raw.get("minor") or "").strip()[:80],
        notes=notes,
        term_year=_term_year(raw.get("term_year")),
        preferred_days=str(raw.get("preferred_days") or "")[:14],
        earliest_start=(str(raw["earliest_start"])[:8] if raw.get("earliest_start") else None),
        latest_end=(str(raw["latest_end"])[:8] if raw.get("latest_end") else None),
        busy=[str(b)[:40] for b in (raw.get("busy") or [])][:10],
        modality=(str(raw["modality"]).lower()[:10] if raw.get("modality") else None),
    )

    # Advisor-style flags. Phrased as things to check, not rules: exact
    # thresholds are university policy and can change.
    if gpa is not None and gpa < 2.0:
        flags.append("GPA is below 2.0: check academic standing before registering, and consider a "
                     "lighter load.")
    if target > 18:
        flags.append(f"{target} hours is an overload; loads above 18 hours usually need approval.")
    if target < 12:
        flags.append(f"{target} hours is below full-time (12); this can affect financial aid "
                     "and scholarships.")
    earned = sum(credit_hours(c) for c in completed)
    lo = _CLASSIFICATION_HOURS[year]
    if completed and earned < lo:
        flags.append(f"The courses listed add up to about {earned} hours, fewer than a typical "
                     f"{year} ({lo}+). If you have transfer, AP or dual credit, list it too.")
    return profile, flags
