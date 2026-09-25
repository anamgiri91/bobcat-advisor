# Bobcat Advisor

A multi-agent AI advisor for Texas State University CS students. It answers
course questions from the official catalog, **computes** prerequisite
eligibility instead of guessing it, builds a next-term plan from the live
TXST catalog, cites every claim, and verifies its own answers before you see
them.

> "Which course covers compilers?" · "Should I take CS3358 or CS3360 first?"
> · "I've taken CS1428 and CS2308, what can I take next?" · "What are the
> prereqs for CS3360?"

It stores no data about individual instructors and declines questions about
them. Not affiliated with Texas State University.

## Highlights

- **Multi-agent pipeline.** Router → parallel specialists (catalog, search,
  planner) → cited synthesis → claim verifier. Streamed live to the
  UI so you can watch each agent work.
- **Agentic course recommendations.** A second pipeline of seven agents
  browses the live TXST catalog for your degree, fact-checks what it read,
  audits your progress and plans next term from your year, completed
  courses, major, interests and target load (see below).
- **Hybrid retrieval.** Dense (MiniLM, ONNX) + BM25 over the catalog, fused
  with RRF, course filters with relaxation, and optional cross-encoder
  reranking.
- **Prerequisite graph.** The catalog is parsed into CNF prerequisites.
  Eligibility and "what does this unlock" are set logic, never generated.
- **Self-verification.** Every sentence is checked against the evidence it
  cites; unsupported sentences are removed, and the pass rate is logged per answer.
- **Evals in CI.** 63-case golden set plus an 18-case held-out set;
  router, retrieval and tool metrics are gated against a baseline on every
  push, and a weekly LLM-judged end-to-end run.
- **Degrades gracefully.** No API key, an overloaded, rate-limited or
  quota-exhausted model, a timeout mid-stream, or an exhausted budget: every
  path still produces a cited answer. The provider failures were all hit live
  during development, and every path is covered by tests.
- **Production concerns.** Per-request traces in Postgres, per-agent latency
  and token analytics, rate limiting, answer caching, prompt-injection and
  PII guardrails, and a 512MB memory budget.
- **MCP server.** The same tools, usable from Claude Desktop, Claude Code or any
  MCP client.

## Architecture

```
question ─► Guardrails ─► Router (LLM → rules fallback) ─► QueryPlan
                                    │
             ┌──────────────┬───────┴──────┐
             ▼              ▼              ▼
          Catalog         Search        Planner                     (parallel,
        prereq graph   hybrid RAG    eligibility                     no LLM)
             └──────────────┴── evidence [1..n]
                                    ▼
                      Synthesizer (streamed, cites [n])
                                    ▼
                      Verifier (citations + claim check)
                                    ▼
                 answer + sources + trace ─► Postgres ─► analytics
```

| | |
|---|---|
| Frontend | React + Vite + Tailwind. Chat with live agent trace, clickable citations, verification badge; Advisor and Planner views |
| API | FastAPI: JSON and SSE chat and advising, history, feedback, analytics, knowledge endpoints |
| LLM | Gemini (`gemini-3.6-flash`, fallback `gemini-3.5-flash-lite`) or Groq, through one OpenAI-compatible HTTP client with no vendor SDK. Models, reasoning effort and deadlines are env-configurable; `/api/health?deep=true` probes that the key can actually use them |
| Retrieval | ChromaDB (persistence) + in-memory exact hybrid search |
| Storage | Postgres: conversations, messages (with trace, intent, verifier pass rate), feedback |

Design decisions and trade-offs: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Metrics and methodology: **[docs/EVALUATION.md](docs/EVALUATION.md)**.

## Course recommendations (Advisor tab)

Fill in your major, year, completed and in-progress courses, interests and
target load; seven agents take it from there, streamed live to the UI:

| Agent | Role | LLM? |
|---|---|---|
| Intake | Normalises course codes, year and load; flags a low GPA, an overload, or hours that don't match your classification | no |
| Web researcher | Browses the live TXST catalog (`mycatalog.txstate.edu`): finds your program page (known URL, else the catalog search), parses the requirement tables, elective groups and four-year plan, then reads the course pages for the courses you need next | only if the page layout isn't recognised |
| Fact-checker | Checks every fact: official TXST source, quoted verbatim on the fetched page, agrees with the bundled catalog snapshot, right catalog year. Verified / conflict (kept, flagged, stricter reading wins) / dropped | no |
| Degree auditor | Requirements done / in progress / remaining, elective hours, hours left, where you're behind the suggested four-year plan | no |
| Schedule planner | Eligibility in both sources, priority (required, unlocks the most, four-year plan position, your interests), workload balance (a cap on upper-division courses per term, lower for a low GPA), a multi-term roadmap | no |
| Advisor | Writes cited advising notes from the evidence above; template fallback without an LLM | yes |
| Verifier | Claim-level check of the notes; unsupported sentences removed | yes |

The browser is sandboxed: HTTPS only, hosts under `WEB_ALLOWED_DOMAINS`
(default `txstate.edu,txst.edu`), every redirect re-checked, a page budget,
timeout and size cap, and a shared cache. If the catalog can't be reached,
the planner falls back to the prerequisite graph and says so. Settings:
`WEB_BROWSING_ENABLED`, `CATALOG_BASE_URL`, `WEB_MAX_PAGES`,
`WEB_MAX_COURSE_LOOKUPS`, `WEB_TIMEOUT_S`, `WEB_CACHE_TTL_S`.

## Results

| | Score |
|---|---|
| Router intent accuracy (held-out, rules only) | **0.89** |
| Ordinary course questions wrongly declined as instructor questions | **0** (golden and held-out) |
| Topic search hit@4 / MRR (BM25 leg; hybrid not yet re-measured) | **1.00 / 0.90** |
| Prerequisite / planner correctness | **15/15** |
| Answer verification | claim-level, logged per answer |

End-to-end LLM metrics (faithfulness, citation coverage, refusal accuracy)
are produced by `python -m evals.run_eval --suite e2e`. See
[docs/EVALUATION.md](docs/EVALUATION.md) for status.

## Local setup

**Requirements:** Docker + Docker Compose, or Python 3.11+ and Node 20+.

### Option A: Docker Compose

```bash
cp backend/.env.example backend/.env      # add your GEMINI_API_KEY (optional, see below)
cp frontend/.env.example frontend/.env
docker compose up --build -d
```

- Frontend: http://localhost:3000
- API docs (Swagger): http://localhost:8000/docs

The prebuilt index (`backend/data/`) is included. **No API key?** The app
still works: routing falls back to rules, and answers are extractive
(prerequisite results and catalog entries, still cited).

### Option B: Run natively

```bash
# Postgres
docker run -d --name bobcat-pg -e POSTGRES_USER=bobcat -e POSTGRES_PASSWORD=bobcat \
  -e POSTGRES_DB=bobcat_advisor -p 5432:5432 postgres:16-alpine

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend && npm ci && cp .env.example .env && npm run dev
```

### Updating the catalog

Replace `backend/documents/official/coursecatalog.txt` with a newer export,
then:

```bash
cd backend
bash scripts/build_index.sh              # incremental: only new/changed entries are embedded
```

## Tests and evals

```bash
cd backend
pytest -q                                   # incl. the eval gate (no API key needed)
python -m evals.run_eval                    # router + retrieval + tools report
python -m evals.run_eval --suite retrieval --compare-configs
python -m evals.run_eval --suite e2e        # full pipeline + LLM judge (needs key)
ruff check app evals tests
```

Tests run against SQLite and a fake LLM backend, so the whole agent graph
(including verifier revisions, model fallback and budget exhaustion) is
covered offline.

## MCP server

```bash
cd backend
python -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt
```

Add to your MCP client config:

```json
{ "mcpServers": { "bobcat-advisor": {
    "command": "/abs/path/backend/.venv-mcp/bin/python",
    "args": ["/abs/path/backend/mcp_server.py"] } } }
```

Tools: `search_catalog`, `course_info`, `plan_next_courses`, `ask_advisor`,
`recommend_courses`.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/chat/ask` | Ask; returns answer, citations, intent, mode, verifier pass rate |
| POST | `/api/chat/ask/stream` | Same, as Server-Sent Events (`plan`, `agent`, `sources`, `token`, `verification`, `revision`, `done`) |
| GET | `/api/history`, `/api/history/{id}` | Conversations and transcripts |
| POST | `/api/feedback` | 👍/👎 on an answer |
| GET | `/api/analytics` | Headline usage numbers |
| GET | `/api/analytics/agents` | p50/p95 per agent span, tokens, verifier pass rate, feedback by intent |
| GET | `/api/courses`, `/api/courses/{code}` | Catalog, prerequisite tree, unlocks |
| POST | `/api/plan` | `{"completed": [...]}` → eligible courses |
| POST | `/api/advise` | Profile (`major`, `year`, `completed`, `in_progress`, `interests`, `target_credits`, …) → schedule, degree audit, fact-check report, roadmap, advising notes |
| POST | `/api/advise/stream` | Same, as Server-Sent Events (`agent`, `profile`, `browse`, `research`, `factcheck`, `audit`, `schedule`, `sources`, `token`, `verification`, `revision`, `done`) |
| GET | `/api/health` | Index readiness, LLM availability |

## Deployment (Render)

`render.yaml` deploys the backend as a Docker web service on the free plan.
The index and embedding model are baked into the image, so there's no
persistent disk and no model download on cold start. Set `GEMINI_API_KEY`,
`DATABASE_URL` and `CORS_ORIGINS` in the service's environment; migrations
run on boot (`scripts/start.sh`). Deploy `/frontend` as a static site with
`VITE_API_BASE_URL` pointing at the backend.

Memory: the API peaks around 340–390MB. The reranker adds about 100MB, so it's
off on the 512MB free plan (`RERANKER_ENABLED`).

## Project history

The project began as a single-file Gradio RAG demo, then became a
FastAPI + React + Postgres application, and is now a multi-agent system with
evals. Earlier versions also indexed third-party student reviews; those were
removed, and the app now uses only the official catalog.
