"""Index build pipeline (chunk -> clean -> ingest -> incremental embed), the
schedule build CLI, and the LLM client's model checks and error paths."""

from __future__ import annotations

import json
import runpy
import sys

import httpx
import numpy as np
import pytest

from app import llm
from app.config import settings
from app.rag import embed
from app.rag.chunker import chunk_catalog
from app.rag.cleaner import clean_chunks, normalise_course
from app.rag.ingest import ingest_all, save_jsonl

CATALOG_TEXT = """CS 1428. Foundations of Computer Science I.

Problem solving and C++. Prerequisite: MATH 2471 with a grade of "C" or better.

----------

CS 3358. Data Structures and Algorithms.

Classic data structures. Prerequisite: CS 2308 and MATH 2358 both with grades of "C" or better.

----------

HONORS CS 1428. Honors Foundations.

Honors section.
"""


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "official").mkdir()
    (tmp_path / "official" / "catalog.txt").write_text(CATALOG_TEXT)
    return tmp_path


# -- chunk / clean / ingest ----------------------------------------------------------

def test_catalog_chunks_one_per_entry(docs):
    chunks = list(chunk_catalog(docs / "official" / "catalog.txt"))
    assert [c["metadata"]["course"] for c in chunks] == ["CS1428", "CS3358", "HONORSCS1428"]
    assert all(c["metadata"]["chunk_type"] == "catalog" and "professor" not in c["metadata"] for c in chunks)
    assert len({c["id"] for c in chunks}) == 3                     # content-hash ids


def test_cleaner_normalises_course_codes(docs):
    chunks, report = clean_chunks(list(chunk_catalog(docs / "official" / "catalog.txt")))
    assert [c["metadata"]["course"] for c in chunks] == ["CS1428", "CS3358", "CS1428"]
    assert chunks[2]["metadata"]["course_raw"] == "HONORSCS1428"
    assert report["courses_normalised"] == 1
    assert normalise_course(None) == ("", [])


def test_ingest_dedupes_and_ignores_unknown_folders(docs, capsys):
    (docs / "official" / "copy.txt").write_text(CATALOG_TEXT)       # byte-identical duplicate
    (docs / "rmp").mkdir()
    (docs / "rmp" / "someone.txt").write_text("review text")         # not an ingested source
    chunks = ingest_all(docs)
    assert len(chunks) == 3
    assert "3 exact duplicates" in capsys.readouterr().out
    with pytest.raises(FileNotFoundError):
        ingest_all(docs / "missing")


def test_ingest_cli_merges_the_knowledge_base(docs, tmp_path, monkeypatch):
    kb = tmp_path / "kb.jsonl"
    kb.write_text(json.dumps({"id": "kb1", "text": "Withdrawal policy", "metadata": {"chunk_type": "policy"}}) + "\n")
    out = tmp_path / "out" / "chunks.jsonl"
    monkeypatch.setattr(sys, "argv", ["ingest", "--documents-dir", str(docs), "--out", str(out), "--kb", str(kb)])
    runpy.run_module("app.rag.ingest", run_name="__main__")
    ids = [json.loads(line)["id"] for line in out.open()]
    assert len(ids) == 4 and ids[-1] == "kb1"


def test_ingest_cli_preview(docs, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ingest", "--documents-dir", str(docs), "--preview", "1"])
    with pytest.raises(SystemExit):
        runpy.run_module("app.rag.ingest", run_name="__main__")
    assert '"chunk_type": "catalog"' in capsys.readouterr().out


# -- incremental embedding ----------------------------------------------------------

class FakeEmbedder:
    calls: list[int] = []

    def __init__(self, model_name):
        pass

    def embed(self, texts):
        FakeEmbedder.calls.append(len(texts))
        for t in texts:
            v = np.zeros(384, dtype=np.float32)
            v[hash(t) % 384] = 1.0
            yield v


def test_embedding_is_incremental(docs, tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "TextEmbedding", FakeEmbedder)
    FakeEmbedder.calls = []
    chunks_path, db = tmp_path / "chunks.jsonl", tmp_path / "db"
    chunks = ingest_all(docs)
    save_jsonl(chunks, chunks_path)
    embed.build_index(chunks_path, db)
    assert FakeEmbedder.calls == [3]
    embed.build_index(chunks_path, db)                     # unchanged: nothing re-embedded
    assert FakeEmbedder.calls == [3]
    save_jsonl(chunks[:2], chunks_path)                    # one entry removed
    embed.build_index(chunks_path, db)
    import chromadb
    col = chromadb.PersistentClient(path=str(db)).get_collection(embed.COLLECTION_NAME)
    assert col.count() == 2 and FakeEmbedder.calls == [3]


# -- schedule build CLI ------------------------------------------------------------------

def test_schedule_fetch_is_a_noop_without_a_url(capsys):
    from app.structured.build import main
    assert main([]) == 0
    assert "No schedule URL configured" in capsys.readouterr().out


def test_schedule_fetch_with_a_configured_url(monkeypatch, _empty_schedule_store, capsys):
    from app.advising import web
    from app.structured import build as sbuild
    from tests.schedule_fixtures import FALL_2026_HTML
    from tests.txst_fixtures import FakeFetcher

    url = "https://www.registrar.txstate.edu/schedule?term=202670&subject={subject}"
    routes = {url.format(subject=s): FALL_2026_HTML for s in ("CS", "MATH")}
    web.set_fetcher(FakeFetcher(routes))
    monkeypatch.setattr(sbuild, "load_config", lambda: {
        "url_templates": ["https://www.registrar.txstate.edu/schedule?term={term_code}&subject={subject}"],
        "subjects": ["CS", "MATH", "PHIL"], "term_codes": {"Fall": "{year}70", "Spring": "{year}10",
                                                          "Summer": "{year}30"},
        "past_terms": 0, "future_terms": 0})
    assert sbuild.main(["--terms", "Fall 2026"]) == 0
    out = capsys.readouterr().out
    assert "Fall 2026" in out and "1 errors" in out                  # PHIL page 404s
    stored = [json.loads(line) for line in _empty_schedule_store.open()]
    assert {s["course"][:2] for s in stored} == {"CS", "MA"}           # PHIL rows filtered by subject
    assert (_empty_schedule_store.parent / "course_offerings.json").exists()


def test_schedule_import_html_and_bad_csv(tmp_path, _empty_schedule_store):
    from app.structured.build import main
    from tests.schedule_fixtures import FALL_2026_HTML
    page = tmp_path / "s.html"
    page.write_text(FALL_2026_HTML)
    assert main(["--import-html", str(page), "--term", "Fall 2026"]) == 0
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    assert main(["--import-csv", str(bad), "--term", "Fall 2026"]) == 1
    with pytest.raises(SystemExit):
        main(["--import-csv", str(bad)])                             # --term required


# -- LLM client ------------------------------------------------------------------------------

class ListingBackend:
    def __init__(self, models, fail=None):
        self.models, self.fail = models, fail or {}

    def list_models(self):
        return self.models

    def complete(self, model, messages, *a, **kw):
        if model in self.fail:
            raise llm.LLMHTTPError(404, self.fail[model])
        return "OK", {}


def test_missing_and_probed_models(monkeypatch):
    monkeypatch.setattr(llm, "_available", None)
    llm.set_backend(ListingBackend([settings.LLM_MODEL], fail={settings.LLM_FALLBACK_MODEL: "gone"}))
    missing = llm.missing_models()
    assert settings.LLM_MODEL not in missing and settings.LLM_FALLBACK_MODEL in missing
    probe = llm.probe_models()
    assert probe[settings.LLM_MODEL] == "ok" and "gone" in probe[settings.LLM_FALLBACK_MODEL]


def test_no_key_means_unavailable():
    llm.set_backend(None)
    assert not llm.is_available() and llm.missing_models() is None


def test_http_backend_lists_models_and_strips_prefixes():
    b = llm.OpenAICompatBackend("https://example.test/v1", "key", timeout_s=5)
    b._client = httpx.Client(base_url="https://example.test/v1/", transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "models/gemini-x"}, {"id": "y"}]})))
    assert b.list_models() == ["gemini-x", "y"]


def test_http_backend_timeout_becomes_a_fallback_error():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)
    b = llm.OpenAICompatBackend("https://example.test/v1", "key", timeout_s=5)
    b._client = httpx.Client(base_url="https://example.test/v1/", transport=httpx.MockTransport(slow))
    with pytest.raises(llm.LLMHTTPError) as e:
        b.complete("m", [{"role": "user", "content": "x"}], 10, 0.0, False, timeout_s=1)
    assert e.value.status_code == 408


def test_invalid_json_is_repaired_once(fake_llm):
    class Sloppy:
        n = 0

        def complete(self, model, messages, *a, **kw):
            Sloppy.n += 1
            return ("not json" if Sloppy.n == 1 else 'Sure: {"ok": 1}'), {}
    llm.set_backend(Sloppy())
    from app.tracing import start_trace
    with start_trace():
        assert llm.chat_json([{"role": "user", "content": "x"}], agent="t") == {"ok": 1}
    assert Sloppy.n == 2
