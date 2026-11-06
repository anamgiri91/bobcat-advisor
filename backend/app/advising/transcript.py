"""
transcript.py
=============
Reads a student's degree audit or unofficial transcript (a PDF, or text
pasted from the audit page) and returns what the Advisor form needs:
courses passed, courses in progress, transfer and test credit, hours
earned, GPA and classification.

Personal data. The document is read in memory and dropped when the
request ends: it is never written to disk, logged, cached or put in a
trace (spans carry counts only). The result holds course records and a
few program fields, never the student's name, ID, address or birth date,
which the parser doesn't look for.

Layouts differ (Degree Works audits, Banner transcripts, a copy-paste of
either), so the parser keys on what every layout shares: a course record
is a line with ONE course code and a grade next to its credit hours
("CS 1428 Foundations of Computer Science I  A  4.000"). Lines that name
courses without a grade, such as "Still needed: 1 Class in CS 3358", are
requirements, not records, and are skipped.

Grades decide what counts:
  passed       A, B, C, P, S, CR, and transfer grades (TA, TB, TC, TCR...)
  low          D: the hours count, but most CS and MATH prerequisites need a
               C or better, so the course isn't treated as passed for
               planning, and the student is told to ask about a retake
  not counted  F, U, NC, W (withdrawn), Q (dropped)
  in progress  IP, or a course with no grade under an "in progress" heading
An Incomplete ("I") isn't read as a grade: "Calculus I 4.000" is a title
ending in a Roman numeral far more often than an incomplete course.
A course taken more than once counts once, with its best result.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field

from .profile import _CLASSIFICATION_HOURS

MAX_BYTES = 5_000_000
MAX_TEXT_CHARS = 200_000
MAX_PAGES = 30


class TranscriptError(ValueError):
    """The document can't be read; the message is written for the student."""


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# "CS 1428", "MATH2471", "PHYS 2125", generic transfer credit "HIST 1XXX" / "ENG 13XX" / "MATH 2@".
_COURSE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,4})\s?(\d{4}[A-Z]?|\d[\dX@]{0,2}[X@])(?![\w@])")
# Uppercase words that can precede a 4-digit year and look like a subject.
_NOT_SUBJECTS = {"FALL", "TERM", "YEAR", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG",
                 "SEP", "SEPT", "OCT", "NOV", "DEC", "PAGE", "GPA", "HRS", "TOTAL", "DATE", "ID",
                 "UG", "GR", "PRE", "CRN", "SSN", "DOB", "ZIP", "PO", "BOX", "TX"}

_PASS = {"A", "B", "C", "P", "S", "CR", "T", "TA", "TB", "TC", "TP", "TS", "TCR", "K"}
_LOW = {"D", "TD"}
_FAIL = {"F", "U", "NC", "NP", "TF"}
_WITHDRAWN = {"W", "WF", "WP", "Q", "QF", "DR"}
_IN_PROGRESS = {"IP"}
_GRADES = _PASS | _LOW | _FAIL | _WITHDRAWN | _IN_PROGRESS
_GRADE_ALT = "|".join(sorted((re.escape(g) for g in _GRADES), key=len, reverse=True))
_HOURS = r"\(?(\d{1,2}(?:\.\d{1,3})?)\)?"
# Grade then hours ("A 4.000", "IP (3)"), or hours then grade ("4.000 A").
_GRADE_HOURS = re.compile(rf"(?<![\w.])({_GRADE_ALT})([+-]?)\s+{_HOURS}(?![\w.])")
_HOURS_GRADE = re.compile(rf"(?<![\w.(])\(?(\d{{1,2}}\.\d{{1,3}})\)?\s+({_GRADE_ALT})([+-]?)(?![\w.])")
_BARE_HOURS = re.compile(rf"(?<![\w.]){_HOURS}(?![\w.])")

_TERM = re.compile(r"\b(Fall|Spring|Summer(?:\s+(?:I{1,2}|1|2))?|Winter|Maymester)\s+(20\d\d)\b", re.I)
# Requirement text, not a record of a course taken.
_REQUIREMENT = re.compile(
    r"still needed|needed:|\bclass(?:es)? in\b|\bcredits? in\b|\bchoose\b|\bor better\b|"
    r"\bminimum\b|\bat least\b|\bexcept\b|\bnot met\b|\bprerequisite", re.I)

_GPA_LINE = re.compile(r"\b(overall|cumulative|cum\.?|institution(?:al)?|combined)\b", re.I)
_GPA_NUMBER = re.compile(r"(?<![\d.])([0-4]\.\d{2,3})(?![\d.])")
_STATED_HOURS = re.compile(
    r"(?:total\s+(?:hours|credits)\s+earned|earned\s+(?:hours|credits)|hours\s+earned|"
    r"credits\s+earned|credits\s+applied)\D{0,20}?(\d{1,3}(?:\.\d{1,3})?)", re.I)
_CLASSIFICATION = re.compile(r"\bclass(?:ification)?\b\s*:?\s*(freshman|sophomore|junior|senior)", re.I)
_MAJOR = re.compile(
    r"\bmajor\b\s*:?\s*([A-Z][A-Za-z&,' ]{2,60}?)"
    r"(?=\s{2,}|\s*$|\s*[|;]|\s+(?:minor|degree|concentration|college|catalog|classification|level|program)\b)",
    re.I)
_DEGREE = re.compile(r"\b(bachelor of science|bachelor of arts|b\.\s?s\.|b\.\s?a\.|bs|ba)\b", re.I)
_CATALOG_SPAN = re.compile(r"\bcatalog(?:\s+(?:year|term))?\b\s*:?\s*(20\d\d)\s*[-–/]\s*(?:20)?(\d\d)", re.I)
_CATALOG_TERM = re.compile(r"\bcatalog(?:\s+(?:year|term))?\b\s*:?\s*(fall|spring|summer)\s+(20\d\d)", re.I)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class CourseRecord:
    code: str                 # "CS1428", or "HIST1XXX" for generic transfer credit
    title: str
    grade: str                # "" when in progress without a grade
    hours: float
    term: str                 # "Fall 2024" or ""
    source: str               # institution | transfer | test
    status: str               # passed | low | failed | withdrawn | in_progress

    @property
    def generic(self) -> bool:
        return bool(re.search(r"[X@]$", self.code))


@dataclass
class ImportResult:
    courses: list[CourseRecord] = field(default_factory=list)   # best attempt per course
    completed: list[str] = field(default_factory=list)           # passed, specific courses
    in_progress: list[str] = field(default_factory=list)
    not_counted: list[str] = field(default_factory=list)         # D, F, W...: "CS3358 (W)"
    elective_credit: list[dict] = field(default_factory=list)    # generic transfer credit
    hours_earned: int = 0              # what the document states, else counted from courses
    hours_counted: int = 0             # summed from the course lines read
    hours_stated: int | None = None
    gpa: float | None = None
    classification: str | None = None
    classification_source: str | None = None                     # document | hours
    major: str | None = None
    degree: str | None = None
    catalog_year: str | None = None
    warnings: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)           # for the advising prep sheet
    lines_read: int = 0
    source: str = "text"               # pdf | text
    pages: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["courses"] = [{**asdict(c), "generic": c.generic} for c in self.courses]
        return d


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def pdf_text(data: bytes, max_pages: int = MAX_PAGES) -> tuple[str, int]:
    """Text of a PDF held in memory. Raises TranscriptError with a readable message."""
    if len(data) > MAX_BYTES:
        raise TranscriptError(f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise TranscriptError("That file isn't a PDF. Upload the PDF of your degree audit or "
                              "unofficial transcript, or paste its text instead.")
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise TranscriptError("The PDF is password-protected. Save an unprotected copy, or "
                                  "paste its text instead.")
        pages = reader.pages[:max_pages]
        text = "\n".join((p.extract_text() or "") for p in pages)
    except TranscriptError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as e:
        raise TranscriptError("The PDF couldn't be read. Try downloading it again, or paste its "
                              "text instead.") from e
    if not text.strip():
        raise TranscriptError("The PDF has no text in it (it's probably a scan or a photo). "
                              "Paste the text from your audit page instead.")
    return text, len(pages)


def _status(grade: str) -> str:
    """A grade decides; no grade (only allowed under an in-progress heading) means in progress."""
    if not grade or grade in _IN_PROGRESS:
        return "in_progress"
    if grade in _PASS:
        return "passed"
    if grade in _LOW:
        return "low"
    if grade in _FAIL:
        return "failed"
    return "withdrawn"


_RANK = {"passed": 0, "in_progress": 1, "low": 2, "failed": 3, "withdrawn": 4}
_GRADE_ORDER = ["A", "B", "C", "P", "S", "CR", "K", "T", "TA", "TB", "TC", "TP", "TS", "TCR"]


def _better(a: CourseRecord, b: CourseRecord) -> CourseRecord:
    """The attempt that counts: passed over in progress over D over the rest; best grade."""
    ka = (_RANK[a.status], _GRADE_ORDER.index(a.grade) if a.grade in _GRADE_ORDER else 99)
    kb = (_RANK[b.status], _GRADE_ORDER.index(b.grade) if b.grade in _GRADE_ORDER else 99)
    return a if ka <= kb else b


def _record(line: str, m: re.Match, source: str, term: str, in_progress_section: bool
            ) -> CourseRecord | None:
    code = m.group(1) + m.group(2)
    rest = line[m.end():]
    gh = _GRADE_HOURS.search(rest)
    hg = None if gh else _HOURS_GRADE.search(rest)
    if gh:
        grade, hours, title_end = gh.group(1), float(gh.group(3)), gh.start()
    elif hg:
        grade, hours, title_end = hg.group(2), float(hg.group(1)), hg.start()
    elif in_progress_section:
        bare = _BARE_HOURS.search(rest)
        if not bare:
            return None
        grade, hours, title_end = "", float(bare.group(1)), bare.start()
    else:
        return None
    if not 0 < hours <= 6:
        return None
    title = re.sub(r"^\s*(?:UGRD|UG|GRAD|GR)\b", "", rest[:title_end]).strip(" -|:")
    line_term = _TERM.search(line)
    if grade.startswith("T") and grade in _GRADES or re.search(r"\btransfer", line, re.I):
        source = "transfer"
    return CourseRecord(code=code, title=title[:80], grade=grade, hours=hours,
                        term=(f"{line_term.group(1).title()} {line_term.group(2)}" if line_term else term),
                        source=source, status=_status(grade))


def _heading(low: str) -> tuple[str | None, bool | None]:
    """(credit source, in-progress section) a heading line switches to; None = unchanged."""
    if re.match(r"(total|term totals|overall)\b", low):
        return None, None                # a totals row ("Total Transfer: 13.000 ..."), not a heading
    if re.search(r"\bin[- ]progress\b|\bcurrent(?:ly)? (?:enrolled|courses)\b|\bregistered\b", low):
        return "institution", True
    if re.search(r"\btransfer\b", low):
        return "transfer", False
    if re.search(r"\b(test credit|credit by exam\w*|advanced placement|ap credit|clep|"
                 r"examination credit)\b", low):
        return "test", False
    if re.search(r"\b(institution(?:al)? credit|texas state university credit|txst credit)\b", low):
        return "institution", False
    if re.search(r"\b(insufficient|fallthrough|not counted|courses not used)\b", low):
        return None, False
    return None, None


def parse_text(text: str) -> ImportResult:
    if len(text) > MAX_TEXT_CHARS:
        raise TranscriptError("That's more text than a degree audit; paste only the audit or transcript.")
    result = ImportResult()
    source, term, in_progress_section = "institution", "", False
    attempts: dict[str, list[CourseRecord]] = {}
    stated: list[int] = []
    gpa_overall: list[float] = []
    gpa_any: list[float] = []

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        low = line.lower()
        codes = [m for m in _COURSE.finditer(line) if m.group(1) not in _NOT_SUBJECTS]

        if not codes:
            new_source, ip = _heading(low)
            if new_source:
                source = new_source
            if ip is not None:
                in_progress_section = ip
            t = _TERM.search(line)
            if t and len(line) < 60:
                term = f"{t.group(1).title()} {t.group(2)}"
        # Program fields and totals (any line).
        if result.classification is None and (m := _CLASSIFICATION.search(line)):
            result.classification, result.classification_source = m.group(1).lower(), "document"
        if result.major is None and (m := _MAJOR.search(line)):
            major = re.sub(r"^in\s+", "", m.group(1).strip(" ,"), flags=re.I)
            if not re.match(r"requirement|course|elective|gpa", major, re.I):
                result.major = major[:80]
        if result.degree is None and re.search(r"\bdegree\b|\bbachelor\b|\bprogram\b", low):
            if m := _DEGREE.search(line):
                kind = m.group(1).lower().replace(".", "").replace(" ", "")
                result.degree = "BA" if kind in ("bachelorofarts", "ba") else "BS"
        if result.catalog_year is None:
            if m := _CATALOG_SPAN.search(line):
                start, end = int(m.group(1)), int(m.group(2)) % 100
                if end == (start + 1) % 100:
                    result.catalog_year = f"{start}-{start + 1}"
            elif m := _CATALOG_TERM.search(line):
                year = int(m.group(2))
                start = year if m.group(1).lower() == "fall" else year - 1
                result.catalog_year = f"{start}-{start + 1}"
        if m := _STATED_HOURS.search(line):
            stated.append(int(float(m.group(1))))
        if "gpa" in low or _GPA_LINE.search(line):
            numbers = [float(n) for n in _GPA_NUMBER.findall(line) if float(n) <= 4.0]
            if numbers:
                (gpa_overall if _GPA_LINE.search(line) and "term" not in low else gpa_any).append(numbers[-1])

        if not codes or _REQUIREMENT.search(line) or len({c.group(0) for c in codes}) > 1:
            continue
        rec = _record(line, codes[0], source, term, in_progress_section)
        if rec is None:
            continue
        result.lines_read += 1
        attempts.setdefault(rec.code, []).append(rec)

    if not attempts:
        raise TranscriptError("No course records were found. Make sure it's your degree audit or "
                              "unofficial transcript; if the PDF is a scan, paste the text instead.")

    for code, recs in attempts.items():
        best = recs[0]
        for r in recs[1:]:
            best = _better(best, r)
        result.courses.append(best)
        if len(recs) > 1 and best.status == "passed":
            earlier = [r for r in recs if r is not best and r.status != "passed"]
            if earlier:
                result.warnings.append(
                    f"{_spaced(code)} was taken {len(recs)} times; counted your "
                    f"{best.grade or 'passing'} grade{' from ' + best.term if best.term else ''}.")
        elif best.status == "in_progress" and any(r.status in ("low", "failed") for r in recs):
            result.warnings.append(f"You're retaking {_spaced(code)}; it's planned as passed.")

    passed_hours = 0.0
    for c in result.courses:
        if c.status in ("passed", "low"):
            passed_hours += c.hours
        if c.generic:
            if c.status == "passed":
                result.elective_credit.append({"code": c.code, "hours": c.hours, "source": c.source})
            continue
        if c.status == "passed":
            result.completed.append(c.code)
        elif c.status == "in_progress":
            result.in_progress.append(c.code)
        else:
            result.not_counted.append(f"{c.code} ({c.grade})")

    lows = [c for c in result.courses if c.status == "low" and not c.generic]
    if lows:
        names = ", ".join(_spaced(c.code) for c in lows)
        result.warnings.append(
            f"You have a D in {names}. Most CS and MATH prerequisites need a C or better, so it's "
            "not counted as passed for planning. Ask your advisor whether to retake it.")
        result.questions += [f"My transcript shows a D in {_spaced(c.code)}. Do I need to retake it "
                             "before the courses that require it?" for c in lows[:2]]
    if result.elective_credit:
        generic = ", ".join(_spaced(e["code"]) for e in result.elective_credit[:4])
        result.warnings.append(
            f"Some transfer or test credit ({generic}) doesn't match a specific TXST course. It counts "
            "toward your hours; ask your advisor which requirements it covers.")
        result.questions.append(f"Which degree requirements do my transfer credits ({generic}) count for?")

    result.hours_counted = round(passed_hours)
    plausible = [h for h in stated if 0 < h <= 250]
    result.hours_stated = max(plausible) if plausible else None
    result.hours_earned = result.hours_stated if result.hours_stated is not None else result.hours_counted
    if result.hours_stated is not None and abs(result.hours_stated - result.hours_counted) > 6:
        result.warnings.append(
            f"The document says {result.hours_stated} hours earned, but the course lines read add up "
            f"to {result.hours_counted}. Using {result.hours_stated}; check the course list below.")
    gpas = gpa_overall or gpa_any
    result.gpa = gpas[-1] if gpas else None
    if result.classification is None:
        result.classification = next(y for y, lo in sorted(_CLASSIFICATION_HOURS.items(),
                                                            key=lambda kv: -kv[1])
                                     if result.hours_earned >= lo)
        result.classification_source = "hours"
    return result


def read(data: bytes, content_type: str) -> ImportResult:
    """PDF bytes or UTF-8 text -> ImportResult. Nothing is kept after it returns."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype == "application/pdf" or data.lstrip()[:5].startswith(b"%PDF"):
        text, pages = pdf_text(data)
        result = parse_text(text)
        result.source, result.pages = "pdf", pages
        return result
    if ctype in ("text/plain", ""):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise TranscriptError("Paste the audit as plain text, or upload the PDF.") from e
        return parse_text(text)
    raise TranscriptError("Upload a PDF, or paste the text of your audit.")


def _spaced(code: str) -> str:
    return re.sub(r"^([A-Z]+)(\w)", r"\1 \2", code)


__all__ = ["ImportResult", "TranscriptError", "read", "parse_text", "pdf_text"]
