"""Advising prep sheet (PDF), the deadline calendar (.ics), registration-day
backup sections, backup courses, imported hours and anonymous usage counts."""

from __future__ import annotations

import datetime as dt
import io

import pytest
from pypdf import PdfReader

from app.advising import prep
from app.advising.profile import build_profile
from app.advising.web import parse_html
from app.structured import schedule
from app.structured.schedule import Meeting, Section
from app.structured.timetable import Preferences, TimetableOption, backup_sections, build_timetable
from app.tracing import start_trace
from tests.schedule_fixtures import FALL_2026_HTML

SOPHOMORE = {"year": "sophomore", "semester": "Fall", "term_year": 2026, "target_credits": 15,
             "completed": ["CS1428", "CS2308", "MATH2471"]}
FACTS = [
    {"event": "Registration for Fall 2026 begins", "date": "2026-04-06", "term": "Spring 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Classes begin", "date": "2026-08-24", "term": "Fall 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Last day to drop with a W", "date": "2026-10-28", "term": "Fall 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Last day to drop with a W", "date": "2026-10-28", "term": "Fall 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Thanksgiving break", "date": "2026-11-25", "term": "Fall 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Last day to drop", "date": "2026-03-20", "term": "Spring 2026", "url": "https://www.txst.edu/cal"},
    {"event": "Classes begin", "date": "2025-08-25", "term": "Fall 2025", "url": "https://www.txst.edu/cal"},
]


@pytest.fixture
def sections(_empty_schedule_store):
    schedule.save_sections(schedule.sections_from_page(parse_html(FALL_2026_HTML), "Fall 2026"))


@pytest.fixture
def facts(monkeypatch):
    monkeypatch.setattr(prep, "load_facts", lambda: FACTS)


# -- deadlines and the calendar file ------------------------------------------------

def test_deadlines_for_the_planned_term():
    got = prep.deadlines("Fall 2026", today=dt.date(2026, 3, 1), facts=FACTS)
    assert [(d["date"], d["event"]) for d in got] == [
        ("2026-04-06", "Registration for Fall 2026 begins"),     # in Spring's section, names Fall
        ("2026-08-24", "Classes begin (Fall 2026)"),
        ("2026-10-28", "Last day to drop with a W (Fall 2026)"),  # duplicate removed
    ]                                                             # no holidays, other terms or past dates
    assert prep.deadlines("Fall 2026", today=dt.date(2026, 9, 1), facts=FACTS)[0]["date"] == "2026-10-28"


def test_calendar_file_is_valid_ics():
    items = prep.deadlines("Fall 2026", today=dt.date(2026, 3, 1), facts=FACTS)
    items[0]["event"] = "Registration, part one; for Fall 2026 " + "x" * 80
    ics = prep.calendar_ics(items, "Fall 2026", now=dt.datetime(2026, 3, 1, tzinfo=dt.UTC))
    assert ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 3 and ics.count("TRIGGER:-P1D") == 3
    assert "DTSTART;VALUE=DATE:20260406\r\nDTEND;VALUE=DATE:20260407" in ics
    assert "Registration\\, part one\\; for" in ics                 # escaped
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))   # folded
    unfolded = ics.replace("\r\n ", "")
    assert "x" * 80 in unfolded and "DTSTAMP:20260301T000000Z" in unfolded


def test_fold_never_splits_a_character():
    line = "SUMMARY:" + "é" * 60
    folded = prep._fold(line)
    assert folded.replace("\r\n ", "") == line
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n"))


def test_calendar_api(client, monkeypatch):
    monkeypatch.setattr(prep, "load_facts", lambda: [{**f, "date": "2099-01-10", "term": "Spring 2099"}
                                                     for f in FACTS[:3]])
    r = client.get("/api/advise/calendar.ics", params={"term": "Spring 2099"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert 'filename="txst-deadlines-spring-2099.ics"' in r.headers["content-disposition"]
    missing = client.get("/api/advise/calendar.ics", params={"term": "Fall 2098"})
    assert missing.status_code == 404 and "No upcoming academic calendar dates" in missing.json()["detail"]
    assert client.get("/api/advise/calendar.ics", params={"term": "Fall\r\n2099"}).status_code == 422


# -- registration-day backups -------------------------------------------------------------

def _sec(course, section, days, start, crn="", seats=5):
    return Section("Fall 2026", course, section, crn=crn,
                   meetings=[Meeting(days, start, start + 75)] if days else [], seats_open=seats)


def test_backup_fits_the_rest_of_the_week():
    a1, a2, a3 = _sec("A", "1", "MW", 600), _sec("A", "2", "TR", 600), _sec("A", "3", "MW", 780, seats=30)
    b1 = _sec("B", "1", "TR", 600)
    option = TimetableOption([a1, b1], 0)
    backups = backup_sections(option, {"A": [a1, a2, a3], "B": [b1]}, Preferences())
    assert backups["A"] is a3           # a2 clashes with B; a3 fits
    assert backups["B"] is None         # no other section


def test_timetable_carries_backups_and_crns(sections):
    tt = build_timetable(["CS3358", "MATH2358"], "Fall 2026", Preferences())
    d = tt.options[0].to_dict()
    assert d["crns"] == ", ".join(s["crn"] for s in d["sections"])
    for course, backup in d["backups"].items():
        chosen = next(s for s in d["sections"] if s["course"] == course)
        assert backup is None or (backup["id"] != chosen["id"] and not backup["full"])


# -- backup courses, imported hours -------------------------------------------------------

def test_plan_lists_backup_courses(txst_web):
    from app.advising.web import Browser
    from app.advising.whatif import plan_once
    profile, _ = build_profile({**SOPHOMORE, "target_credits": 6})
    with start_trace():
        run, _ = plan_once(profile, Browser())
    picks = {c.code for c in run.plan.courses}
    assert run.plan.alternates and len(run.plan.alternates) <= 3
    assert not picks & {a["code"] for a in run.plan.alternates}
    assert all(a["reason"] for a in run.plan.alternates)


def test_imported_hours_replace_the_estimate(txst_web):
    from app.advising.pipeline import advise
    with start_trace():
        guessed = advise(SOPHOMORE)
        imported = advise({**SOPHOMORE, "hours_earned": 45})
    assert guessed["audit"]["hours_completed"] == 11 and imported["audit"]["hours_completed"] == 45
    assert any("fewer than a typical sophomore" in f for f in guessed["flags"])
    assert not any("fewer than a typical" in f for f in imported["flags"])


# -- the prep sheet ---------------------------------------------------------------------------

def _text(pdf: bytes) -> tuple[int, str]:
    reader = PdfReader(io.BytesIO(pdf))
    return len(reader.pages), " ".join((p.extract_text() or "") for p in reader.pages)


def test_prep_sheet_contents(txst_web, sections, facts):
    with start_trace():
        sheet = prep.build({**SOPHOMORE, "import_questions": ["Which requirements do my transfer credits "
                                                             "(HIST 1XXX) count for?"]},
                           today=dt.date(2026, 3, 1))
    assert [c["label"] for c in sheet.courses] == ["MATH 2358", "PHIL 1305"]
    math = sheet.courses[0]
    assert "CRN 10020" in math["section"] and "CRN 10021" in math["backup"]
    assert sheet.courses[1]["backup"] == "no other section fits"
    assert sheet.questions[0].startswith("Am I on track to graduate in")
    assert "Which requirements do my transfer credits (HIST 1XXX) count for?" in sheet.questions
    assert len(sheet.questions) <= prep.MAX_QUESTIONS
    assert [d["date"] for d in sheet.dates] == ["2026-04-06", "2026-08-24", "2026-10-28"]
    assert all("CS2315" not in c for c in sheet.checks)          # codes spaced for reading

    pages, text = _text(prep.render_pdf(sheet))
    assert pages == 1
    for expected in ("Advising prep sheet", "MATH 2358", "CRN 10020", "CRN 10021", "Questions to ask",
                     "Registration for Fall 2026 begins", "Not an official degree audit"):
        assert expected in text


def test_prep_sheet_without_sections_or_dates(txst_web, monkeypatch):
    monkeypatch.setattr(prep, "load_facts", lambda: [])
    with start_trace():
        sheet = prep.build(SOPHOMORE, today=dt.date(2026, 3, 1))
    assert "aren't loaded yet" in sheet.sections_note and sheet.dates == []
    _, text = _text(prep.render_pdf(sheet))
    assert "No calendar dates are loaded yet" in text and "Backup section" not in text


def test_a_long_sheet_still_fits_one_page():
    sheet = prep.PrepSheet(program="Computer Science (B.S.)", term="Fall 2026", year="Junior",
                           generated="2026-03-01", hours_done=60, hours_total=120, requirements_left=12,
                           graduation_term="Spring 2028")
    sheet.courses = [{"label": f"CS 43{i}0", "title": "A long course title about many things " * 2,
                      "hours": 3, "why": "required for your degree; unlocks 4 courses",
                      "section": "001 | CRN 1000\nMWF 09:00-09:50, 5 open",
                      "backup": "002 | CRN 1001\nTR 11:00-12:20, 15 open"} for i in range(6)]
    sheet.roadmap = [f"Term {i}: CS 4310, CS 4320, CS 4330, CS 4340 (12 hrs)" for i in range(8)]
    sheet.checks = ["Something to confirm with a fairly long explanation that wraps " * 2] * 9
    sheet.questions = ["A question that is long enough to wrap onto a second line of the page? " * 2] * 5
    sheet.dates = [{"event": "Last day to drop — “quoted” → arrows", "date": "2026-10-28"}] * 6
    pages, text = _text(prep.render_pdf(sheet))
    assert pages == 1 and "Last day to drop - \"quoted\" -> arrows" in text


def test_prep_sheet_api(client, txst_web):
    r = client.post("/api/advise/prep-sheet", json=SOPHOMORE)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert 'filename="advising-prep-fall-2026.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and _text(r.content)[0] == 1


# -- usage counters ------------------------------------------------------------------------------

def test_usage_counts_are_anonymous_counters(client):
    from app.database import SessionLocal
    from app.models import UsageCount
    for _ in range(3):
        assert client.post("/api/events", json={"event": "prep_sheet"}).status_code == 204
    assert client.post("/api/events", json={"event": "view_advisor"}).status_code == 204
    assert client.post("/api/events", json={"event": "my name is Jane"}).status_code == 422
    usage = client.get("/api/analytics").json()["usage_30d"]
    assert usage["prep_sheet"] >= 3 and usage["view_advisor"] >= 1
    with SessionLocal() as db:
        row = db.get(UsageCount, (dt.date.today(), "prep_sheet"))
        assert set(UsageCount.__table__.columns.keys()) == {"day", "event", "count"} and row.count >= 3


def test_usage_events_are_rate_limited(client, monkeypatch):
    from app.routers import events
    from app.services.protection import RateLimiter
    monkeypatch.setattr(events, "limiter", RateLimiter(per_minute=2))
    codes = [client.post("/api/events", json={"event": "crn_copy"}).status_code for _ in range(3)]
    assert codes == [204, 204, 429]
