"""Catalog parsing, prerequisite graph, course registry, cleaning."""

import pytest

from app.knowledge import catalog as cat
from app.knowledge.corpus import registry
from app.rag.cleaner import normalise_course


@pytest.mark.parametrize("raw,expected", [
    ("CS 3358", ("CS3358", ["CS3358"])),
    ("1428", ("CS1428", ["CS1428"])),
    ("HONORSCS1428", ("CS1428", ["CS1428"])),
    ("CS3358004", ("CS3358", ["CS3358"])),
    ("CS23183358", ("CS2318", ["CS2318", "CS3358"])),
    ("CS ASSEMBLY", ("CS2318", ["CS2318"])),
    ("CS WHATEVER", ("", [])),
    ("CS230", ("", [])),
    ("UNKNOWN", ("", [])),
])
def test_normalise_course(raw, expected):
    assert normalise_course(raw) == expected


def test_prereq_parsing_cnf():
    groups, other = cat.parse_prereqs(
        'CS 2308 and [CS 2318 or EE 3320] both with grades of "C" or better.')
    assert [g.courses for g in groups] == [["CS2308"], ["CS2318", "EE3320"]]
    assert other == []


def test_prereq_parsing_non_course_requirements():
    groups, other = cat.parse_prereqs("CS 4298 with a grade of \"C\" or better and instructor approval.")
    assert [g.courses for g in groups] == [["CS4298"]]
    assert other == ["instructor approval"]


def test_prereq_conditional_alternative():
    course = cat.get_course("CS1428")
    assert course.prereqs[0].conditional  # ACT/SAT alternatives


def test_catalog_loaded():
    catalog = cat.load_catalog()
    assert len(catalog) >= 40
    assert catalog["CS3358"].title == "Data Structures and Algorithms"


def test_expand_completed_is_transitive():
    assert cat.expand_completed({"CS3358"}) >= {"CS3358", "CS2308", "CS1428", "MATH2358"}


def test_expand_completed_does_not_guess_or_groups():
    # CS3339 needs CS2318 OR EE3320 — can't infer which one was taken
    expanded = cat.expand_completed({"CS3339"})
    assert "CS2318" not in expanded and "EE3320" not in expanded


def test_eligibility():
    ok, missing, _ = cat.is_eligible("CS3360", {"CS2308", "CS3358"})
    assert not ok and missing == [["CS2318", "EE3320"]]
    ok, missing, _ = cat.is_eligible("CS3360", {"CS2318", "CS3358"})
    assert ok and missing == []


def test_eligible_courses_after_foundations():
    codes = {c["code"] for c in cat.eligible_courses(cat.expand_completed({"CS2308", "MATH2358"}))}
    assert {"CS2318", "CS3358"} <= codes
    assert "CS4328" not in codes


def test_unlocks():
    assert {"CS3354", "CS3360", "CS3378"} <= set(cat.unlocks("CS3358"))


@pytest.mark.parametrize("text,courses", [
    ("CS3358 or cs 2308", ["CS3358", "CS2308"]),
    ("data structures", ["CS3358"]),
    ("I took 1428 in 2024", ["CS1428"]),                      # year isn't a course
    ("MATH 2358 first", ["MATH2358"]),
])
def test_registry_courses(text, courses):
    assert registry().match_courses(text) == courses


def test_catalog_only_corpus():
    from app.knowledge.corpus import load_chunks
    chunks = load_chunks()
    assert chunks and {c["metadata"]["source_dir"] for c in chunks} == {"official"}
