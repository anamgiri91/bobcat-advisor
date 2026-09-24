# Bobcat Advisor — Architecture

A multi-agent advisor for Texas State CS students. It answers questions about
professors and courses from real reviews, computes statistics and
prerequisite eligibility exactly, and verifies its own answers against the
sources it cites.

## Request flow

```
 question + last 6 turns
          │
          ▼
 ┌──────────────────┐  deterministic: private-info / harassment / system-prompt
 │    Guardrails    │  requests are declined here, before any retrieval or LLM
 └────────┬─────────┘
          ▼
 ┌──────────────────┐  fast model, JSON mode → QueryPlan
 │      Router      │  {intent, professors, courses, completed, standalone_question}
 │  (LLM → rules)   │  every entity validated against the data-derived registry;
 └────────┬─────────┘  falls back to rule router on any failure
          │ intent → fixed set of specialists (state.AGENTS_FOR_INTENT)
          ▼
 ┌──────────┬──────────┬───────────┬───────────┐   run in parallel threads,
 │ Reviews  │  Stats   │  Catalog  │  Planner  │   no LLM calls — tools only
 │ hybrid   │ exact    │ prereq    │ eligibility│
 │ RAG      │ counts   │ graph     │ + stats   │
 └────┬─────┴────┬─────┴─────┬─────┴─────┬─────┘
      └──────────┴─── evidence [1..n] ───┘   deduped, ordered: stats → plan →
          │                                   prereq → catalog → reviews
          ▼
 ┌──────────────────┐  streamed; every factual sentence cites [n]
 │   Synthesizer    │  no LLM available → extractive answer (tool output + quotes)
 └────────┬─────────┘
          ▼
 ┌──────────────────┐  1. citation check (deterministic): invalid [n], coverage
 │     Verifier     │  2. claim check (fast model): each sentence vs cited evidence
 └────────┬─────────┘  unsupported sentences removed (if ≥50% of the answer survives)
          ▼
   PII redaction → answer + sources + trace → Postgres
```

Each step is a plain function with typed inputs and outputs
(`app/agents/state.py`). The orchestrator (`app/agents/orchestrator.py`)
yields events, and the API streams those same events to the browser over SSE:
`plan → agent(start/done) → sources → token… → verification → revision? → done`.

## Components

| Layer | Module | What it does |
|---|---|---|
| LLM client | `app/llm.py` | One pooled HTTP client for any OpenAI-compatible API (Gemini by default, Groq supported), with no vendor SDK. Model fallback on 404/408/429/5xx/JSON-validation errors, one wait-and-retry when every model is rate limited, per-role reasoning effort and deadlines, JSON mode with repair retry, streaming, a per-request budget that counts hidden reasoning tokens, and a tracing span per call. Swappable backend for tests. |
| Tracing | `app/tracing.py` | ContextVar trace with OTel-shaped spans. Stored per answer in `messages.trace`. |
| Guardrails | `app/guardrails.py` | Request classification, injection detection, neutralising instruction-like text inside scraped reviews, PII redaction. |
| Router | `app/agents/router.py` | LLM router plus a rule router with the same `QueryPlan` contract. |
| Specialists | `app/agents/specialists.py` | reviews / stats / catalog / planner. |
| Synthesizer | `app/agents/synthesizer.py` | Grounded, cited, balanced answer; extractive fallback. |
| Verifier | `app/agents/verifier.py` | Citation and claim-level support checks; revision. |
| Retrieval | `app/rag/index.py` | Dense + BM25 → RRF, optional cross-encoder rerank, metadata filters with course-filter relaxation, near-duplicate collapse. |
| Knowledge | `app/knowledge/` | `catalog.py` prerequisite graph (CNF), `corpus.py` entity registry, `stats.py` review statistics, `aspects.py` LLM aspect extraction. |
| Ingestion | `app/rag/{chunker,cleaner,ingest,embed}.py` | Chunk → clean (course normalisation) → incremental embed (content-hash IDs). |
| API | `app/routers/` | chat (JSON + SSE), history, feedback, analytics (+ `/agents`), knowledge (professors, courses, compare, plan). |
| MCP | `mcp_server.py` | The same tools exposed to any MCP client. |

## Design decisions

**The router picks an intent, not a free-form plan.** The LLM router can't
invent agents or tool calls. It picks one of six intents, and each intent maps
to a fixed set of specialists. That keeps the plan space small enough to
test exhaustively (`evals/golden.jsonl` covers every intent), and a bad
router output degrades to a slightly wrong set of tools rather than an
unbounded loop.

**Specialists don't call LLMs.** Everything the synthesizer can cite came
from a deterministic tool: a retrieved review, a computed statistic, a
prerequisite-graph result. That's why the numbers in answers can be trusted
("mentioned in 11 of 139 reviews") and why the specialists are unit-testable
and cost nothing to run. The LLM budget goes to the three places it adds
value: understanding the question, writing, and checking.

**Counting is computed, not generated.** `stats.py` merges cross-posted
reviews (the same text on RMP and Coursicle), then counts ratings, grades and
aspect mentions with explicit denominators. The synthesizer prompt forbids
"most students" unless the counts show a majority.

**Prerequisites are a graph, not a retrieval problem.** The catalog is parsed
into CNF (an AND of OR-groups), with conditional groups for ACT/approval
alternatives. Eligibility is set logic: `expand_completed` infers implied
courses (passing CS2308 implies CS1428), but never guesses which branch of
an OR-group was taken.

**Exact search in memory, Chroma as the store of record.** About 800 chunks is
a 1.2MB matrix. Brute-force cosine is exact, takes about 4ms, and supports
filters Chroma's `where` can't express (e.g. "any of this multi-course
review's courses"). Past roughly 50k chunks, the dense leg moves back to
`collection.query`; the fusion code doesn't change.

**Hybrid + RRF.** MiniLM misses exact tokens (surnames, course numbers,
"zyBooks", "curve"); BM25 misses paraphrase. RRF fuses ranks, so the two
legs never need score calibration. See `docs/EVALUATION.md` for the measured
gains.

**The reranker is off by default.** It improves raw ranking but adds about 70ms
p50 and about 100MB RSS. It also *hurts* professor coverage on comparison
questions, because it concentrates results on whoever has the most on-topic
text. On a 512MB instance that isn't worth it. Enable it with
`RERANKER_ENABLED=true` on larger instances.

**The verifier deletes rather than regenerates.** A second synthesis is the
most expensive call, and it can introduce new unsupported claims. Deleting a
flagged sentence can only make the answer more conservative. A guard keeps
the draft when the verifier rejects more than half of it, since a small judge
model over-flagging shouldn't erase a good answer.

**Graceful degradation at every LLM step.** The provider failures here
(404, 429, 503, timeouts including mid-stream, invalid JSON) were all hit
against the live Gemini and Groq APIs during development. Every row is also
covered by tests with a fake backend.

| Failure | Behaviour |
|---|---|
| No API key / provider down | rule router + extractive answer, still cited |
| Synthesis model 404 / 429 / 503 / timeout | fall back to `LLM_FALLBACK_MODEL`; if every model is rate limited, wait the provider's retry delay once (≤ 8s) |
| Failure *mid-stream* | discard the partial text, send the extractive answer as a `revision` event |
| Router slow or failing | 10s deadline, no second model, then the rule router |
| Router returns invalid JSON/intent | repair retry, then the rule router |
| Verifier slow or failing | 10s deadline, then ship unverified (`verification.claims.method = "skipped"`) |
| Budget exhausted | skip the verifier; extractive answer if synthesis can't run |

**Provider-agnostic, SDK-free LLM client.** Gemini and Groq both serve
OpenAI-compatible chat APIs, so `llm.py` talks plain HTTP (httpx) to either.
Switching provider is configuration (`LLM_PROVIDER`, `LLM_*_MODEL`). The
project switched from Groq to Gemini in one commit without touching any
agent code.

**Model availability is verified, not assumed.** Both providers have served
this project model IDs that were listed publicly but failed for the key:
Groq's `llama-3.3-70b-versatile` returned 404 for a key without Llama access,
and Gemini's `gemini-2.5-flash` is listed by `/models` but returns 404 "no
longer available to new users". `/api/health` reports configured models
missing from the key's list, and `/api/health?deep=true` makes one real call
per model.

**Reasoning effort is a latency and truncation control.** Hidden thinking
tokens count against `max_tokens`. At Gemini's default effort, synthesis
spent ~1.5k of a 1.6k budget thinking and returned 61 visible tokens
(`finish_reason=length`). Synthesis runs at `low` and the router/verifier at
`minimal`. Traces record `reasoning_tokens` and `finish_reason`, and the
budget counts the reasoning tokens.

**Deterministic fallbacks earn tight deadlines.** The router and verifier
each have a non-LLM fallback (rules, or ship unverified), so they get a 10s
deadline and no second model. Under load, two 30s timeouts on the optional
verifier had doubled answer latency.

**Hand-written orchestrator instead of LangGraph/CrewAI.** The graph is
small and fixed. A framework would add about 100MB of dependencies to a 512MB
container and hide the control flow. Every node is a plain function, so a
port to LangGraph would be mechanical.

**In-process rate limiting and caching.** The app runs as a single instance,
so Redis would add a service (and a failure mode) for no benefit.
`services/protection.py` keeps a narrow interface, so moving to Redis only
touches that file. Only first-turn questions are cached, because follow-up
answers depend on the conversation.

## Safety model

- **Private info / harassment / prompt extraction:** declined before retrieval with fixed responses.
- **Prompt injection via the question:** flagged; the synthesizer is told to answer only the legitimate part.
- **Prompt injection via reviews:** scraped text is untrusted. Evidence is
  wrapped in `<evidence>` tags, instruction-like spans are rewritten to
  `[quoted text: …]`, and the system prompt says evidence is data.
- **Fairness:** opinions are attributed ("reviewers say"), both sides are presented,
  and the eval judge scores balance.
- **PII:** emails and phone numbers are redacted from answers and from the stored question.

## Observability

Every answer stores a trace (spans for the router, each specialist,
retrieval, each LLM call with model and tokens, and the verifier) plus its
intent, answer mode and verifier pass rate. `GET /api/analytics/agents`
aggregates p50/p95 latency per span, token usage, verifier pass rate, answer
modes, intents, and 👍/👎 by intent. That shows which kinds of question
users are unhappy with.

## Memory budget (Render free tier, 512MB)

Measured on macOS arm64; Linux numbers will differ somewhat.

| Configuration | Peak RSS |
|---|---|
| API after warmup (index + MiniLM + registry + stats) | ~340–390MB |
| + cross-encoder reranker | ~440–490MB |

This is why the reranker is off by default, and why PyTorch was replaced with ONNX
(`fastembed`) earlier.
