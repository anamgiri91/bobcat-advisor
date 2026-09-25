"""What-if planning: graduation estimates under switching major, adding a
minor, failing a course and changing the load (catalog pages from
tests/txst_fixtures.py)."""

from __future__ import annotations

import pytest

from app.advising.audit import AuditResult, RequirementStatus
from app.advising.profile import build_profile
from app.advising.scheduler import SchedulePlan
from app.advising.whatif import estimate, what_if

# Nearly done: CS3358 -> {CS3360, advanced electives} left (CS3398 too, but its
# CS3354 prerequisite isn't a listed requirement, so it stays unplaced). 30
# filler courses of 3 hours put the student close to the 120-hour total.
FILLER = [f"HIST{1300 + i}" for i in range(30)]
SENIOR = {
    "year": "senior", "semester": "Fall", "term_year": 2026, "target_credits": 15,
    "completed": ["CS1428", "CS2308", "CS2315", "CS2318", "CS3339",
                  "MATH2471", "MATH2358", "PHIL1305", *FILLER],
}
SOPHOMORE = {"year": "sophomore", "semester": "Fall", "term_year": 2026, "target_credits": 15,
             "completed": ["CS1428", "CS2308", "MATH2471"]}


def _by_name(result: dict) -> dict:
    return {s["description"]: s for s in result["scenarios"]}


# -- estimate() in isolation -------------------------------------------------

def _audit(total: int | None, completed: int, remaining: list[tuple[str, int]]) -> AuditResult:
    return AuditResult(available=True, program="X", total_hours=total, hours_completed=completed,
                       requirements=[RequirementStatus(id=c, section="s", label=c, options=[c], hours=h,
                                                       status="remaining") for c, h in remaining])


def test_estimate_takes_the_slower_of_roadmap_and_hours():
    profile, _ = build_profile({"semester": "Fall", "term_year": 2026, "target_credits": 15})
    plan = SchedulePlan(term="Fall 2026", mode="degree", target_credits=15,
                        roadmap=[{"term": t, "courses": ["CS1"], "hours": 3}
                                 for t in ("Fall 2026", "Spring 2027", "Fall 2027")])
    # Roadmap-bound: 3 terms of chained courses, only 9 hours left.
    o = estimate(profile, _audit(120, 111, [("CS1", 3)]), plan, "b", "b")
    assert (o.terms_remaining, o.graduation_term) == (3, "Fall 2027")
    # Hours-bound: 60 hours at 15/term = 4 terms.
    o = estimate(profile, _audit(120, 60, [("CS1", 3)]), plan, "b", "b")
    assert (o.terms_remaining, o.graduation_term, o.hours_remaining) == (4, "Spring 2028", 60)
    assert any("Total hours set the pace" in n for n in o.notes)


def test_estimate_without_verified_requirements():
    profile, _ = build_profile({})
    o = estimate(profile, AuditResult(available=False), SchedulePlan("Fall 2026", "prerequisites_only", 15),
                 "b", "b")
    assert o.graduation_term is None and "couldn't be verified" in o.notes[0]


# -- scenarios ---------------------------------------------------------------

def test_failing_a_prerequisite_near_graduation_costs_a_term(txst_web):
    r = what_if(SENIOR, [{"type": "fail_course", "course": "CS 3358"}])
    base, fail = r["baseline"], r["scenarios"][0]
    assert base["terms_remaining"] == 2 and base["graduation_term"] == "Spring 2027"
    assert fail["delta_terms"] == 1 and fail["graduation_term"] == "Fall 2027"
    assert fail["delta_hours"] == 3
    assert fail["roadmap"][0]["failed"] == ["CS3358"]
    assert "CS3358" in fail["roadmap"][1]["courses"]                     # retaken next term


def test_failing_a_completed_course_puts_it_back_in_the_plan(txst_web):
    r = what_if(SENIOR, [{"type": "fail_course", "course": "CS2318"}])
    s = r["scenarios"][0]
    assert s["requirements_remaining"] == r["baseline"]["requirements_remaining"] + 1
    assert any("no longer counts as passed" in n for n in s["notes"])
    assert "CS2318" in s["roadmap"][0]["courses"]


def test_lighter_load_graduates_later(txst_web):
    r = what_if(SOPHOMORE, [{"type": "change_load", "target_credits": 9}])
    s = r["scenarios"][0]
    assert s["delta_terms"] > 0 and s["graduation_term"] > r["baseline"]["graduation_term"] or \
        s["terms_remaining"] > r["baseline"]["terms_remaining"]


def test_switch_major_uses_the_other_programs_requirements(txst_web):
    r = what_if(SOPHOMORE, [{"type": "switch_major", "major": "Computer Science", "degree": "BA"}])
    s = r["scenarios"][0]
    assert s["program"] == "Computer Science (B.A.)"
    assert s["requirements_remaining"] < r["baseline"]["requirements_remaining"]


def test_adding_a_minor_adds_its_requirements(txst_web):
    r = what_if(SENIOR, [{"type": "add_minor", "minor": "Data Science"}])
    s, base = r["scenarios"][0], r["baseline"]
    # CS3358 overlaps; CS4315, CS4332 and 6 elective hours are new.
    assert s["requirements_remaining"] == base["requirements_remaining"] + 2
    assert s["hours_remaining"] > base["hours_remaining"] and s["delta_terms"] >= 0
    assert any("double counting" in n for n in s["notes"])
    placed = {c for t in s["roadmap"] for c in t["courses"]}
    assert {"CS4315", "CS4332"} <= placed


def test_unknown_minor_is_reported_not_guessed(txst_web):
    s = what_if(SOPHOMORE, [{"type": "add_minor", "minor": "Underwater Basketry"}])["scenarios"][0]
    assert s["graduation_term"] is None and "Couldn't read" in s["notes"][0]


def test_bad_inputs(txst_web):
    r = what_if(SOPHOMORE, [{"type": "fail_course", "course": "nope"}, {"type": "teleport"}])
    assert r["scenarios"][0]["notes"] == ["Not a course code."]
    assert "Unknown scenario type" in r["scenarios"][1]["notes"][0]


def test_whatif_is_deterministic(txst_web):
    sc = [{"type": "fail_course", "course": "CS3358"}, {"type": "change_load", "target_credits": 12}]
    assert what_if(SENIOR, sc) == what_if(SENIOR, sc)


# -- API -----------------------------------------------------------------------

def test_api_whatif(client, txst_web):
    r = client.post("/api/advise/whatif", json={
        "profile": SENIOR, "scenarios": [{"type": "fail_course", "course": "CS3358"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["planned_term"] == "Fall 2026" and body["scenarios"][0]["delta_terms"] == 1
    assert body["trace"]["spans"] > 0


@pytest.mark.parametrize("payload", [
    {"profile": SOPHOMORE, "scenarios": []},
    {"profile": SOPHOMORE, "scenarios": [{"type": "teleport"}]},
    {"profile": SOPHOMORE, "scenarios": [{"type": "change_load", "target_credits": 40}]},
    {"profile": SOPHOMORE, "scenarios": [{"type": "fail_course"}] * 5},
])
def test_api_whatif_validation(client, payload):
    assert client.post("/api/advise/whatif", json=payload).status_code == 422
