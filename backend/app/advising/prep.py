"""
prep.py
=======
The advising prep sheet: a one-page PDF a student brings to their advising
appointment, and a calendar file of the deadlines that apply to them.

Everything on the sheet is computed, never written by an LLM: the same
research -> fact-check -> audit -> schedule run the Advisor uses
(whatif.plan_once), the timetable with a backup section per course, the
graduation estimate, and dated facts from the TXST academic calendar
(data/kb_dates.jsonl, built by the knowledge-base crawler). The questions
for the advisor are generated from what the plan couldn't settle on its
own: unconfirmed prerequisites, approvals, courses to arrange, a D on the
transcript, credit that doesn't map to a TXST course.

The sheet carries no name or student ID; the profile's courses are the
only personal data on it, and the PDF is built in memory and returned.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

from ..kb.calendar import load_facts
from ..structured.schedule import terms as schedule_terms
from ..structured.timetable import Preferences, build_timetable
from ..tracing import span
from .profile import build_profile
from .web import Browser
from .whatif import estimate, plan_once

MAX_QUESTIONS = 5
_DEADLINE = re.compile(
    r"registration|register|\bdrop|withdraw|\badd\b|classes (?:begin|start)|first (?:class )?day|"
    r"last day|census|payment|tuition|final exam|graduat|apply", re.I)


def spaced(code: str) -> str:
    return re.sub(r"\b([A-Z]{2,4})(\d)", r"\1 \2", code)


# ---------------------------------------------------------------------------
# Deadlines and the calendar file
# ---------------------------------------------------------------------------

def deadlines(term: str, today: dt.date | None = None, facts: list[dict] | None = None,
              limit: int = 10) -> list[dict]:
    """Upcoming calendar facts for the planned term: its own dates, plus dates
    elsewhere that name it ("Registration for Spring 2027 begins" sits in Fall)."""
    today = today or dt.date.today()
    facts = load_facts() if facts is None else facts
    seen, out = set(), []
    for f in sorted(facts, key=lambda f: f["date"]):
        if f["date"] < today.isoformat() or not _DEADLINE.search(f["event"]):
            continue
        if f.get("term") != term and term.lower() not in f["event"].lower():
            continue
        key = (f["event"].lower(), f["date"])
        if key in seen:
            continue
        seen.add(key)
        label = f["event"] if term.lower() in f["event"].lower() else f"{f['event']} ({term})"
        out.append({"event": label, "date": f["date"], "url": f.get("url", "")})
    return out[:limit]


def _ics_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _fold(line: str) -> str:
    """RFC 5545: lines over 75 octets continue on the next line after a space."""
    data, parts = line.encode("utf-8"), []
    limit = 75
    while len(data) > limit:
        cut = limit
        while cut > 1 and (data[cut] & 0xC0) == 0x80:    # don't split a UTF-8 character
            cut -= 1
        parts.append(data[:cut])
        data, limit = data[cut:], 74
    parts.append(data)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def calendar_ics(items: list[dict], term: str, now: dt.datetime | None = None) -> str:
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Bobcat Advisor//Advising deadlines//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(f'TXST deadlines {term}')}"]
    for it in items:
        day = dt.date.fromisoformat(it["date"])
        uid = hashlib.sha1(f"{it['event']}|{it['date']}".encode()).hexdigest()[:20]
        url = re.sub(r"[\x00-\x20\x7f]", "", it.get("url") or "")
        lines += ["BEGIN:VEVENT", f"UID:{uid}@bobcat-advisor", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_ics_text(it['event'])}",
                  "DESCRIPTION:" + _ics_text("From the TXST academic calendar; confirm on the official "
                                             "calendar" + (f": {url}" if url else ".")),
                  *([f"URL:{url}"] if url.startswith("https://") else []),
                  "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(it['event'])}",
                  "TRIGGER:-P1D", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# ---------------------------------------------------------------------------
# The sheet's contents
# ---------------------------------------------------------------------------

@dataclass
class PrepSheet:
    program: str
    term: str
    year: str
    generated: str
    hours_done: int
    hours_total: int | None
    requirements_left: int | None
    graduation_term: str | None
    courses: list[dict] = field(default_factory=list)    # label, title, hours, why, section, backup
    alternates: list[str] = field(default_factory=list)
    roadmap: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    dates: list[dict] = field(default_factory=list)
    sections_note: str = ""
    catalog_year: str | None = None


def _section_text(s) -> str:
    times = ", ".join(m.label() for m in s.meetings) or "online, no set time"
    seats = f", {s.seats_open} open" if s.seats_open is not None else ""
    return f"{s.section}" + (f" | CRN {s.crn}" if s.crn else "") + f"\n{times}{seats}"


def _questions(sheet: PrepSheet, plan, audit, imported: list[str]) -> list[str]:
    qs: list[str] = []
    if sheet.graduation_term:
        qs.append(f"Am I on track to graduate in {sheet.graduation_term}? That's what this plan estimates.")
    qs += [q.strip()[:200] for q in imported if q and q.strip()]
    for c in plan.courses:
        for cond in c.conditions:
            if "prerequisites not checked" in cond:
                qs.append(f"Can I register for {spaced(c.label)} next term? I couldn't confirm its "
                          "prerequisites online.")
            elif re.search(r"gpa|approval|consent|permission", cond, re.I):
                qs.append(f"{spaced(c.label)} requires: {cond}. Do I meet it, and who approves it?")
    for w in plan.warnings:
        if w.startswith("Arrange with the department"):
            what = w.split(": ", 1)[-1].rstrip(".")
            qs.append(f"How and when do I arrange {spaced(what)}?")
    if audit.behind_plan:
        qs.append(f"I'm behind the four-year plan on {', '.join(spaced(c) for c in audit.behind_plan[:3])}. "
                  "Can I catch up without delaying graduation?")
    if plan.alternates and plan.courses:
        alt = plan.alternates[0]
        instead = alt.get("instead_of") or plan.courses[0].code
        qs.append(f"If {spaced(instead)} is full, is {spaced(alt['code'])} a good substitute?")
    return list(dict.fromkeys(qs))[:MAX_QUESTIONS]


def build(raw: dict, today: dt.date | None = None) -> PrepSheet:
    today = today or dt.date.today()
    with span("tool.prep_sheet") as sp:
        profile, flags = build_profile(raw)
        run, _notes = plan_once(profile, Browser())
        plan, audit = run.plan, run.audit
        outcome = estimate(profile, audit, plan, "baseline", "Your current plan", run.research)

        timetable = None
        if plan.courses and profile.planned_term in schedule_terms():
            prefs = Preferences.from_raw({
                "preferred_days": profile.preferred_days, "earliest_start": profile.earliest_start,
                "latest_end": profile.latest_end, "busy": profile.busy, "modality": profile.modality})
            timetable = build_timetable([x for c in plan.courses for x in (c.code, *c.bundle)],
                                        profile.planned_term, prefs)
        option = timetable.options[0] if timetable and timetable.options else None
        chosen = {s.course: s for s in option.sections} if option else {}

        sheet = PrepSheet(
            program=audit.program or f"{profile.major} ({profile.degree})",
            term=profile.planned_term, year=profile.year.title(), generated=today.isoformat(),
            hours_done=audit.hours_completed, hours_total=audit.total_hours,
            requirements_left=len(audit.remaining) if audit.available else None,
            graduation_term=outcome.graduation_term, catalog_year=audit.catalog_year)
        for c in plan.courses:
            codes = [c.code, *c.bundle]
            first = [chosen[x] for x in codes if x in chosen]
            backups = [option.backups.get(x) for x in codes] if option else []
            sheet.courses.append({
                "label": spaced(c.label), "title": c.title, "hours": c.hours,
                "why": c.reasons[0] if c.reasons else c.kind,
                "section": "\n".join(_section_text(s) for s in first),
                "backup": ("\n".join(_section_text(b) for b in backups if b)
                           or ("no other section fits" if first else "")),
            })
        if not plan.courses:
            sheet.sections_note = "No eligible degree courses were found for this term."
        elif timetable is None:
            sheet.sections_note = (f"Class sections for {profile.planned_term} aren't loaded yet; check the "
                                   "schedule of classes for times and CRNs.")
        elif option is None:
            sheet.sections_note = timetable.note or "No clash-free timetable was found for these courses."
        sheet.alternates = [f"{spaced(a['code'])} {a['title']}".strip()
                            + (f" (instead of {spaced(a['instead_of'])})" if a.get("instead_of") else "")
                            for a in plan.alternates]
        sheet.roadmap = [f"{t['term']}: " + (", ".join(spaced(x) for x in t.get("items") or t["courses"])
                                             or "nothing schedulable") + f" ({t['hours']} hrs)"
                         for t in plan.roadmap]
        sheet.checks = list(dict.fromkeys(spaced(x) for x in (
            [f"{c.label}: {'; '.join(c.conditions)}" for c in plan.courses if c.conditions]
            + plan.warnings + flags + run.research.notes)))
        sheet.questions = _questions(sheet, plan, audit, raw.get("import_questions") or [])
        sheet.dates = deadlines(profile.planned_term, today)
        sp.attributes.update(courses=len(sheet.courses), questions=len(sheet.questions),
                             dates=len(sheet.dates), sections=bool(option))
    return sheet


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_LATIN1 = str.maketrans({"—": "-", "–": "-", "→": "->", "‘": "'", "’": "'",
                         "“": '"', "”": '"', "…": "...", "•": "-", "≥": ">=",
                         "≤": "<=", " ": " "})
MAROON = (74, 25, 66)
MUTED = (107, 96, 112)


def _t(text) -> str:
    """Core PDF fonts are Latin-1: map typographic characters, drop the rest."""
    return str(text).translate(_LATIN1).encode("latin-1", "replace").decode("latin-1")


def _render(sheet: PrepSheet, scale: float):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    class Sheet(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*MUTED)
            self.multi_cell(0, 3.2, _t(
                "Not an official degree audit. Computed by Bobcat Advisor from the TXST catalog"
                + (f" ({sheet.catalog_year})" if sheet.catalog_year else "")
                + "; sections, seats and dates change. Confirm everything with your advisor before "
                  "registering."), align="C")

    caps = {"checks": max(3, round(7 * scale)), "roadmap": max(3, round(8 * scale)),
            "dates": max(3, round(6 * scale)), "questions": MAX_QUESTIONS}
    pdf = Sheet(orientation="P", unit="mm", format="Letter")
    pdf.set_title("Advising prep sheet")
    pdf.set_creator("Bobcat Advisor")
    pdf.set_margins(13, 12, 13)
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    lh = 4.3 * scale

    def heading(text: str):
        pdf.ln(2.2 * scale)
        pdf.set_font("Helvetica", "B", 10.5 * scale)
        pdf.set_text_color(*MAROON)
        pdf.cell(0, 5.5 * scale, _t(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*MAROON)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1.2 * scale)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 8.6 * scale)

    def lines(items: list[str], numbered: bool = False):
        for i, item in enumerate(items, 1):
            pdf.multi_cell(0, lh, _t(f"{i}. {item}" if numbered else f"- {item}"), align="L",
                           new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 16 * scale)
    pdf.set_text_color(*MAROON)
    pdf.cell(0, 8 * scale, "Advising prep sheet", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9 * scale)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 4.5 * scale, _t(f"{sheet.program} | {sheet.year} | planning {sheet.term} | "
                                f"prepared {sheet.generated}"), new_x="LMARGIN", new_y="NEXT")
    stats = [f"~{sheet.hours_done}" + (f" of {sheet.hours_total}" if sheet.hours_total else "") + " hours done"]
    if sheet.requirements_left is not None:
        stats.append(f"{sheet.requirements_left} named requirements left")
    if sheet.graduation_term:
        stats.append(f"graduation estimate: {sheet.graduation_term}")
    pdf.set_font("Helvetica", "B", 9 * scale)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 5 * scale, _t("  |  ".join(stats)), new_x="LMARGIN", new_y="NEXT")

    total = sum(c["hours"] for c in sheet.courses)
    heading(f"Next term: {sheet.term} ({total} hours)")
    if sheet.courses:
        with_sections = any(c["section"] for c in sheet.courses)
        cols = ((24, 58, 9, 47, 52) if with_sections else (26, 78, 10, 76))
        heads = (["Course", "Title and why", "Hrs", "First-choice section", "Backup section"]
                 if with_sections else ["Course", "Title", "Hrs", "Why"])
        with pdf.table(col_widths=cols, line_height=lh, padding=(0.8, 1),
                       headings_style=FontFace(emphasis="BOLD", color=MAROON),
                       borders_layout="HORIZONTAL_LINES", text_align="LEFT") as table:
            row = table.row()
            for h in heads:
                row.cell(h)
            for c in sheet.courses:
                row = table.row()
                row.cell(_t(c["label"]))
                if with_sections:
                    row.cell(_t(f"{c['title']}\n{c['why']}"))
                    row.cell(str(c["hours"]))
                    row.cell(_t(c["section"] or "-"))
                    row.cell(_t(c["backup"] or "-"))
                else:
                    row.cell(_t(c["title"]))
                    row.cell(str(c["hours"]))
                    row.cell(_t(c["why"]))
    if sheet.sections_note:
        pdf.set_font("Helvetica", "I", 8 * scale)
        pdf.multi_cell(0, lh, _t(sheet.sections_note), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8.6 * scale)
    if sheet.alternates:
        pdf.multi_cell(0, lh, _t("Backup courses if one is full or cancelled: " + "; ".join(sheet.alternates)),
                       new_x="LMARGIN", new_y="NEXT")

    if sheet.roadmap:
        heading("Roadmap to graduation (tentative)")
        lines(sheet.roadmap[:caps["roadmap"]])
    if sheet.checks:
        heading("Check with your advisor")
        lines(sheet.checks[:caps["checks"]])
    if sheet.questions:
        heading("Questions to ask")
        lines(sheet.questions[:caps["questions"]], numbered=True)
    heading(f"Key dates for {sheet.term}")
    if sheet.dates:
        lines([f"{dt.date.fromisoformat(d['date']):%a %b %d, %Y}: {d['event']}"
               for d in sheet.dates[:caps["dates"]]])
    else:
        pdf.multi_cell(0, lh, _t("No calendar dates are loaded yet: check registration and drop deadlines "
                                 "on the Registrar's academic calendar."), new_x="LMARGIN", new_y="NEXT")
    return pdf


def render_pdf(sheet: PrepSheet) -> bytes:
    """One page: shrink type and trim the longest lists until it fits."""
    for scale in (1.0, 0.92, 0.85, 0.78, 0.7):
        pdf = _render(sheet, scale)
        if pdf.page_no() == 1:
            break
    return bytes(pdf.output())
