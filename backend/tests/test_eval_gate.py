"""
CI quality gate: the offline eval suites must not drop below
evals/baseline.json. Change retrieval, routing or the knowledge layer in a
way that regresses a metric and this fails with the metric name and delta.
"""

import json
from pathlib import Path

import pytest

from evals.run_eval import eval_retrieval, eval_router, eval_tools, load_cases

EVALS = Path(__file__).resolve().parent.parent / "evals"
BASELINE = json.loads((EVALS / "baseline.json").read_text())


@pytest.fixture(scope="module")
def golden():
    return load_cases(EVALS / "golden.jsonl")


@pytest.fixture(scope="module")
def retrieval(golden):
    return eval_retrieval(golden)


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


def test_retrieval_pipeline(retrieval):
    _check(retrieval["pipeline"], BASELINE["retrieval_pipeline"])


def test_retrieval_unfiltered(retrieval):
    _check(retrieval["unfiltered"], BASELINE["retrieval_unfiltered"])
