"""
run_eval.py
===========
Evaluation harness for Bobcat Advisor.

Suites
------
  router     intent accuracy, professor/course extraction, completed-course
             parsing, unknown-professor detection               (offline)
  retrieval  entity precision@k, keyword hit@k / MRR, professor coverage for
             comparisons — for each retrieval configuration      (offline)
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

A note on labels: retrieval relevance here is *proxy-labelled* — a chunk is
"on-entity" if its metadata matches the expected professor/course, and
"on-topic" if it contains one of the case's keywords. That's cheaper and
more reproducible than hand-labelling chunk ids, and it's honest about what
it measures. The e2e suite adds an LLM judge for answer-level quality.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from app.agents.router import route_rules
from app.agents.specialists import run_catalog, run_planner, run_reviews
from app.knowledge.corpus import chunk_courses
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

def eval_router(cases: list[dict], router=route_rules) -> dict:
    rows = []
    for c in cases:
        e = c["expect"]
        p = router(c["question"], c.get("history"))
        exp_p, got_p = set(e.get("professors", [])), set(p.professors)
        row = {
            "id": c["id"],
            "intent_ok": p.intent == e["intent"],
            "professors_exact": exp_p == got_p,
            "prof_tp": len(exp_p & got_p), "prof_fp": len(got_p - exp_p), "prof_fn": len(exp_p - got_p),
            "courses_recall": (len(set(e.get("courses", [])) & set(p.courses)) / len(e["courses"])
                               if e.get("courses") else None),
        }
        if "completed" in e:
            row["completed_exact"] = set(p.completed_courses) == set(e["completed"])
        if e.get("unknown_professor"):
            row["unknown_detected"] = e["unknown_professor"] in p.unknown_professors
        if not row["intent_ok"] or not row["professors_exact"]:
            row["got"] = {"intent": p.intent, "professors": p.professors, "courses": p.courses}
        rows.append(row)

    tp = sum(r["prof_tp"] for r in rows)
    fp = sum(r["prof_fp"] for r in rows)
    fn = sum(r["prof_fn"] for r in rows)
    return {
        "n": len(rows),
        "intent_accuracy": _mean([r["intent_ok"] for r in rows]),
        "professor_exact_match": _mean([r["professors_exact"] for r in rows]),
        "professor_precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
        "professor_recall": round(tp / (tp + fn), 4) if tp + fn else 1.0,
        "course_recall": _mean([r["courses_recall"] for r in rows if r["courses_recall"] is not None]),
        "completed_exact": _mean([r["completed_exact"] for r in rows if "completed_exact" in r]),
        "unknown_professor_detection": _mean([r["unknown_detected"] for r in rows if "unknown_detected" in r]),
        "failures": [r for r in rows if not (r["intent_ok"] and r["professors_exact"])],
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _on_entity(meta: dict, profs: set[str], courses: set[str]) -> bool:
    if profs and meta.get("professor") not in profs:
        return False
    if courses and not (set(chunk_courses(meta)) & courses):
        return False
    return True


def _retrieval_metrics(chunks: list[dict], e: dict) -> dict:
    profs, courses = set(e.get("professors", [])), set(e.get("courses", []))
    # Catalog-content keywords are answered by the catalog agent, not retrieval.
    kws = [] if e.get("needs_catalog") else [k.lower() for k in e.get("keywords_any", [])]
    m: dict = {"n_chunks": len(chunks)}
    opinion = [c for c in chunks if c["metadata"].get("chunk_type") in ("review", "reddit")]
    if profs or courses:
        # Professor precision is the strict one; course match is secondary
        # because reviews for a professor's other courses are often relevant.
        m["prof_precision"] = (_mean([_on_entity(c["metadata"], profs, set()) for c in opinion])
                               if profs and opinion else None)
        m["entity_precision"] = (_mean([_on_entity(c["metadata"], profs, courses) for c in opinion])
                                 if opinion else 0.0)
    if kws:
        hits = [any(k in c["text"].lower() for k in kws) for c in chunks]
        m["keyword_hit"] = float(any(hits))
        m["keyword_mrr"] = next((1 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
        m["keyword_precision"] = _mean(hits) if hits else 0.0
    if e.get("intent") == "compare":
        present = {c["metadata"].get("professor") for c in chunks}
        if profs:
            m["prof_coverage"] = len(profs & present) / len(profs)
        elif e.get("min_professors_covered"):
            real = {p for p in present if p and p.lower() != "unknown"}
            m["prof_coverage"] = min(1.0, len(real) / e["min_professors_covered"])
    return m


def eval_retrieval(cases: list[dict], mode: str = "hybrid", rerank: bool = False,
                   include_short: bool = False, k: int = 8) -> dict:
    """
    Two views per case:
      pipeline    the production path (rule router -> reviews agent), so
                  filters derived from entities are applied
      unfiltered  raw ranking with no metadata filters: measures whether the
                  retriever itself surfaces the right professor/topic
    """
    from app.config import settings
    ix = get_index()
    old = (settings.RERANKER_ENABLED, settings.INCLUDE_SHORT_REVIEWS)
    settings.RERANKER_ENABLED, settings.INCLUDE_SHORT_REVIEWS = rerank, include_short

    # run_reviews uses ix.search/balanced with the default mode; patch mode in.
    orig_search = ix.search

    def search_with_mode(*a, **kw):
        kw.setdefault("mode", mode)
        return orig_search(*a, **kw)

    ix.search = search_with_mode
    rows, latencies = [], []
    try:
        for c in cases:
            e = c["expect"]
            if e["intent"] not in ("professor_info", "compare", "course_info") or e.get("must_refuse"):
                continue
            if not (e.get("professors") or e.get("courses") or e.get("keywords_any")):
                continue
            plan = route_rules(c["question"], c.get("history"))
            t0 = time.perf_counter()
            result = run_reviews(plan, k=k)
            latencies.append((time.perf_counter() - t0) * 1000)
            chunks = [{"text": ev.text, "metadata": {
                "professor": ev.metadata.get("professor"), "course": ev.metadata.get("course"),
                "courses": "|".join(chunk_courses(ix.metas[ix.by_id[ev.chunk_id]])) if ev.chunk_id else "",
                "chunk_type": {"review": "review", "reddit": "reddit"}.get(ev.kind, ev.kind)}}
                for ev in result.evidence]
            unf = ix.search(plan.standalone_question, k=k, filters=None)
            rows.append({"id": c["id"], "category": c["category"],
                         "pipeline": _retrieval_metrics(chunks, e),
                         "unfiltered": _retrieval_metrics(unf, e)})
    finally:
        ix.search = orig_search
        settings.RERANKER_ENABLED, settings.INCLUDE_SHORT_REVIEWS = old

    def agg(view: str, key: str):
        return _mean([r[view][key] for r in rows if r[view].get(key) is not None])

    keys = ["prof_precision", "entity_precision", "keyword_hit", "keyword_mrr",
            "keyword_precision", "prof_coverage"]
    return {
        "config": {"mode": mode, "rerank": rerank, "include_short": include_short, "k": k},
        "n": len(rows),
        "pipeline": {kk: agg("pipeline", kk) for kk in keys},
        "unfiltered": {kk: agg("unfiltered", kk) for kk in keys},
        "latency_ms_p50": _pct(latencies, 0.5),
        "latency_ms_p95": _pct(latencies, 0.95),
        "misses": [
            {"id": r["id"], **r["pipeline"]} for r in rows
            if r["pipeline"].get("keyword_hit") == 0.0 or (r["pipeline"].get("prof_coverage") or 1) < 1
        ],
    }


RETRIEVAL_CONFIGS = [
    {"mode": "dense", "rerank": False, "include_short": False},   # the original system
    {"mode": "dense", "rerank": False, "include_short": True},
    {"mode": "bm25", "rerank": False, "include_short": True},
    {"mode": "hybrid", "rerank": False, "include_short": False},
    {"mode": "hybrid", "rerank": False, "include_short": True},
    {"mode": "hybrid", "rerank": True, "include_short": True},
]


# ---------------------------------------------------------------------------
# Tools (catalog + planner)
# ---------------------------------------------------------------------------

def eval_tools(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        e = c["expect"]
        if e["intent"] == "prereq" and e.get("prereq_codes"):
            plan = route_rules(c["question"], c.get("history"))
            text = "\n".join(ev.text for ev in run_catalog(plan).evidence)
            found = [code for code in e["prereq_codes"] if code in text]
            rows.append({"id": c["id"], "kind": "prereq",
                         "ok": len(found) == len(e["prereq_codes"]),
                         "missing": sorted(set(e["prereq_codes"]) - set(found))})
        elif e["intent"] == "plan" and ("eligible_must_include" in e):
            plan = route_rules(c["question"], c.get("history"))
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

JUDGE_PROMPT = """You grade an AI advisor's answer about university professors/courses.
Score 1-5 for each:
- faithfulness: every claim is supported by the SOURCES (5 = fully grounded, 1 = mostly unsupported)
- relevance: the answer addresses the QUESTION directly and helpfully (5 = fully, 1 = not at all)
- balance: opinions are attributed to reviewers and fairly represent positive and negative views
  (5 = balanced, 1 = one-sided or disrespectful). Use 5 if the answer is a refusal.
Return JSON: {"faithfulness": n, "relevance": n, "balance": n, "reason": "<one sentence>"}"""

_REFUSAL_MARKERS = ("couldn't find", "can't help", "can't share", "won't write", "don't have any reviews",
                    "only help with", "no reviews", "not in the", "i can't", "i cannot", "not available")


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
        if e.get("must_refuse") or e.get("must_refuse_reviews"):
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
                for k in ("faithfulness", "relevance", "balance"):
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
        "judge_balance": agg("judge_balance"),
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
    ap.add_argument("--suite", choices=["router", "retrieval", "tools", "e2e", "offline"], default="offline")
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
    suites = ["router", "retrieval", "tools"] if args.suite == "offline" else [args.suite]

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
        elif suite == "tools":
            res, name = eval_tools(cases), f"tools{tag}"
        else:
            res, name = eval_e2e(cases, judge=not args.no_judge, args_pause=args.pause), f"e2e{tag}"
        _print(name, res)
        _save(name, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
