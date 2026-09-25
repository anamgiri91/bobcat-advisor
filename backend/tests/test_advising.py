"""
Course-recommendation pipeline: browser sandbox, CourseLeaf parsing,
fact-checking, degree audit, scheduling, and the end-to-end agent stream.
Catalog pages come from tests/txst_fixtures.py; no test touches the network.
"""

from __future__ import annotations

import json

import pytest

from app import llm
from app.advising import web
from app.advising.audit import audit
from app.advising.factcheck import CONFLICT, UNVERIFIED, fact_check
from app.advising.pipeline import run
from app.advising.profile import build_profile, credit_hours, normalise_codes
from app.advising.research import (
    Requirement,
    parse_course_page,
    parse_requirements,
    parse_sequence,
    research,
)
from app.advising.scheduler import interest_terms, plan_schedule
from app.config import settings
from app.tracing import start_trace
from tests.txst_fixtures import BASE, MOVED_URL, PROGRAM_URL, FakeFetcher, pages

SOPHOMORE = {"year": "sophomore", "semester": "Fall", "completed": ["cs 1428", "CS2308", "MATH 2471"],
             "interests": ["AI"], "target_credits": 15}


def _pipeline(raw: dict):
    profile, flags = build_profile(raw)
    browser = web.Browser()
    found = research(profile, browser)
    report, verified = fact_check(found, profile, browser.pages)
    result = audit(profile, verified, report)
    return profile, browser, found, report, verified, result


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

def test_course_codes_are_normalised():
    assert normalise_codes(["cs 2308", "MATH2471, eng-1310", "CS2308"]) == [
        "CS2308", "MATH2471", "ENG1310"]
    assert normalise_codes("the 2024 catalog and CS 3358") == ["CS3358"]
    assert credit_hours("CS1428") == 4 and credit_hours("CS3358") == 3


def test_profile_flags():
    p, flags = build_profile({"year": "3rd", "gpa": 1.8, "target_credits": 20,
                              "completed": ["CS1428"], "notes": "I passed MATH 2358 in summer"})
    assert p.year == "junior"
    assert any("below 2.0" in f for f in flags)
    assert any("overload" in f for f in flags)
    assert any("MATH2358" in f for f in flags)          # asked about, not silently added
    assert "MATH2358" not in p.completed
    assert any("fewer than a typical junior" in f for f in flags)


def test_blank_year_and_catalog_year_formats():
    p, flags = build_profile({"year": "   ", "catalog_year": "2025-26"})
    assert p.year == "freshman" and not flags
    assert p.catalog_year == "2025-2026"
    assert build_profile({"catalog_year": "2025/2026"})[0].catalog_year == "2025-2026"
    assert build_profile({"catalog_year": "2025-2031"})[0].catalog_year is None


def test_in_progress_counts_for_planning_only():
    p, _ = build_profile({"completed": ["CS1428"], "in_progress": ["CS2308", "CS1428"]})
    assert p.in_progress == ["CS2308"]
    assert p.planning_completed == ["CS1428", "CS2308"]


# ---------------------------------------------------------------------------
# Browser sandbox
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://mycatalog.txstate.edu/x/", True),
    ("https://www.cs.txstate.edu/", True),
    ("https://txst.edu/", True),
    ("http://mycatalog.txstate.edu/", False),               # plain HTTP
    ("https://txstate.edu.evil.com/", False),              # suffix trick
    ("https://eviltxstate.edu/", False),
    ("https://user:pw@mycatalog.txstate.edu/", False),
    ("https://mycatalog.txstate.edu:8080/", False),
    ("https://169.254.169.254/latest/meta-data/", False),
    ("file:///etc/passwd", False),
])
def test_url_allowlist(url, ok):
    assert web.is_allowed(url) is ok


def test_redirect_off_domain_is_blocked():
    web.set_fetcher(FakeFetcher({
        PROGRAM_URL: (302, {"location": "https://169.254.169.254/"}, ""),
    }))
    with pytest.raises(web.FetchError, match="redirect"):
        web.Browser().fetch(PROGRAM_URL)


def test_page_budget_and_cache(txst_web):
    b = web.Browser(max_pages=1)
    b.fetch(PROGRAM_URL)
    with pytest.raises(web.FetchError, match="budget"):
        b.fetch(BASE + "/search/?P=CS+3358")
    # A second request is served from the process cache without a network fetch.
    b2 = web.Browser(max_pages=0)
    b2.fetch(PROGRAM_URL)
    assert b2.visits[-1].cached
    assert txst_web.requests.count(PROGRAM_URL) == 1


def test_browsing_can_be_disabled(txst_web, monkeypatch):
    monkeypatch.setattr(settings, "WEB_BROWSING_ENABLED", False)
    with pytest.raises(web.FetchError, match="disabled"):
        web.Browser().fetch(PROGRAM_URL)


def test_parser_drops_scripts_and_styles():
    parsed = web.parse_html(pages()[PROGRAM_URL], PROGRAM_URL)
    assert "tracking" not in parsed.text and ".x{}" not in parsed.text
    assert parsed.title.startswith("Computer Science (B.S.)")


# ---------------------------------------------------------------------------
# CourseLeaf parsing
# ---------------------------------------------------------------------------

def _page(url: str) -> web.Page:
    return web.Page(url=url, status=200, parsed=web.parse_html(pages()[url], url), fetched_at=0)


def test_requirement_tables():
    reqs, pools, titles = parse_requirements(_page(PROGRAM_URL))
    by_code = {r.options[0]: r for r in reqs}
    assert len(reqs) == 11
    assert by_code["CS1428"].hours == 4 and by_code["CS1428"].section == "Major Requirements"
    assert by_code["PHIL1305"].options == ["PHIL1305", "PHIL1320"]      # "or" row merged
    assert by_code["MATH2358"].section == "Supporting Courses"
    assert len(pools) == 1
    assert pools[0].hours_required == 6
    assert pools[0].options == ["CS4346", "CS4371", "CS4332"]
    assert titles["CS3358"] == "Data Structures and Algorithms"


def test_four_year_plan():
    seq = parse_sequence(_page(PROGRAM_URL))
    terms = {(t.year, t.term): t.items for t in seq}
    assert terms[("First Year", "Fall")] == ["CS1428", "MATH2471"]
    assert terms[("Second Year", "Spring")] == ["CS3339", "Core Curriculum Component"]


def test_course_page():
    url = BASE + "/search/?P=CS+3358"
    c = parse_course_page(_page(url), "CS3358")
    assert c.title == "Data Structures and Algorithms" and c.hours == 3
    assert c.prereq_text.startswith("CS 2308 and MATH 2358")
    assert "Course Attributes" not in c.description
    assert parse_course_page(_page(url), "CS3360") is None


def test_program_found_through_search_when_url_moved():
    routes = pages()
    routes[MOVED_URL] = routes.pop(PROGRAM_URL)       # known URL now 404s
    fetcher = FakeFetcher(routes)
    web.set_fetcher(fetcher)
    p, _ = build_profile(SOPHOMORE)
    found = research(p, web.Browser())
    assert found.program.found_by == "search"
    assert found.program.url == MOVED_URL
    assert len(found.requirements) == 11
    # The off-domain look-alike search result was never followed.
    assert not any("evil" in u for u in fetcher.requests)


# ---------------------------------------------------------------------------
# Fact-checking
# ---------------------------------------------------------------------------

def test_fact_check_verifies_and_flags_conflicts(txst_web):
    _, _, _, report, verified, _ = _pipeline(SOPHOMORE)
    assert report.status("R6") == "verified"                     # CS3358 on page + snapshot
    assert report.status("course:CS2318") == CONFLICT            # live prereqs differ
    conflict = next(c for c in report.checks if c.id == "course:CS2318")
    assert "MATH2471" in conflict.reasons[-1]
    assert report.summary["unverified"] == 0
    assert len(verified.requirements) == 11


def test_fabricated_llm_quote_is_dropped(txst_web):
    p, _ = build_profile(SOPHOMORE)
    browser = web.Browser()
    found = research(p, browser)
    found.requirements.append(Requirement(
        id="R99", section="Major Requirements", options=["CS4999"], hours=3,
        quote="CS 4999 Quantum Basket Weaving", source_url=PROGRAM_URL, method="llm"))
    found.requirements.append(Requirement(
        id="R98", section="Major Requirements", options=["CS3358"], hours=3,
        quote="CS 3358", source_url="https://example.com/fake", method="llm"))
    report, verified = fact_check(found, p, browser.pages)
    assert report.status("R99") == UNVERIFIED
    assert report.status("R98") == UNVERIFIED
    assert all(r.id not in ("R98", "R99") for r in verified.requirements)


def test_catalog_year_mismatch_is_a_conflict(txst_web):
    _, _, _, report, _, _ = _pipeline({**SOPHOMORE, "catalog_year": "2023-2024"})
    assert report.status("program") == CONFLICT


# ---------------------------------------------------------------------------
# Audit and schedule
# ---------------------------------------------------------------------------

def test_audit(txst_web):
    _, _, _, _, _, result = _pipeline({**SOPHOMORE, "completed": [*SOPHOMORE["completed"], "CS4371"]})
    d = result.to_dict()
    assert d["counts"]["done"] == 3                     # CS1428, CS2308, MATH2471
    assert result.total_hours == 120
    assert result.hours_completed == 4 + 3 + 4 + 3
    pool = result.pools[0]
    assert pool.hours_done == 3 and pool.hours_left == 3 and pool.status == "remaining"
    assert "MATH2358" in result.behind_plan              # plan puts it in first-year spring


def test_schedule_respects_prereqs_load_and_requirements(txst_web):
    profile, _, _, report, verified, result = _pipeline(SOPHOMORE)
    plan = plan_schedule(profile, verified, result, report)
    codes = [c.code for c in plan.courses]
    assert codes[0] == "MATH2358"                        # required, behind plan, unlocks most
    assert "CS3358" not in codes                          # needs MATH2358 first
    assert "CS2318" not in codes
    assert plan.total_hours <= profile.target_credits + 1
    assert not ({"PHIL1305", "PHIL1320"} <= set(codes))   # one pick per requirement
    deferred = {d["code"]: d["reason"] for d in plan.deferred}
    assert "MATH2358" in deferred["CS3358"]
    # Roadmap puts CS3358 after MATH2358 and uses the AI elective later.
    order = [c for t in plan.roadmap for c in t["courses"]]
    assert order.index("MATH2358") < order.index("CS3358")
    assert "CS4346" in order


def test_stricter_prereqs_win_on_conflict(txst_web):
    # Live CS2318 page also requires MATH2471; the snapshot doesn't.
    raw = {**SOPHOMORE, "completed": ["CS1428", "CS2308", "MATH2358", "ENG1310"]}
    profile, _, _, report, verified, result = _pipeline(raw)
    plan = plan_schedule(profile, verified, result, report)
    assert "CS2318" not in [c.code for c in plan.courses]
    assert "MATH2471" in next(d["reason"] for d in plan.deferred if d["code"] == "CS2318")


def test_interests_steer_electives(txst_web):
    raw = {"year": "junior", "completed": ["CS1428", "CS2308", "MATH2471", "MATH2358", "CS3358",
                                           "CS2318"], "interests": ["cybersecurity"]}
    profile, _, _, report, verified, result = _pipeline(raw)
    plan = plan_schedule(profile, verified, result, report)
    electives = [c.code for c in plan.courses if c.kind == "elective"]
    assert electives and electives[0] == "CS4371"
    rec = next(c for c in plan.courses if c.code == "CS4371")
    assert any("cybersecurity" in r for r in rec.reasons)
    assert "Upper Division" not in json.dumps(interest_terms(profile))


def test_prerequisites_only_when_catalog_unreachable():
    profile, flags = build_profile(SOPHOMORE)
    found = research(profile, web.Browser())            # offline fetcher
    assert not found.found and found.errors
    report, verified = fact_check(found, profile, {})
    result = audit(profile, verified, report)
    plan = plan_schedule(profile, verified, result, report)
    assert not result.available
    assert plan.mode == "prerequisites_only"
    assert plan.courses and all(c.kind == "eligible" for c in plan.courses)
    assert any("couldn't be verified" in w for w in plan.warnings)


def test_llm_extraction_fallback_for_unknown_layout(monkeypatch):
    page = ("<html><head><title>CS BS</title></head><body><p>2025-2026 catalog</p>"
            "<p>Students must complete CS 3358 Data Structures and Algorithms.</p></body></html>")
    web.set_fetcher(FakeFetcher({PROGRAM_URL: page}))

    class Extractor:
        def complete(self, model, messages, max_tokens, temperature, json_mode, **kw):
            return json.dumps({"requirements": [
                {"section": "Major", "options": ["CS 3358"], "hours": 3,
                 "quote": "Students must complete CS 3358 Data Structures and Algorithms."},
                {"section": "Major", "options": ["CS 4999"], "hours": 3,
                 "quote": "Students must complete CS 4999."}],
                "pools": [], "total_hours": None}), {"prompt_tokens": 10, "completion_tokens": 10}

    llm.set_backend(Extractor())
    profile, _ = build_profile(SOPHOMORE)
    browser = web.Browser()
    found = research(profile, browser)
    assert found.method == "llm" and len(found.requirements) == 2
    report, verified = fact_check(found, profile, browser.pages)
    assert [r.options for r in verified.requirements] == [["CS3358"]]   # hallucination dropped


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_pipeline_stream_without_llm(txst_web):
    with start_trace():
        events = list(run(SOPHOMORE))
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    for t in ("profile", "browse", "research", "factcheck", "audit", "schedule", "sources", "token"):
        assert t in types
    assert types.index("browse") < types.index("factcheck") < types.index("schedule")
    done = events[-1]
    assert done["mode"] == "extractive"
    assert "MATH2358" in done["answer"]
    assert done["verification"]["citations"]["invalid_citations"] == []
    agents = [e["agent"] for e in events if e["type"] == "agent" and e["status"] == "done"]
    assert agents == ["intake", "researcher", "fact_checker", "auditor", "scheduler", "advisor"]


def test_pipeline_with_llm_verifies_memo(txst_web, fake_llm):
    fake = fake_llm(
        answer="**Where you stand**\nYou have 8 named requirements left to finish [4].\n"
               "- MATH2358 Discrete Mathematics I is your top priority this fall [5].\n"
               "- You should also take CS 4999 because it is very easy and fun [5].",
        verifier_fn=lambda m: {"verdicts": [{"i": 4, "supported": False, "reason": "not in evidence"}]},
    )
    with start_trace() as trace:
        events = list(run(SOPHOMORE))
    done = events[-1]
    assert done["mode"] == "llm"
    assert "CS 4999" not in done["answer"]
    assert "MATH2358 Discrete Mathematics I" in done["answer"]
    assert any(e["type"] == "revision" for e in events)
    assert fake.calls.count("verifier") == 1
    assert trace.llm_calls <= settings.MAX_LLM_CALLS_PER_REQUEST


def test_api_advise(client, txst_web):
    r = client.post("/api/advise", json=SOPHOMORE)
    assert r.status_code == 200
    body = r.json()
    assert body["schedule"]["courses"][0]["code"] == "MATH2358"
    assert body["research"]["program"]["catalog_year"] == "2025-2026"
    assert body["visits"] and all(v["url"].startswith("https://mycatalog.txstate.edu") for v in body["visits"])
    assert body["trace"]["spans"]


def test_api_advise_stream(client, txst_web):
    with client.stream("POST", "/api/advise/stream", json=SOPHOMORE) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "event: browse" in text and "event: schedule" in text
    assert text.rstrip().split("\n\n")[-1].startswith("event: done")


def test_api_validates_input(client):
    assert client.post("/api/advise", json={"target_credits": 40}).status_code == 422
    assert client.post("/api/advise", json={"gpa": 5}).status_code == 422
