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

The app answers from the official course catalog only. It keeps no data
about individual instructors, so the evals check that it **declines**
instructor questions rather than how well it answers them.

## Datasets

| File | Cases | Purpose |
|---|---|---|
| `evals/golden.jsonl` | 63 | Development set: 8 course, 17 topic search, 5 course comparison, 8 prerequisite, 5 planning, 10 instructor (must decline), 4 off-topic, 3 adversarial, 3 follow-up |
| `evals/holdout.jsonl` | 18 | Written after the rule router was finished and **never tuned against**. It's the honest number for routing. |

Instructor questions use made-up names ("Professor Smith", "Dr. Patel"),
so the eval data names no real person.

**How retrieval is labelled.** Each topic case lists the catalog courses
that answer it (`retrieve_any`); a hit is any of them in the top 4 results
of the production search agent. With ~50 catalog entries these labels are
unambiguous, but 17 cases is a small set: treat differences of a case or two
as noise.

## Results (offline)

### Router (rules, no LLM)

| Set | Intent acc. | Course recall | Completed-courses exact | Instructor questions declined | Other questions wrongly declined |
|---|---|---|---|---|---|
| golden (tuned on) | 0.98 | 1.00 | 1.00 | 10/11 | 0/52 |
| **holdout (not tuned on)** | **0.89** | 0.96 | 1.00 | 4/5 | 0/13 |

The misses are instructor questions that name someone without "professor",
"Dr." or similar ("Should I take CS2308 with Smith or with Garcia?",
"Does Martinez give partial credit…?"), and "Database Systems or Data
Mining" (no alias for "data mining"). They were left unfixed so the numbers
stay honest. The LLM router handles unmarked names; when it isn't available,
the synthesizer prompt still forbids naming or rating instructors, and the
index contains nothing about people to leak.

### Tools (deterministic)

| Metric | Score |
|---|---|
| Prerequisite answers contain every expected course | 10/10 |
| Planner eligibility (must-include and must-exclude) | 5/5 |

### Retrieval (topic questions, k = 4)

| Config | hit@4 | MRR |
|---|---|---|
| BM25 only | 1.00 | 0.90 |
| hybrid (RRF), dense only, hybrid + rerank | not yet measured |

The BM25 row was measured when the catalog-only index was built, in an
environment that couldn't download the embedding model. The hybrid floors
in `baseline.json` are therefore provisional (set from the BM25 numbers);
run `python -m evals.run_eval --suite retrieval --compare-configs` and raise
them. `test_retrieval_bm25` gates the keyword leg on its own, so it runs
anywhere.

One fix came out of this: BM25 tokenised "CS3358" as one token while the
catalog writes "CS 3358", so "What is CS3358 about?" retrieved nothing on the
keyword leg. Splitting letters from digits took hit@4 from 0.95 to 1.00.

### CI gate

`evals/baseline.json` sets floors a little below the current numbers
(instructor specificity is held at 1.0: no ordinary course question may be
declined).

## End-to-end (LLM) suite

`--suite e2e` runs every golden case through the full pipeline and records:

| Metric | How |
|---|---|
| Refusal accuracy | `must_refuse` and `must_decline_instructor` cases: canned mode or refusal language |
| Forbidden-phrase pass | adversarial cases must not leak e.g. the system prompt |
| Citation coverage | share of factual sentences carrying a valid `[n]` (deterministic) |
| Invalid citations | `[n]` that don't exist (deterministic) |
| Verifier pass rate | share of claims the verifier found supported |
| Judge faithfulness / relevance / people | 1–5, judged by `LLM_MODEL` at full reasoning effort; `people` checks that no instructor is named or rated |
| Latency p50/p95, tokens, LLM calls | from the per-request trace |

**Status: not yet run on the catalog-only system.** Use `--sample 20`
(stratified across categories) and `--pause` on free-tier keys; a 429 with a
retry hint is waited out automatically (up to 65s in eval mode). Paste the
summary from `evals/results/e2e.json` here, and run the LLM router on the
holdout set (`--suite router --llm-router --golden evals/holdout.jsonl`) to
compare with the 0.89 rules baseline above.
