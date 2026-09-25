"""
Class schedule store, offering history and the timetable builder, plus
their use in the Advisor and the API. Data comes from
tests/schedule_fixtures.py (test data, not TXST's schedule).
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import random

import pytest

from app.advising.web import parse_html
from app.structured import schedule as sched
from app.structured.build import main as build_main
from app.structured.build import term_window
from app.structured.offerings import compute_offerings
from app.structured.schedule import (
    Meeting,
    Section,
    parse_days,
    parse_time_range,
    save_sections,
    sections_from_page,
)
from app.structured.timetable import Preferences, build_timetable, parse_block, penalty
from app.tracing import start_trace
from tests import schedule_fixtures as fx

FALL = "Fall 2026"


@pytest.fixture
def fall_sections():
    return sections_from_page(parse_html(fx.FALL_2026_HTML), FALL)


@pytest.fixture
def schedule_store(fall_sections, _empty_schedule_store, tmp_path):
    """Fall 2026 sections plus four past terms of history in the (temp) store."""
    save_sections(fall_sections)
    for term, text in fx.PAST_TERMS.items():
        path = tmp_path / f"{term}.csv"
        path.write_text(text)
        assert build_main(["--import-csv", str(path), "--term", term]) == 0
    return _empty_schedule_store


# -- parsing ---------------------------------------------------------------

def test_days_and_times():
    assert parse_days("TuTh") == "TR" and parse_days("M W F") == "MWF" and parse_days("TBA") == ""
    assert parse_time_range("2:00-3:15 pm") == (840, 915)
    assert parse_time_range("11:00-12:20 pm") == (660, 740)
    assert parse_time_range("1000-1120") == (600, 680)
    assert parse_block("TR 12:00-17:00") == Meeting("TR", 720, 1020)
    assert parse_block("MWF 1pm-5pm") == Meeting("MWF", 780, 1020)
    assert parse_block("Sat 9-13") == Meeting("S", 540, 780)


def test_schedule_table_is_parsed_by_header_names(fall_sections):
    by_id = {s.id: s for s in fall_sections}
    assert len(fall_sections) == 8
    s = by_id["CS3358.001"]
    assert s.crn == "10001" and s.meetings == [Meeting("MW", 600, 680)]
    assert (s.seats_total, s.seats_open) == (40, 5) and s.modality == "in person"
    assert by_id["CS3358.002"].is_full and by_id["CS3358.002"].waitlist == 3
    assert by_id["CS3358.251"].meetings == [] and by_id["CS3358.251"].modality == "online"
    # The lab row with no CRN is a second meeting of MATH 2358.002.
    assert by_id["MATH2358.002"].meetings == [Meeting("TR", 660, 740), Meeting("F", 840, 950)]


def test_instructor_names_are_never_stored(schedule_store):
    stored = schedule_store.read_text()
    for name in ("Placeholder", "Alex Example", "Sam Sample", "Robin Test", "Casey Demo"):
        assert name not in stored
    assert "instructor" not in json.loads(stored.splitlines()[0])


def test_saving_a_term_keeps_other_terms(schedule_store):
    assert sched.terms() == ["Fall 2024", "Spring 2025", "Fall 2025", "Spring 2026", FALL]
    save_sections([Section(FALL, "CS1428", "001")])
    assert "Fall 2024" in sched.terms()
    assert [s.course for s in sched.load_sections() if s.term == FALL] == ["CS1428"]


def test_term_window():
    assert term_window(dt.date(2026, 9, 25), 1, 1) == ["Summer 2026", "Fall 2026", "Spring 2027"]


# -- offering history --------------------------------------------------------

def test_offering_history(schedule_store):
    off = compute_offerings()
    cs3360 = off["CS3360"]
    assert cs3360.offered == {"Fall": 2, "Spring": 0, "Summer": 0}
    assert cs3360.not_usually_offered("Spring") and not cs3360.not_usually_offered("Fall")
    assert cs3360.label().startswith("usually offered in Fall")
    assert not off["CS3358"].not_usually_offered("Spring")
    # MATH appears in one term only: too little history to say anything about Spring.
    assert not off["MATH2358"].not_usually_offered("Spring")
    assert off["MATH2358"].observed["Spring"] == 0          # CS history doesn't count for MATH


# -- timetable -----------------------------------------------------------------

def _no_clashes(option) -> bool:
    return not any(a.conflicts(b) for a, b in itertools.combinations(option.sections, 2))


@pytest.mark.parametrize("solver", ["ortools", "search"])
def test_timetable_avoids_clashes_and_full_sections(schedule_store, solver, monkeypatch):
    monkeypatch.setattr("app.config.settings.TIMETABLE_SOLVER", solver)
    res = build_timetable(["CS3358", "CS2318", "MATH2358"], FALL)
    assert res.solver == solver and res.options
    for o in res.options:
        assert _no_clashes(o)
        assert "CS3358.002" not in {s.id for s in o.sections}      # full
    assert len({tuple(s.id for s in o.sections) for o in res.options}) == len(res.options)


def test_busy_blocks_and_preferred_days(schedule_store):
    prefs = Preferences.from_raw({"busy": ["TR 12:00-17:00"], "preferred_days": "MWF"})
    best = build_timetable(["CS3358", "CS2318", "MATH2358"], FALL, prefs).options[0]
    assert not any(m.overlaps(b) for s in best.sections for m in s.meetings for b in prefs.busy)
    ids = {s.id for s in best.sections}
    assert "CS2318.002" not in ids                  # TR 2pm overlaps work
    assert "MATH2358.002" not in ids                # TR 11:00-12:20 runs into work at noon
    assert best.days and set(best.days) <= set("MWF")


def test_unplaceable_courses_are_explained(schedule_store):
    prefs = Preferences.from_raw({"earliest_start": "10:30", "modality": "in person"})
    res = build_timetable(["CS3358", "CS9999", "PHIL1305"], FALL, prefs)
    reasons = {u["course"]: u["reason"] for u in res.unplaced}
    assert reasons["CS9999"] == "no sections listed for Fall 2026"
    assert "starts before 10:30" in reasons["CS3358"] or "full" in reasons["CS3358"]
    assert [s.id for s in res.options[0].sections] == ["PHIL1305.001"]


def test_impossible_pair_is_named():
    a = [Section(FALL, "CS1", "1", meetings=[Meeting("MW", 600, 680)])]
    b = [Section(FALL, "CS2", "1", meetings=[Meeting("M", 630, 700)])]
    res = build_timetable(["CS1", "CS2"], FALL, pool=a + b)
    assert res.options == [] and "CS1 and CS2 can't both fit" in res.note


def test_both_solvers_find_equally_good_timetables(monkeypatch):
    rng = random.Random(7)
    starts = [480, 570, 660, 750, 840, 930]
    for trial in range(15):
        pool = []
        for c in range(4):
            for s in range(rng.randint(1, 4)):
                days = rng.choice(["MW", "TR", "MWF", "F", "TR"])
                start = rng.choice(starts)
                pool.append(Section(FALL, f"C{c}", str(s), meetings=[Meeting(days, start, start + 80)]))
        courses = [f"C{c}" for c in range(4)]
        prefs = Preferences(preferred_days=rng.choice(["", "MWF", "TR"]))
        results = {}
        for solver in ("ortools", "search"):
            monkeypatch.setattr("app.config.settings.TIMETABLE_SOLVER", solver)
            results[solver] = build_timetable(courses, FALL, prefs, pool=pool)
        best = {k: (r.options[0].penalty if r.options else None) for k, r in results.items()}
        assert best["ortools"] == best["search"], (trial, best)
        for r in results.values():
            for o in r.options:
                assert _no_clashes(o) and o.penalty == penalty(o.sections, prefs)


# -- Advisor integration ------------------------------------------------------

def _advise_raw(**extra):
    return {"year": "sophomore", "semester": "Fall", "term_year": 2026, "target_credits": 15,
            "completed": ["CS1428", "CS2308", "MATH2471"], "interests": ["AI"], **extra}


def test_advisor_builds_a_timetable_for_its_picks(schedule_store, txst_web):
    from app.advising.pipeline import run
    with start_trace():
        events = list(run(_advise_raw(busy=["TR 12:00-17:00"])))
    tt = next(e for e in events if e["type"] == "timetable")["timetable"]
    assert tt["term"] == FALL and tt["options"]
    picked = {s["id"] for s in tt["options"][0]["sections"]}
    assert "MATH2358.001" in picked or "MATH2358.002" in picked
    done = events[-1]
    assert done["timetable"]["term"] == FALL
    assert "**Your week**" in done["answer"]
    assert any(s["label"].startswith("Timetable builder") for s in done["sources"])


def test_scheduler_uses_offering_history_and_the_terms_sections(schedule_store, txst_web):
    from app.advising.audit import audit
    from app.advising.factcheck import fact_check
    from app.advising.profile import build_profile
    from app.advising.research import research
    from app.advising.scheduler import plan_schedule
    from app.advising.web import Browser

    profile, _ = build_profile(_advise_raw(semester="Spring", term_year=2027,
                                           completed=["CS1428", "CS2308", "MATH2471", "MATH2358",
                                                      "CS2318", "CS3358"]))
    browser = Browser()
    found = research(profile, browser)
    report, verified = fact_check(found, profile, browser.pages)
    plan = plan_schedule(profile, verified, audit(profile, verified, report), report)
    reasons = {d["code"]: d["reason"] for d in plan.deferred}
    assert reasons["CS3360"].startswith("not usually offered in Spring")
    assert plan.roadmap[0]["term"] == "Spring 2027" and plan.roadmap[1]["term"] == "Fall 2027"
    later = [c for t in plan.roadmap[1:] for c in t["courses"]]
    assert "CS3360" in later                                   # placed in a Fall instead


def test_no_sections_for_the_planned_term_means_not_this_term(schedule_store, txst_web):
    from app.advising.pipeline import advise
    with start_trace():
        done = advise(_advise_raw())
    reasons = {d["code"]: d["reason"] for d in done["schedule"]["deferred"]}
    assert reasons["CS2315"] == "no sections listed for Fall 2026"
    offered = {x.course for x in sched.load_sections() if x.term == FALL}
    assert {c["code"] for c in done["schedule"]["courses"]} <= offered


# -- API ------------------------------------------------------------------------

def test_api(client, schedule_store):
    assert FALL in client.get("/api/schedule/terms").json()["terms"]
    secs = client.get("/api/schedule/sections", params={"course": "cs 3358", "term": FALL}).json()
    assert len(secs["sections"]) == 3 and "instructor" not in json.dumps(secs).lower()
    assert client.get("/api/offerings/CS3360").json()["usual_seasons"] == ["Fall"]
    r = client.post("/api/timetable", json={"term": FALL, "courses": ["CS3358", "CS 2318"],
                                            "preferred_days": "TR"})
    assert r.status_code == 200 and r.json()["options"]
    assert client.post("/api/timetable", json={"term": "Fall 1999", "courses": ["CS3358"]}).status_code == 404
