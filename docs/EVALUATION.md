# Evaluation

`backend/evals/run_eval.py` runs four suites. The three offline suites run in
CI on every push, gated by `evals/baseline.json` through
`tests/test_eval_gate.py`. The LLM suite runs weekly or on demand
(`.github/workflows/e2e-eval.yml`).

```bash
cd backend
python -m evals.run_eval                                  # router + retrieval + tools
python -m evals.run_eval --suite retrieval --compare-configs
python -m evals.run_eval --suite router --golden evals/holdout.jsonl
python -m evals.run_eval --suite e2e --sample 20 --pause 6   # needs an LLM key; quota-friendly
python -m evals.run_eval --suite router --llm-router         # needs an LLM key
```

## Datasets

| File | Cases | Purpose |
|---|---|---|
| `evals/golden.jsonl` | 90 | Development set: 30 factual, 12 comparison, 6 stats, 8 course, 8 prereq, 5 planning, 7 unanswerable, 4 off-topic, 5 adversarial, 5 follow-up |
| `evals/holdout.jsonl` | 25 | Written after the rule router was finished and **never tuned against**. It's the honest number for routing. |

Each case carries its expected intent, entities, completed courses,
evidence keywords, and refusal and forbidden-phrase expectations.

**How relevance is labelled.** Retrieval relevance is *proxy-labelled*, not
hand-labelled per chunk. A chunk is *on-entity* if its metadata matches the
expected professor/course, and *on-topic* if it contains one of the case's
keywords. This is reproducible and cheap to extend, but keyword matching is a
noisy stand-in for relevance. Treat retrieval deltas of a few points as
indicative, not significant (about 40 keyword-bearing cases).

**Mislabels found and fixed while building this.** `p03` expected CS3358 to
unlock CS4328; it doesn't, because CS4328 goes through CS3360. `f25` expected
Burtscher attendance evidence, which the corpus doesn't contain; that case now
tests fuzzy name matching and an honest "not found".

## Results (offline, current defaults)

### Router

| Set | Intent acc. | Professor exact | Course recall | Completed-courses exact | Unknown-prof detection |
|---|---|---|---|---|---|
| golden (tuned on) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| **holdout (not tuned on)** | **0.92** | 1.00 | 1.00 | 1.00 | 1.00 |

The two holdout misses ("Which instructor should I pick for CS2308…", "What
classes require CS2308?") use phrasing the rules don't cover. That's
exactly the gap the LLM router is for, and they were deliberately left unfixed
so this number stays honest.

### Tools (deterministic)

| Metric | Score |
|---|---|
| Prerequisite answers contain every expected course | 9/9 |
| Planner eligibility (must-include and must-exclude) | 5/5 |

### Retrieval configurations

*Pipeline* is the production path: router entities become metadata filters,
then the Reviews agent retrieves. *Unfiltered* is the same questions with no
filters, measuring the ranker on its own. k = 8.

| Config | Pipeline kw-hit | Pipeline kw-MRR | Pipeline entity-prec | Unfiltered entity-prec | Unfiltered kw-MRR | Unfiltered comparison coverage | p50 |
|---|---|---|---|---|---|---|---|
| dense, no short reviews *(original system)* | 0.975 | 0.840 | 0.932 | 0.451 | 0.771 | 0.821 | 3.9ms |
| dense + short reviews | 0.950 | 0.799 | 0.948 | 0.451 | 0.717 | 0.821 | 3.9ms |
| BM25 only | 0.975 | 0.857 | 0.949 | 0.439 | 0.815 | 0.893 | 0.4ms |
| **hybrid (RRF), no short** *(default)* | **1.000** | **0.887** | 0.932 | 0.521 | **0.856** | 0.893 | 4.1ms |
| hybrid + short reviews | 0.975 | 0.838 | 0.948 | 0.517 | 0.799 | **0.929** | 4.2ms |
| hybrid + cross-encoder rerank | 0.950 | 0.865 | 0.948 | **0.544** | 0.873 | 0.750 | 68.6ms |

What we learned:

1. **Entity routing matters more than the ranker.** With router-derived
   filters, every configuration reaches professor precision of about 0.98 and full
   comparison coverage. Without filters, the best ranker only reaches 0.54
   entity precision. The router and filters are doing most of the work.
2. **Hybrid beats either leg alone** on topical relevance. Against the
   original dense system, pipeline keyword MRR rises from 0.840 to 0.887,
   keyword hit rate from 0.975 to 1.000, and unfiltered entity precision from
   0.451 to 0.521 (+16%).
3. **Short reviews trade topicality for coverage.** Adding the 217 reviews
   under 50 words helps comparison coverage slightly but dilutes topical matches
   ("pretty chill"). They're excluded from *retrieval* by default, but the Stats
   agent still counts all of them.
4. **The reranker isn't worth it here.** It gives the best unfiltered entity
   precision, but costs 17× the latency and about 100MB of RAM, and drops comparison
   coverage to 0.75 because it concentrates results on whichever professor has
   the most on-topic text.

### CI gate

`evals/baseline.json` sets floors a little below the current numbers.
Sanity check: reverting to *dense + short reviews* fails the gate
(`keyword_mrr 0.799 < 0.82`).

## End-to-end (LLM) suite

`--suite e2e` runs every golden case through the full pipeline and records:

| Metric | How |
|---|---|
| Refusal accuracy | `must_refuse` cases: canned/no-evidence mode or refusal language |
| Forbidden-phrase pass | adversarial cases must not contain e.g. the injected insult |
| Citation coverage | share of factual sentences carrying a valid `[n]` (deterministic) |
| Invalid citations | `[n]` that don't exist (deterministic) |
| Verifier pass rate | share of claims the verifier found supported |
| Judge faithfulness / relevance / balance | 1–5, judged by `LLM_MODEL` at full reasoning effort, separate from the verifier's fast-path call, to limit self-grading |
| Latency p50/p95, tokens, LLM calls | from the per-request trace |

**Status: not yet run in full.** Free-tier quotas are the constraint. A full
90-case run needs roughly 4 LLM calls and 3–5k tokens per case. Groq's free
tier (8k tokens/min, ~200k/day per model) would take about an hour and use
most of a day's quota. Gemini's free-tier quota on `gemini-3.6-flash` was
exhausted after about a dozen development calls. Use `--sample 20` (stratified
across all 10 categories) and `--pause`; a 429 with a retry hint is waited out
automatically (up to 65s in eval mode).

**Live smoke test, Gemini, 3 questions (comparison, pronoun follow-up, planning):**

| Observation | |
|---|---|
| LLM-written answers | cited, grounded in the retrieved stats and quotes; citation coverage 0.93–1.00 |
| Latency | 23–50s end to end, dominated by provider load (the same 60-token call measured 3s–28s); spans show 3.6-flash 503s ("high demand") and timeouts |
| Degradation paths exercised | router timeout → rules; verifier 503 → skipped; synthesis 429 → fallback model; fallback timeout → extractive answer. No request failed |
| Bugs found and fixed | default Gemini thinking truncated answers (`finish_reason=length`); `【n】` citations from gpt-oss (normalised); a mid-stream timeout crashed the request (now degrades) |

To finish: run the sample, paste the summary table from
`evals/results/e2e.json` here, and run the LLM router on the holdout set
(`--suite router --llm-router --golden evals/holdout.jsonl`) to compare with
the 0.92 rules baseline above.

## Aspect tags

Stats use LLM-extracted aspect tags when `data/aspects.jsonl` exists
(`python -m app.knowledge.aspects`), otherwise a conservative keyword
lexicon. Every stats payload says which method produced it
(`aspect_method`). `python -m app.knowledge.aspects --compare` reports
per-aspect agreement between the two, which is worth recording here after the
first extraction run.
