"""
CI quality gate: the offline eval suites must not drop below
evals/baseline.json. Change retrieval, routing or the knowledge layer in a
way that regresses a metric and this fails with the metric name and delta.
"""

import json
from pathlib import Path

import pytest

from evals.run_eval import eval_kb_retrieval, eval_retrieval, eval_router, eval_tools, load_cases

EVALS = Path(__file__).resolve().parent.parent / "evals"
BASELINE = json.loads((EVALS / "baseline.json").read_text())


@pytest.fixture(scope="module")
def golden():
    return load_cases(EVALS / "golden.jsonl")


def _check(results: dict, floors: dict) -> None:
    failures = [
        f"{k}: {results.get(k)} < {v}" for k, v in floors.items()
        if results.get(k) is None or results[k] < v
    ]
    assert not failures, "eval regression — " + "; ".join(failures)


def test_router_golden(golden):
    _check(eval_router(golden), BASELINE["router_golden"])


def test_router_holdout():
    _check(eval_router(load_cases(EVALS / "holdout.jsonl")), BASELINE["router_holdout"])


def test_tools(golden):
    _check(eval_tools(golden), BASELINE["tools"])


def test_retrieval_bm25(golden):
    """Keyword leg alone: needs no embedding model, so it always runs."""
    _check(eval_retrieval(golden, mode="bm25"), BASELINE["retrieval_bm25"])


def test_retrieval_hybrid(golden):
    _check(eval_retrieval(golden), BASELINE["retrieval_hybrid"])


def test_kb_retrieval(golden):
    """Knowledge-base sources: right source kind retrieved. Runs once crawled."""
    res = eval_kb_retrieval(golden)
    if res["n"] == 0:
        pytest.skip("knowledge base not crawled yet (python -m app.kb.build)")
    _check(res, BASELINE["kb"])
