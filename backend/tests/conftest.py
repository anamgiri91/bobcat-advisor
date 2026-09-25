"""
Shared fixtures. Environment is set before any app import so Settings picks
up a throwaway SQLite database and no real API key; LLM behaviour is
injected per-test through app.llm.set_backend(FakeLLM(...)).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="bobcat-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["GROQ_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""  # never hit a real provider from tests
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ["SECRETS_DIR"] = tempfile.mkdtemp(prefix="bobcat-secrets-")   # never read host secret files
os.environ.pop("SECRETS_BACKEND", None)
# Tests run from backend/, where data/ and documents/ live.
os.chdir(Path(__file__).resolve().parent.parent)

import pytest  # noqa: E402

from app import llm  # noqa: E402


class FakeLLM:
    """
    Deterministic stand-in for the LLM provider. Dispatches on the system prompt so one
    fake can play router, synthesizer and verifier.
      router_fn(messages)   -> dict   (JSON the router should return)
      answer                -> str    (streamed by the synthesizer)
      verifier_fn(messages) -> dict   (JSON the verifier should return)
    """

    def __init__(self, answer: str = "Reviewers say exams are hard [1].",
                 router_fn=None, verifier_fn=None, fail_with: Exception | None = None):
        self.answer = answer
        self.router_fn = router_fn
        self.verifier_fn = verifier_fn or (lambda m: {"verdicts": []})
        self.fail_with = fail_with
        self.calls: list[str] = []

    def _role(self, messages) -> str:
        system = messages[0]["content"]
        if "You route questions" in system:
            return "router"
        if "check whether each sentence" in system:
            return "verifier"
        if "You grade" in system:
            return "judge"
        return "synth"

    def complete(self, model, messages, max_tokens, temperature, json_mode, **kwargs):
        role = self._role(messages)
        self.calls.append(role)
        if self.fail_with:
            raise self.fail_with
        if role == "router":
            if self.router_fn is None:
                raise RuntimeError("no router configured")  # -> rules fallback
            return json.dumps(self.router_fn(messages)), {"prompt_tokens": 100, "completion_tokens": 20}
        if role == "verifier":
            return json.dumps(self.verifier_fn(messages)), {"prompt_tokens": 300, "completion_tokens": 30}
        return self.answer, {"prompt_tokens": 500, "completion_tokens": 50}

    def stream(self, model, messages, max_tokens, temperature, **kwargs):
        self.calls.append("synth")
        if self.fail_with:
            raise self.fail_with
        for word in self.answer.split(" "):
            yield word + " "


@pytest.fixture
def fake_llm():
    """Install a FakeLLM; yields a setter so tests can reconfigure it."""
    installed = {}

    def install(**kwargs) -> FakeLLM:
        fake = FakeLLM(**kwargs)
        llm.set_backend(fake)
        installed["fake"] = fake
        return fake

    yield install
    llm.set_backend(None)


@pytest.fixture(autouse=True)
def _no_llm_by_default():
    """Every test starts offline unless it installs a fake."""
    llm.set_backend(None)
    yield
    llm.set_backend(None)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.protection import answer_cache, rate_limiter

    answer_cache.clear()
    rate_limiter.reset()
    with TestClient(app) as c:
        yield c


class _OfflineFetcher:
    def get(self, url, timeout_s):
        from app.advising.web import FetchError
        raise FetchError("network disabled in tests")


@pytest.fixture(autouse=True)
def _no_web_by_default():
    """No test touches the real TXST site; use the txst_web fixture for catalog pages."""
    from app.advising import web
    web.set_fetcher(_OfflineFetcher())
    yield
    web.set_fetcher(None)


@pytest.fixture
def txst_web():
    """Serve the CourseLeaf fixture pages in tests/txst_fixtures.py."""
    from app.advising import web
    from tests.txst_fixtures import FakeFetcher

    fetcher = FakeFetcher()
    web.set_fetcher(fetcher)
    yield fetcher
    web.set_fetcher(None)


@pytest.fixture(autouse=True)
def _empty_schedule_store(tmp_path, monkeypatch):
    """Tests never see real schedule data in data/; use the schedule_store fixture to add some."""
    path = tmp_path / "schedule_sections.jsonl"
    monkeypatch.setattr("app.structured.schedule.store_path", lambda: path)
    monkeypatch.setattr("app.structured.offerings.store_path", lambda: path)
    yield path
