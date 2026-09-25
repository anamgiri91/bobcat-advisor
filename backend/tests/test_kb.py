"""
Knowledge base: section splitting, people scrubbing, date extraction,
crawling, the build, freshness, and the kb / calendar / search agents.
Pages come from tests/kb_fixtures.py (test data, not real TXST policy).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import numpy as np
import pytest

from app.advising import web
from app.agents.orchestrator import answer
from app.agents.router import route_rules
from app.agents.specialists import _fresh, run_calendar, run_kb, run_search
from app.agents.state import QueryPlan
from app.config import settings
from app.kb import calendar
from app.kb.build import build, overdue
from app.kb.crawl import Crawler
from app.kb.dates import extract_dates
from app.kb.scrub import REMOVED, is_people_section, scrub_people
from app.kb.sections import html_sections, text_sections
from app.kb.sources import load_sources, sources_by_id
from app.rag import index as index_mod
from app.tracing import start_trace
from tests import kb_fixtures as fx
from tests.txst_fixtures import FakeFetcher

TODAY = dt.date(2026, 9, 25)


@pytest.fixture
def kb_web():
    fetcher = FakeFetcher(fx.routes())
    web.set_fetcher(fetcher)
    yield fetcher
    web.set_fetcher(None)


@pytest.fixture
def kb_data(kb_web, tmp_path, monkeypatch):
    """Build the fixture knowledge base into a temp data dir."""
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    manifest = build(["catalog_policy", "registrar", "syllabi"], today=TODAY)
    return tmp_path, manifest


def _chunks(path) -> list[dict]:
    return [json.loads(line) for line in (path / "kb_chunks.jsonl").open()]


def _fake_embed(text: str) -> np.ndarray:
    seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(384).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def kb_index(kb_data, monkeypatch):
    """Catalog index + fixture KB chunks, searchable offline (fake query vectors;
    BM25 carries the ranking)."""
    data_dir, _ = kb_data
    real = index_mod.HybridIndex(settings.CHROMA_DB_PATH)
    kb = _chunks(data_dir)
    combined = index_mod.HybridIndex(data={
        "ids": real.ids + [c["id"] for c in kb],
        "documents": real.texts + [c["text"] for c in kb],
        "metadatas": real.metas + [c["metadata"] for c in kb],
        "embeddings": np.vstack([real.emb] + [_fake_embed(c["text"]) for c in kb]),
    })
    monkeypatch.setattr(index_mod, "embed_query", _fake_embed)
    monkeypatch.setattr(settings, "RETRIEVAL_MODE", "bm25")   # fake vectors carry no meaning
    index_mod.set_index(combined)
    yield combined
    index_mod.set_index(None)


# -- sources ---------------------------------------------------------------

def test_every_requested_source_is_registered():
    kinds = {s.kind for s in load_sources()}
    assert kinds == {"policy", "core", "grad_catalog", "department", "registrar", "handbook", "syllabus"}
    assert all(web.is_allowed(u) for s in load_sources() for u in s.seeds)
    assert sources_by_id()["syllabi"].scrub_people


# -- sections --------------------------------------------------------------

def test_sections_follow_headings_and_drop_page_chrome():
    title, sections = html_sections(fx.CATALOG_POLICIES)
    paths = [s.path_str for s in sections]
    assert "Academic Policies > Withdrawal > Deadlines" in paths
    text = "\n".join(s.text for s in sections)
    assert "Menu" not in text and "athletics" not in text and "webmaster" not in text
    load = next(s for s in sections if s.heading == "Course Load")
    assert "Full-time | 12 or more" in load.text
    assert title.startswith("Academic Policies")


def test_pdf_text_sections_detect_headings():
    text = "\n".join(fx.SYLLABUS_PDF_LINES)
    headings = [s.heading for s in text_sections(text, "CS 4371")]
    assert "Course Description" in headings and "Use of AI" in headings


# -- scrubbing -------------------------------------------------------------

def test_scrub_people_removes_names_and_contacts():
    text, n = scrub_people("Instructor: Dr. Jane Placeholder\nEmail: jp@example.edu\n"
                           "Ask Prof. Jane Placeholder or call 512-555-0100.\nWeek 1: big-O")
    assert "Placeholder" not in text and "jp@" not in text and "555" not in text
    assert REMOVED in text and "Week 1: big-O" in text and n >= 3
    assert is_people_section("Instructor Information") and is_people_section("Office Hours")
    assert not is_people_section("Grading")


# -- dates -----------------------------------------------------------------

def test_dates_take_the_year_from_the_section_term():
    facts = extract_dates("Last day to drop | Oct. 28\nRegistration for Spring 2027 opens | Nov. 2",
                          "Calendar > Fall 2026", "https://www.registrar.txstate.edu/c", "2026-09-25")
    assert [(f.date, f.term) for f in facts] == [("2026-10-28", "Fall 2026"), ("2026-11-02", "Fall 2026")]


def test_dates_use_the_academic_year_and_never_guess():
    facts = extract_dates("Winter break begins | Dec. 19\nClasses resume | Jan. 19",
                          "Calendar 2026-2027", "u", "2026-09-25")
    assert [f.date for f in facts] == ["2026-12-19", "2027-01-19"]
    assert extract_dates("Last day to drop | Oct. 28", "Calendar", "u", "2026-09-25") == []


def test_calendar_lookup_marks_past_and_upcoming():
    facts = [{"event": "Last day to drop a class", "date": d, "term": t, "url": "u",
              "section": "", "fetched_at": "2026-09-25"}
             for d, t in (("2026-03-26", "Spring 2026"), ("2026-10-28", "Fall 2026"))]
    got = calendar.lookup("When is the last day to drop?", today=TODAY, facts=facts)
    assert [(g["date"], g["status"]) for g in got] == [("2026-10-28", "upcoming"), ("2026-03-26", "past")]
    assert calendar.lookup("When is the last day?", today=TODAY, facts=facts) == []


# -- crawling --------------------------------------------------------------

def test_crawler_scope(kb_web):
    result = Crawler().crawl(sources_by_id()["catalog_policy"])
    urls = [p.url for p, _ in result.pages]
    assert urls == [f"{fx.CAT}/undergraduate/academic-policies/"]    # hub not indexed
    assert result.skipped_robots == 1                                 # /private/ disallowed
    assert not any("evil.example.com" in u or u.endswith(".jpg") or "/about/" in u
                   for u in kb_web.requests)


def test_pdf_pages_are_read(kb_web):
    page = web.Browser().fetch(f"{fx.SYL}/syllabi/cs4371.pdf")
    assert page.is_pdf and "Generative AI tools" in page.text


# -- build -----------------------------------------------------------------

def test_build_tags_every_chunk(kb_data):
    data_dir, manifest = kb_data
    chunks = _chunks(data_dir)
    policy = [c for c in chunks if c["metadata"]["chunk_type"] == "policy"]
    assert policy and all(c["metadata"]["catalog_year"] == "2026-2027" for c in policy)
    for c in chunks:
        md = c["metadata"]
        assert md["url"].startswith("https://") and md["fetched_at"] == "2026-09-25"
        assert md["heading"] in c["text"].split("\n\n")[0]          # header names the section
    withdrawal = next(c for c in policy if c["metadata"]["heading"] == "Withdrawal")
    assert "Deadlines:" in withdrawal["text"]                          # tiny child merged into parent
    assert manifest["syllabi"]["errors"]                                # the 404 seed is reported


def test_syllabi_have_no_people_details(kb_data):
    data_dir, manifest = kb_data
    syl = [c for c in _chunks(data_dir) if c["metadata"]["chunk_type"] == "syllabus"]
    text = "\n".join(c["text"] for c in syl)
    for leaked in ("Placeholder", "Sam Example", "@example.edu", "555-0100", "Office hours", "Comal 210"):
        assert leaked not in text
    assert {c["metadata"]["course"] for c in syl} == {"CS3358", "CS4371"}   # MATH syllabus skipped
    assert manifest["syllabi"]["people_sections_dropped"] >= 2


def test_dates_file_and_partial_rebuild(kb_data):
    data_dir, _ = kb_data
    dates = [json.loads(line) for line in (data_dir / "kb_dates.jsonl").open()]
    assert ("2027-03-26", "Spring 2027") in {(d["date"], d["term"]) for d in dates}
    before = {c["id"] for c in _chunks(data_dir) if c["metadata"]["source"] == "catalog_policy"}
    build(["registrar"], today=TODAY)
    after = {c["id"] for c in _chunks(data_dir) if c["metadata"]["source"] == "catalog_policy"}
    assert before == after                          # other sources kept


def test_freshness_check(kb_data):
    _, manifest = kb_data
    late = overdue(manifest, today=TODAY + dt.timedelta(days=8))
    assert any(x.startswith("registrar: fetched 8 days ago") for x in late)   # weekly source
    assert any(x.startswith("cs_department: never fetched") for x in late)
    assert not any(x.startswith("syllabi") for x in late)


# -- agents ------------------------------------------------------------------

def test_policy_questions_route_to_the_knowledge_base():
    for q in ("Can I retake CS2308 to replace a D?", "What's the last day to drop?",
              "How many hours count as full-time?", "Can I use AI on assignments?",
              "Can I take a 5000-level course as an undergrad?", "Is there a BS/MS fast track?"):
        assert route_rules(q).intent == "policy", q
    assert route_rules("What's the textbook for CS3358?").intent == "course_info"


def test_kb_agent_finds_the_policy_section(kb_index):
    res = run_kb(QueryPlan(intent="policy", standalone_question="Can I repeat a course to replace a D grade?"))
    assert res.evidence[0].kind == "policy"
    assert "Repeating a Course" in res.evidence[0].label and "2026-2027 catalog" in res.evidence[0].label
    assert res.evidence[0].metadata["url"].endswith("/academic-policies/")


def test_search_agent_returns_only_the_named_courses_syllabus(kb_index):
    res = run_search(QueryPlan(intent="course_info", standalone_question="What's the textbook for CS3358?",
                               courses=["CS3358"]))
    syl = [e for e in res.evidence if e.kind == "syllabus"]
    assert syl and all(e.metadata["course"] == "CS3358" for e in syl)
    assert any("Textbook" in e.text for e in syl)


def test_calendar_agent_marks_status(kb_data):
    res = run_calendar(QueryPlan(intent="policy", standalone_question="What's the last day to drop a class?"),
                       today=TODAY)
    text = res.evidence[0].text
    assert "2026-10-28 (Fall 2026) — UPCOMING, in 33 days" in text
    assert res.evidence[0].kind == "dates"


def test_stale_pages_are_not_used(monkeypatch):
    monkeypatch.setattr(settings, "KB_MAX_AGE_DAYS", 30)
    chunks = [{"metadata": {"fetched_at": "2026-01-01"}}, {"metadata": {"fetched_at": "2026-09-20"}},
              {"metadata": {}}]
    keep, stale = _fresh(chunks, today=TODAY)
    assert stale == 1 and len(keep) == 2


def test_policy_answer_offline_is_cited_with_urls(kb_index):
    with start_trace():
        out = answer("Can I repeat a course to replace a D grade?")
    assert out["plan"]["intent"] == "policy" and out["mode"] == "extractive"
    kinds = {s["kind"] for s in out["sources"]}
    assert "policy" in kinds
    assert all(s["url"] for s in out["sources"] if s["kind"] == "policy")
    assert "confirm on the cited official page" in out["answer"]


def test_kb_eval_scores_crawled_sources_and_skips_the_rest(kb_index):
    from pathlib import Path

    from evals.run_eval import eval_kb_retrieval, load_cases
    res = eval_kb_retrieval(load_cases(Path("evals/golden.jsonl")))
    assert res["n"] > 0
    assert {"q01", "d01", "m01"} <= set(res["skipped_not_crawled"])   # core/department/grad not crawled
    assert "y01" not in res["skipped_not_crawled"]                    # syllabi were
    assert res["hit_by_source"]["syllabus"] == 1.0
