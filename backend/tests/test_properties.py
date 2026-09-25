"""
Property-based tests (Hypothesis): invariants that must hold for any input,
not just the examples elsewhere in the suite.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.advising.profile import normalise_codes
from app.advising.web import is_allowed
from app.guardrails import redact_pii
from app.kb.dates import extract_dates
from app.kb.scrub import scrub_people
from app.knowledge import catalog as cat
from app.rag.index import BM25, tokenize
from app.structured.schedule import DAY_ORDER, Meeting, Section, parse_days, parse_time_range
from app.structured.timetable import Preferences, build_timetable, penalty

FAST = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
CATALOG = sorted(cat.load_catalog())

# -- schedule parsing ------------------------------------------------------------

minutes = st.integers(min_value=6 * 60, max_value=22 * 60 - 1)


def _fmt12(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d} {'am' if h < 12 else 'pm'}"


@FAST
@given(start=minutes, length=st.integers(min_value=20, max_value=180))
def test_time_ranges_round_trip_in_every_format(start, length):
    end = start + length
    assume(end < 24 * 60)
    for text in (f"{_fmt12(start)}-{_fmt12(end)}",
                 f"{start // 60:02d}{start % 60:02d}-{end // 60:02d}{end % 60:02d}",
                 f"{start // 60}:{start % 60:02d} - {end // 60}:{end % 60:02d}"):
        assert parse_time_range(text) == (start, end), text


@FAST
@given(st.sets(st.sampled_from("MTWRF"), min_size=1))
def test_days_parse_in_any_spelling_and_order(days):
    names = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri"}
    expected = "".join(d for d in DAY_ORDER if d in days)
    shuffled = sorted(days, key=lambda d: hash(d) % 7)
    assert parse_days("".join(shuffled)) == expected
    assert parse_days(", ".join(names[d] for d in shuffled)) == expected
    assert parse_days(parse_days("".join(days))) == expected          # idempotent


# -- timetable -------------------------------------------------------------------

@st.composite
def instances(draw):
    n_courses = draw(st.integers(min_value=1, max_value=4))
    pool = []
    for c in range(n_courses):
        for s in range(draw(st.integers(min_value=1, max_value=4))):
            days = draw(st.sampled_from(["MW", "TR", "MWF", "F", "M", "TR"]))
            start = draw(st.sampled_from(range(8 * 60, 18 * 60, 30)))
            meetings = [] if draw(st.integers(0, 9)) == 0 else [Meeting(days, start, start + 75)]
            pool.append(Section("Fall 2026", f"C{c}", str(s), meetings=meetings))
    busy = draw(st.lists(st.builds(lambda d, s: Meeting(d, s, s + 120),
                                   st.sampled_from(["TR", "MW", "F"]),
                                   st.sampled_from(range(8 * 60, 18 * 60, 60))), max_size=2))
    prefs = Preferences(preferred_days=draw(st.sampled_from(["", "MWF", "TR"])), busy=busy)
    return [f"C{c}" for c in range(n_courses)], pool, prefs


@FAST
@given(instances())
def test_timetables_are_always_valid_and_optimal(instance):
    courses, pool, prefs = instance
    res = build_timetable(courses, "Fall 2026", prefs, pool=pool)
    placed = [c for c in courses if c not in {u["course"] for u in res.unplaced}]
    # Brute force the optimum over the placeable courses.
    groups = [[s for s in pool if s.course == c and not any(m.overlaps(b) for m in s.meetings
                                                              for b in prefs.busy)] for c in placed]
    feasible = [combo for combo in itertools.product(*groups)
                if not any(a.conflicts(b) for a, b in itertools.combinations(combo, 2))]
    best = min((penalty(list(c), prefs) for c in feasible), default=None) if placed else None
    assert (res.options[0].penalty if res.options else None) == best
    for o in res.options:
        assert sorted(s.course for s in o.sections) == sorted(placed)          # one per course
        assert not any(a.conflicts(b) for a, b in itertools.combinations(o.sections, 2))
        assert not any(m.overlaps(b) for s in o.sections for m in s.meetings for b in prefs.busy)


# -- prerequisite graph ---------------------------------------------------------------

@FAST
@given(st.sets(st.sampled_from(CATALOG), max_size=8))
def test_eligibility_is_consistent_with_prerequisites(completed):
    done = cat.expand_completed(completed)
    assert cat.expand_completed(done) == done                        # closure is idempotent
    assert completed <= done
    for e in cat.eligible_courses(done):
        course = cat.get_course(e["code"])
        assert e["code"] not in done
        for g in course.prereqs:
            cs_only = any(c.startswith("CS") for c in g.courses)
            if cs_only and not g.conditional:
                assert set(g.courses) & done, (e["code"], g.courses)


@FAST
@given(st.sampled_from(CATALOG))
def test_descendants_match_a_naive_walk(code):
    naive, frontier = set(), [code]
    while frontier:
        cur = frontier.pop()
        for c in cat.load_catalog().values():
            if any(cur in g.courses for g in c.prereqs) and c.code not in naive and c.code != code:
                naive.add(c.code)
                frontier.append(c.code)
    assert cat.descendants(code) == naive


# -- text safety ------------------------------------------------------------------------

emails = st.from_regex(r"[a-z]{2,8}\.[a-z]{2,8}@[a-z]{3,8}\.(edu|com|org)", fullmatch=True)
phones = st.from_regex(r"\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}", fullmatch=True)
words = st.lists(st.sampled_from(["course", "grading", "week", "project", "exam", "lab"]),
                 min_size=1, max_size=6).map(" ".join)


@FAST
@given(words, emails, phones, words)
def test_contact_details_never_survive(before, email, phone, after):
    text = f"{before} {email} or {phone} {after}"
    for out in (redact_pii(text), scrub_people(text)[0]):
        assert email not in out and re.sub(r"\D", "", phone) not in re.sub(r"\D", "", out)
        assert before.split()[0] in out


names = st.from_regex(r"[A-Z][a-z]{2,9} [A-Z][a-z]{2,9}", fullmatch=True)


@FAST
@given(st.sampled_from(["Dr.", "Prof.", "Professor", "Dr"]), names)
def test_titled_names_are_removed_from_syllabi(title, name):
    out, removed = scrub_people(f"Questions go to {title} {name} after class.")
    assert name not in out and removed >= 1


# -- URL policy ---------------------------------------------------------------------------

labels = st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True)


@FAST
@given(st.lists(labels, max_size=3), st.sampled_from(["txstate.edu", "txst.edu"]), labels)
def test_url_allowlist_is_exactly_the_domain_suffix(subs, domain, evil):
    host = ".".join([*subs, domain])
    assert is_allowed(f"https://{host}/x")
    assert not is_allowed(f"http://{host}/x")
    assert not is_allowed(f"https://{host}.{evil}.com/x")               # suffix trick
    assume(not evil.endswith(("txstate", "txst")))
    assert not is_allowed(f"https://{evil}{domain}/x")                  # glued prefix


# -- misc parsers ------------------------------------------------------------------------------

@FAST
@given(st.lists(st.sampled_from(CATALOG + ["MATH2471", "ENG1310"]), max_size=6),
       st.sampled_from([", ", " ; ", " and ", "\n"]))
def test_course_codes_normalise_idempotently(codes, sep):
    spaced = sep.join(re.sub(r"([A-Z]+)(\d)", r"\1 \2", c).lower() for c in codes)
    out = normalise_codes(spaced)
    assert out == list(dict.fromkeys(codes))
    assert normalise_codes(out) == out


@FAST
@given(st.dates(min_value=dt.date(2025, 1, 1), max_value=dt.date(2030, 12, 31)))
def test_calendar_dates_parse_with_the_sections_term(date):
    season = "Spring" if date.month <= 5 else "Summer" if date.month <= 7 else "Fall"
    line = f"Last day to drop | {date.strftime('%B')} {date.day}"
    facts = extract_dates(line, f"Calendar > {season} {date.year}", "u", "2026-01-01")
    assert [f.date for f in facts] == [date.isoformat()]


@FAST
@given(st.lists(words, min_size=2, max_size=6), words)
def test_bm25_scores_are_non_negative_and_favour_matches(docs, query):
    bm = BM25([tokenize(d) for d in docs])
    scores = bm.scores(tokenize(query))
    assert (scores >= 0).all()
    for doc, s in zip(docs, scores, strict=True):
        if not set(tokenize(doc)) & set(tokenize(query)):
            assert s == 0
