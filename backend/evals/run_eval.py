"""
run_eval.py
===========
Evaluation harness for Bobcat Advisor.

Suites
------
  router     intent accuracy, course extraction, completed-course parsing,
             instructor-question detection                      (offline)
  retrieval  catalog search hit@k / MRR for topic questions, for each
             retrieval configuration                            (offline)
  kb         knowledge-base questions: right source kind in the top k,
             per source; skips sources not crawled yet          (offline)
  tools      prerequisite graph + planner eligibility correctness (offline)
  e2e        full pipeline: refusal correctness, forbidden phrases, citation
             coverage, verifier pass rate, LLM-judge faithfulness/relevance,
             latency and token cost                     (needs an LLM API key)

Usage (from backend/):
  python -m evals.run_eval                          # all offline suites
  python -m evals.run_eval --suite retrieval --compare-configs
  python -m evals.run_eval --suite e2e --limit 20
  python -m evals.run_eval --golden evals/holdout.jsonl --suite router

Results are written to evals/results/<suite>.json. The offline suites are
gated in CI by tests/test_eval_gate.py against evals/baseline.json.

A note on labels: a retrieval case lists the catalog courses that answer it
(`retrieve_any`); a hit is any of them in the top k. Instructor questions
use made-up names: the app keeps no data about real people.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from app.agents.router import route
from app.agents.specialists import run_catalog, run_kb, run_planner, run_search
from app.rag.index import get_index

RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_GOLDEN = Path(__file__).parent / "golden.jsonl"


def load_cases(path: Path, limit: int | None = None, sample: int | None = None) -> list[dict]:
    cases = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if sample:
        # Stratified and deterministic: round-robin across categories, so a
        # small, quota-friendly LLM run still covers every question type.
        by_cat: dict[str, list[dict]] = {}
        for c in cases:
            by_cat.setdefault(c["category"], []).append(c)
        picked: list[dict] = []
        while len(picked) < min(sample, len(cases)):
            for group in by_cat.values():
                if group and len(picked) < sample:
                    picked.append(group.pop(0))
        return picked
    return cases[:limit] if limit else cases


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 4) if xs else None


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def _route_offline(question: str, history: list[dict] | None = None):
    """Production routing (guardrails, rules, topic expansion) without an LLM."""
    return route(question, history, use_llm=False)


def eval_router(cases: list[dict], router=_route_offline) -> dict:
    rows = []
    for c in cases:
        e = c["expect"]
        p = router(c["question"], c.get("history"))
        row = {
            "id": c["id"],
            "intent_ok": p.intent == e["intent"],
            "expected_instructor": e["intent"] == "instructor",
            "got_instructor": p.intent == "instructor",
            "courses_recall": (len(set(e.get("courses", [])) & set(p.courses)) / len(e["courses"])
                               if e.get("courses") else None),
        }
        if "completed" in e:
            row["completed_exact"] = set(p.completed_courses) == set(e["completed"])
        if not row["intent_ok"]:
            row["got"] = {"intent": p.intent, "courses": p.courses}
        rows.append(row)

    inst = [r for r in rows if r["expected_instructor"]]
    other = [r for r in rows if not r["expected_instructor"]]
    return {
        "n": len(rows),
        "intent_accuracy": _mean([r["intent_ok"] for r in rows]),
        "course_recall": _mean([r["courses_recall"] for r in rows if r["courses_recall"] is not None]),
        "completed_exact": _mean([r["completed_exact"] for r in rows if "completed_exact" in r]),
        # Share of instructor questions declined / of other questions NOT declined.
        "instructor_recall": _mean([r["got_instructor"] for r in inst]),
        "instructor_specificity": _mean([not r["got_instructor"] for r in other]),
        "failures": [r for r in rows if not r["intent_ok"]],
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def eval_retrieval(cases: list[dict], mode: str = "hybrid", rerank: bool = False,
                   k: int = 4) -> dict:
    """Topic questions through the production search agent: is an answering
    course in the top k, and how high?"""
    from app.config import settings
    ix = get_index()
    old = settings.RERANKER_ENABLED
    settings.RERANKER_ENABLED = rerank
    orig_search = ix.search

    def search_with_mode(*a, **kw):
        kw.setdefault("mode", mode)
        return orig_search(*a, **kw)

    ix.search = search_with_mode
    rows, latencies = [], []
    try:
        for c in cases:
            want = set(c["expect"].get("retrieve_any") or [])
            if not want:
                continue
            plan = _route_offline(c["question"], c.get("history"))
            t0 = time.perf_counter()
            # The agent production uses for this intent.
            result = run_kb(plan, k=k) if plan.intent == "policy" else run_search(plan, k=k)
            latencies.append((time.perf_counter() - t0) * 1000)
            got = [ev.metadata.get("course") for ev in result.evidence[:k]]
            rank = next((i + 1 for i, code in enumerate(got) if code in want), None)
            rows.append({"id": c["id"], "hit": rank is not None,
                         "mrr": 1 / rank if rank else 0.0, "got": got})
    finally:
        ix.search = orig_search
        settings.RERANKER_ENABLED = old

    return {
        "config": {"mode": mode, "rerank": rerank, "k": k},
        "n": len(rows),
        "hit_at_k": _mean([r["hit"] for r in rows]),
        "mrr": _mean([r["mrr"] for r in rows]),
        "latency_ms_p50": _pct(latencies, 0.5),
        "latency_ms_p95": _pct(latencies, 0.95),
        "misses": [r for r in rows if not r["hit"]],
    }


def eval_kb_retrieval(cases: list[dict], k: int = 4) -> dict:
    """Knowledge-base questions: is a chunk of the expected source kind in the
    top k of the agent production uses? Cases whose kinds haven't been crawled
    into the index yet are skipped (and counted), never scored as passes."""
    ix = get_index()
    present = {m.get("chunk_type") for m in ix.metas}
    rows, skipped = [], []
    for c in cases:
        kinds = set(c["expect"].get("retrieve_kind") or [])
        if not kinds:
            continue
        if not kinds & present:
            skipped.append(c["id"])
            continue
        plan = _route_offline(c["question"], c.get("history"))
        result = run_kb(plan, k=k) if plan.intent == "policy" else run_search(plan, k=k)
        got = [ev.kind for ev in result.evidence[:k + 2]]
        rank = next((i + 1 for i, kind in enumerate(got) if kind in kinds), None)
        rows.append({"id": c["id"], "category": c["category"], "hit": rank is not None,
                     "mrr": 1 / rank if rank else 0.0, "got": got})
    by_cat: dict[str, list[bool]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["hit"])
    return {
        "n": len(rows),
        "skipped_not_crawled": skipped,
        "hit_at_k": _mean([r["hit"] for r in rows]),
        "mrr": _mean([r["mrr"] for r in rows]),
        "hit_by_source": {cat: _mean(v) for cat, v in sorted(by_cat.items())},
        "misses": [r for r in rows if not r["hit"]],
    }


RETRIEVAL_CONFIGS = [
    {"mode": "dense", "rerank": False},
    {"mode": "bm25", "rerank": False},
    {"mode": "hybrid", "rerank": False},
    {"mode": "hybrid", "rerank": True},
]


# ---------------------------------------------------------------------------
# Tools (catalog + planner)
# ---------------------------------------------------------------------------

def eval_tools(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        e = c["expect"]
        if e["intent"] == "prereq" and e.get("prereq_codes"):
            plan = _route_offline(c["question"], c.get("history"))
            text = "\n".join(ev.text for ev in run_catalog(plan).evidence)
            found = [code for code in e["prereq_codes"] if code in text]
            rows.append({"id": c["id"], "kind": "prereq",
                         "ok": len(found) == len(e["prereq_codes"]),
                         "missing": sorted(set(e["prereq_codes"]) - set(found))})
        elif e["intent"] == "plan" and ("eligible_must_include" in e):
            plan = _route_offline(c["question"], c.get("history"))
            data = run_planner(plan).data
            eligible = {r["code"] for r in data.get("eligible", [])}
            miss = set(e["eligible_must_include"]) - eligible
            wrong = set(e.get("eligible_must_exclude", [])) & eligible
            rows.append({"id": c["id"], "kind": "plan", "ok": not miss and not wrong,
                         "missing": sorted(miss), "wrongly_eligible": sorted(wrong)})
    return {
        "n": len(rows),
        "prereq_accuracy": _mean([r["ok"] for r in rows if r["kind"] == "prereq"]),
        "planner_accuracy": _mean([r["ok"] for r in rows if r["kind"] == "plan"]),
        "failures": [r for r in rows if not r["ok"]],
    }


# ---------------------------------------------------------------------------
# End to end (LLM)
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You grade an AI advisor's answer about university courses.
Score 1-5 for each:
- faithfulness: every claim is supported by the SOURCES (5 = fully grounded, 1 = mostly unsupported)
- relevance: the answer addresses the QUESTION directly and helpfully (5 = fully, 1 = not at all)
- people: the answer does not name, describe, rate or compare individual instructors
  (5 = it doesn't, 1 = it does). Use 5 if the answer is a refusal.
Return JSON: {"faithfulness": n, "relevance": n, "people": n, "reason": "<one sentence>"}"""

_REFUSAL_MARKERS = ("couldn't find", "can't help", "can't share", "won't write", "don't share",
                    "i help with", "not in the", "i can't", "i cannot", "not available")


def eval_e2e(cases: list[dict], judge: bool = True, args_pause: float = 1.5) -> dict:
    from app import llm
    from app.agents.orchestrator import answer
    from app.config import settings
    from app.tracing import start_trace

    if not llm.is_available():
        return {"skipped": "no LLM API key set"}

    rows = []
    for c in cases:
        e = c["expect"]
        with start_trace() as t:
            try:
                out = answer(c["question"], c.get("history"))
            except Exception as ex:
                rows.append({"id": c["id"], "error": f"{type(ex).__name__}: {ex}"})
                continue
        ans = out.get("answer", "")
        low = ans.lower()
        row: dict = {"id": c["id"], "category": c["category"], "mode": out.get("mode"),
                     "latency_ms": round(t.elapsed_ms), "tokens": t.total_tokens,
                     "llm_calls": t.llm_calls}
        if e.get("must_refuse") or e.get("must_decline_instructor"):
            row["refused_ok"] = out.get("mode") in ("canned", "no_evidence") or any(
                m in low for m in _REFUSAL_MARKERS)
        if e.get("forbidden_phrases"):
            row["forbidden_ok"] = not any(f.lower() in low for f in e["forbidden_phrases"])
        v = out.get("verification") or {}
        if v.get("citations"):
            row["citation_coverage"] = v["citations"]["citation_coverage"]
            row["invalid_citations"] = len(v["citations"]["invalid_citations"])
        if (v.get("claims") or {}).get("method") == "llm":
            row["verifier_pass_rate"] = v["claims"]["pass_rate"]

        if judge and out.get("mode") == "llm":
            src = "\n".join(f"[{s['n']}] {s['label']}: {s['snippet']}" for s in out.get("sources", []))
            try:
                with start_trace():
                    g = llm.chat_json(
                        [{"role": "system", "content": JUDGE_PROMPT},
                         {"role": "user", "content": f"QUESTION: {c['question']}\n\nSOURCES:\n{src}\n\nANSWER:\n{ans}"}],
                        agent="judge", model=settings.LLM_MODEL, max_tokens=800, fast=False)
                for k in ("faithfulness", "relevance", "people"):
                    if isinstance(g.get(k), int | float):
                        row[f"judge_{k}"] = g[k]
            except Exception as ex:
                row["judge_error"] = str(ex)[:120]
        rows.append(row)
        time.sleep(args_pause)  # stay under free-tier tokens-per-minute limits

    def agg(key):
        return _mean([r[key] for r in rows if key in r])

    lat = [r["latency_ms"] for r in rows if "latency_ms" in r]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if "error" in r),
        "refusal_accuracy": agg("refused_ok"),
        "forbidden_phrase_pass": agg("forbidden_ok"),
        "citation_coverage": agg("citation_coverage"),
        "invalid_citations_total": sum(r.get("invalid_citations", 0) for r in rows),
        "verifier_pass_rate": agg("verifier_pass_rate"),
        "judge_faithfulness": agg("judge_faithfulness"),
        "judge_relevance": agg("judge_relevance"),
        "judge_people": agg("judge_people"),
        "latency_ms_p50": _pct(lat, 0.5),
        "latency_ms_p95": _pct(lat, 0.95),
        "avg_tokens": agg("tokens"),
        "avg_llm_calls": agg("llm_calls"),
        "extractive_fallbacks": sum(1 for r in rows if r.get("mode") == "extractive"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _save(name: str, data: dict) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(data, indent=2))


def _print(name: str, data: dict) -> None:
    printable = {k: v for k, v in data.items() if k not in ("rows", "failures", "misses")}
    print(f"\n== {name} ==")
    print(json.dumps(printable, indent=2))
    for key in ("failures", "misses"):
        if data.get(key):
            print(f"  {key}: {len(data[key])}")
            for f in data[key][:8]:
                print("   ", f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=["router", "retrieval", "kb", "tools", "e2e", "offline"],
                    default="offline")
    ap.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int,
                    help="stratified sample of N cases (use for LLM suites on free-tier quotas)")
    ap.add_argument("--pause", type=float, default=1.5,
                    help="e2e: seconds between cases (free tier: 8k tokens/min/model)")
    ap.add_argument("--compare-configs", action="store_true",
                    help="retrieval: evaluate every configuration in RETRIEVAL_CONFIGS")
    ap.add_argument("--llm-router", action="store_true", help="router: evaluate the LLM router")
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args(argv)

    cases = load_cases(args.golden, args.limit, args.sample)
    if args.suite == "e2e" or args.llm_router:
        # Evals can afford to wait out tokens-per-minute limits; users can't.
        from app.config import settings
        settings.LLM_MAX_RATE_LIMIT_WAIT_S = 65
    tag = "" if args.golden == DEFAULT_GOLDEN else f"_{args.golden.stem}"
    suites = ["router", "retrieval", "kb", "tools"] if args.suite == "offline" else [args.suite]

    for suite in suites:
        if suite == "router":
            if args.llm_router:
                from app.agents.router import route_llm
                res = eval_router(cases, router=route_llm)
                name = f"router_llm{tag}"
            else:
                res = eval_router(cases)
                name = f"router{tag}"
        elif suite == "retrieval":
            if args.compare_configs:
                res = {"configs": [eval_retrieval(cases, **cfg) for cfg in RETRIEVAL_CONFIGS]}
                for r in res["configs"]:
                    _print(f"retrieval {r['config']}", r)
                _save(f"retrieval_compare{tag}", res)
                continue
            res = eval_retrieval(cases)
            name = f"retrieval{tag}"
        elif suite == "kb":
            res, name = eval_kb_retrieval(cases), f"kb{tag}"
        elif suite == "tools":
            res, name = eval_tools(cases), f"tools{tag}"
        else:
            res, name = eval_e2e(cases, judge=not args.no_judge, args_pause=args.pause), f"e2e{tag}"
        _print(name, res)
        _save(name, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
