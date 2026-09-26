"""
CourseLeaf layouts that the first requirement parser misread on the live
TXST page: required courses swallowed by an earlier "Select N hours" group,
alternatives read as all-required, a lecture and its lab split into two
requirements, and a course that doesn't count toward the major accepted.
The page below is illustrative test data, not the official degree plan.
"""

from __future__ import annotations

from app.advising import web
from app.advising.audit import audit
from app.advising.factcheck import fact_check
from app.advising.profile import build_profile
from app.advising.research import clean_requirements, parse_requirements, research
from app.advising.scheduler import plan_schedule
from app.tracing import start_trace
from tests.txst_fixtures import PROGRAM_URL, FakeFetcher


def _row(code, title, hours="3", indent=False, cls="even"):
    code_html = f'<a href="/search/?P={code}">{code}</a>'
    if indent:
        code_html = f'<div class="blockindent">{code_html}</div>'
    return (f'<tr class="{cls}"><td class="codecol">{code_html}</td><td>{title}</td>'
            f'<td class="hourscol">{hours}</td></tr>')


def _pair(lecture, lab, title, lab_title):
    return (f'<tr class="odd"><td class="codecol"><a href="#">{lecture}</a>'
            f'<span class="blockindent">&amp; <a href="#">{lab}</a></span></td>'
            f'<td>{title}<span class="blockindent">and {lab_title}</span></td><td class="hourscol">4</td></tr>')


def _area(text):
    return (f'<tr class="areaheader"><td colspan="2"><span class="courselistcomment areaheader">{text}'
            '</span></td><td class="hourscol"></td></tr>')


def _comment(text, hours=""):
    return (f'<tr><td colspan="2"><span class="courselistcomment">{text}</span></td>'
            f'<td class="hourscol">{hours}</td></tr>')


TABLE = (
    '<table class="sc_courselist"><tbody>'
    + _area("Major Requirements")
    + _row("CS 1428", "Foundations of Computer Science I", "4")
    + _row("CS 2308", "Foundations of Computer Science II")
    + _comment("Select 3 hours from the following:", "3")
    + _row("CS 2315", "Computer Ethics", indent=True)
    + _row("PHIL 1320", "Ethics and Society", indent=True)
    + _row("CS 2318", "Assembly Language")                       # unindented: required again
    + _row("CS 3358", "Data Structures and Algorithms")
    + _area("Choose one of the following:")                     # a heading that is a rule
    + _row("CS 4398", "Software Engineering Project")
    + _row("HON 4390B", "Honors Thesis")
    + _area("Support Courses")
    + _row("CS 1319", "Fundamentals of Computer Science")        # doesn't count for a CS BS
    + _row("MATH 2471", "Calculus I", "4")
    + _row("MATH 2472", "Calculus II", "4")
    + _pair("PHYS 2325", "PHYS 2125", "General Physics I", "General Physics I Laboratory")
    + _pair("PHYS 2326", "PHYS 2126", "General Physics II", "General Physics II Laboratory")
    + _area("Experience")
    + _row("CS 3190", "Cooperative Education", "1")
    + _row("CS 4100", "Computer Science Internship", "1")
    + _row("CS 4298", "Undergraduate Research I")
    + _row("RES 4399", "Undergraduate Research Project")
    + '<tr class="listsum"><td colspan="2">Total Hours</td><td class="hourscol">120</td></tr>'
    + "</tbody></table>"
)
PAGE = (f"<html><head><title>Computer Science (B.S.) &lt; Texas State</title></head><body>"
        f"<p>2025-2026 Undergraduate Catalog</p><p>A minimum of 120 semester credit hours.</p>{TABLE}</body></html>")


def _parsed():
    page = web.Page(url=PROGRAM_URL, status=200, parsed=web.parse_html(PAGE, PROGRAM_URL), fetched_at=0)
    return parse_requirements(page)


def test_an_indented_group_ends_at_the_next_required_course():
    reqs, pools, _ = _parsed()
    labels = [r.label() for r in reqs]
    assert pools[0].options == ["CS2315", "PHIL1320"] and pools[0].hours_required == 3
    assert "CS2318" in labels and "CS3358" in labels          # not swallowed by the group


def test_a_heading_that_is_a_rule_is_a_group():
    reqs, pools, _ = _parsed()
    assert pools[1].rule == "Choose one of the following:"
    assert pools[1].options == ["CS4398", "HON4390B"]
    assert "HON4390B" not in {o for r in reqs for o in r.options}


def test_lecture_and_lab_are_one_four_hour_requirement():
    reqs, _, titles = _parsed()
    phys = next(r for r in reqs if r.options == ["PHYS2325"])
    assert phys.bundle == ["PHYS2125"] and phys.hours == 4 and phys.label() == "PHYS2325 + PHYS2125"
    assert titles["PHYS2325"] == "General Physics I and General Physics I Laboratory"
    assert not any(r.options == ["PHYS2125"] for r in reqs)


def test_clean_up_drops_non_counting_courses_and_merges_placements():
    reqs, pools, _ = _parsed()
    reqs, pools, notes = clean_requirements(reqs, pools, "Computer Science")
    options = [r.options for r in reqs]
    assert ["CS1319"] not in options
    assert ["CS3190", "CS4100", "CS4298", "RES4399"] in options    # any one, not all four
    assert any("Left out CS1319" in n and "Does not count" in n for n in notes)
    assert [r.id for r in reqs] == [f"R{i}" for i in range(1, len(reqs) + 1)]


def test_the_plan_is_short_real_and_keeps_labs_with_lectures():
    web.set_fetcher(FakeFetcher({PROGRAM_URL: PAGE}))
    profile, _ = build_profile({"year": "sophomore", "semester": "Fall", "target_credits": 15,
                                "completed": ["CS1428", "CS2308", "MATH2471"]})
    with start_trace():
        browser = web.Browser()
        found = research(profile, browser)
        report, verified = fact_check(found, profile, browser.pages)
        result = audit(profile, verified, report)
        plan = plan_schedule(profile, verified, result, report)
    picks = {c.code: c for c in plan.courses}
    assert not {"CS1319", "CS3190", "CS4100", "CS4298", "RES4399", "HON4390B"} & set(picks)
    assert picks["PHYS2325"].bundle == ["PHYS2125"] and picks["PHYS2325"].hours == 4
    assert picks["PHYS2325"].title.startswith("General Physics I")
    assert "PHYS2326" not in picks                                # follows PHYS 2325
    assert "MATH2472" in picks and plan.total_hours <= 16
    assert any(w.startswith("Arrange with the department") for w in plan.warnings)
    first = plan.roadmap[0]
    assert "PHYS2325 + PHYS2125" in first["items"] and "PHYS2125" in first["courses"]
    later = [t for t in plan.roadmap if "PHYS2326" in t["courses"]]
    assert later and later[0]["term"] != first["term"]
    assert any("Left out CS1319" in n for n in found.notes)
