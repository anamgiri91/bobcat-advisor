# Bobcat Advisor

A multi-agent AI advisor for Texas State University CS students. It answers
course questions from the official catalog, **computes** prerequisite
eligibility instead of guessing it, builds a next-term plan from the live
TXST catalog, cites every claim, and verifies its own answers before you see
them.

> "Which course covers compilers?" · "Can I retake CS2308 to replace a D?"
> · "What's the last day to drop?" · "What's the textbook for CS3358?" ·
> "I've taken CS1428 and CS2308, what can I take next?"

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
- **Knowledge base of official TXST pages.** Academic rules, core
  curriculum, graduate catalog, CS department, registrar and calendar,
  student handbook, and public course syllabi — crawled from TXST sites
  only, split at headings, tagged with URL, catalog year and fetch date,
  instructor details removed from syllabi, deadlines stored as dated
  facts, refreshed by a weekly workflow (see below).
- **Career paths.** Pick a role (ML engineer, security, data, games…) or
  describe one: agents map it to skills, find the TXST courses that teach
  each (in the order you can take them), and search for outside courses
  and certifications for what the catalog doesn't cover, opening every
  link to check it's live and on topic before showing it (see below).
- **What-if planning.** Switch major, add a minor, fail a course or change
  the load and see how the graduation term moves.
- **Production-ready.** OpenTelemetry traces and metrics with a latency and
  cost dashboard, secrets from AWS/GCP secret managers or secret files, a
  Render blueprint with its own database, a hardened image, a published
  load test, and 93% test coverage enforced in CI.
- **Class schedule and timetables.** Sections live in a structured store
  (not the search index); an OR-Tools CP-SAT solver builds clash-free
  weekly timetables around work hours and preferred days, and the Advisor
  learns which terms each course is usually offered in.
- **Hybrid retrieval.** Dense (MiniLM, ONNX) + BM25 over the catalog, fused
  with RRF, course filters with relaxation, and optional cross-encoder
  reranking.
- **Prerequisite graph.** The catalog is parsed into CNF prerequisites.
  Eligibility and "what does this unlock" are set logic, never generated.
- **Self-verification.** Every sentence is checked against the evidence it
  cites; unsupported sentences are removed, and the pass rate is logged per answer.
- **Evals in CI.** 85-case golden set plus a 25-case held-out set;
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

## Knowledge base (official TXST pages)

`backend/app/kb/` crawls the sources in `app/kb/sources.json` and feeds them
into the same search index as the catalog:

| Source | Kind | Answers |
|---|---|---|
| Undergraduate catalog: academic rules | `policy` | repeating a course, pass/fail, withdrawal, course load, probation, graduation, transfer/AP credit |
| Core curriculum | `core` | which courses count for each core area |
| Graduate catalog | `grad_catalog` | taking graduate courses as an undergraduate |
| CS department | `department` | research, internships/co-op, honors, the BS/MS track, advising |
| Registrar and academic calendar | `registrar` | registration, full classes, deadlines |
| Student handbook, honor code | `handbook` | academic integrity, AI use |
| Public course syllabi (Texas HB 2504) | `syllabus` | textbook, weekly topics, projects, grading scheme |

How it's built:

- **Only official pages.** The crawler reuses the advising browser's sandbox
  (HTTPS, TXST hosts, per-hop redirect checks, size/time/page limits),
  obeys robots.txt, stays on each source's hosts, and only follows links
  matching the source's keywords. Seeds are hub pages that are crawled
  through, not indexed.
- **Split at headings.** Chunks follow the page's h1–h4 structure (or
  detected headings in PDF syllabi). A tiny subsection folds into its
  parent; siblings never merge, so each chunk keeps its own heading.
- **Tagged.** Every chunk records its URL, page title, heading path,
  catalog year (catalog pages) and fetch date; citations show the
  section and year and link to the page.
- **People removed.** Syllabus sections about the instructor, contact or
  office hours are dropped; lines naming a person, emails, phone numbers
  and titled names are removed before indexing.
- **Dates as data.** Calendar lines become dated facts
  (`data/kb_dates.jsonl`) with the year taken from the section's term,
  never guessed. The calendar agent marks each fact past or upcoming
  relative to today; the answer writer only states dates from these facts.
- **Kept fresh.** Each source has a refresh interval; pages older than
  `KB_MAX_AGE_DAYS` aren't used to answer. `.github/workflows/kb-refresh.yml`
  re-crawls weekly, rebuilds the index, runs the tests and evals, and opens
  a pull request, so every refresh is reviewed before it ships.

```bash
cd backend
python -m app.kb.build                      # crawl all sources -> data/kb_*.jsonl
python -m app.kb.build --sources registrar  # one source; others kept
python -m app.kb.build --check-freshness    # which sources are overdue
bash scripts/build_index.sh                 # merge into the index (embeds only changes)
```

The sources' seed URLs are starting points: pages are found by following
links, so a moved page is still discovered. Check the crawl report after
the first run and adjust `sources.json` if a source finds no pages.

## Class schedule and timetable builder

Tables belong in structured stores that tools query, not in the search
index. `backend/app/structured/` holds the class schedule:

- **Sections table** (`data/schedule_sections.jsonl`): term, course,
  section, CRN, meeting days/times, seats, modality, campus. The parser maps
  columns by header name (CRN, Days, Time, Cap/Act/Rem, ...), so it reads
  most HTML schedule tables and CSV exports; lab rows without a CRN become
  extra meetings of the section above. **Instructor columns are never
  stored.**
- **Offering history** (`data/course_offerings.json`): from every stored
  term, "usually offered in Fall (4 of 4 Falls, 0 of 4 Springs)". Shown as
  "usually", never a guarantee, and it only constrains a plan when a season
  was observed at least twice with the course never offered in it. A term
  only counts for subjects it actually has data for.
- **Timetable builder** (`timetable.py`): OR-Tools CP-SAT. Hard: one section
  per course, no clashes, busy blocks, earliest/latest times, modality,
  full sections excluded. Soft: days outside the preferred days, then days
  on campus. Returns up to 3 options and explains any course it can't place.
  An exact fallback search gives the same optimum (cross-checked in tests)
  when `TIMETABLE_SOLVER=search`, which avoids OR-Tools' ~85MB of memory.

In the Advisor, the planner skips courses history says aren't offered that
season (and, when the planned term is loaded, courses with no sections
listed), labels the roadmap with real terms ("Spring 2027"), and the new
timetable step lays out the recommended courses on a weekly grid.

```bash
cd backend
python -m app.structured.build --import-csv fall.csv --term "Fall 2026"   # load an export
python -m app.structured.build                                          # fetch configured terms
```

TXST's schedule site hasn't been checked yet (it may need a form POST or an
API rather than page fetches), so `app/structured/sources.json` ships with no
URL: fetching is a no-op until one is configured, and CSV/HTML import works
now. The weekly refresh workflow runs the fetch alongside the knowledge base.

## Results

| | Score |
|---|---|
| Router intent accuracy (held-out, rules only; the LLM router covers paraphrases) | **0.80** |
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
pytest -q --cov=app                         # 250 tests incl. the eval gate; no API key needed
python -m evals.run_eval                    # router + retrieval + kb + tools report
python -m evals.run_eval --suite e2e        # full pipeline + LLM judge (needs key)
ruff check app evals tests
cd ../frontend && npm test                  # Vitest + Testing Library
```

- **Unit and integration** against SQLite, a fake LLM backend and sample
  catalog/schedule pages, so the whole agent graph (verifier revisions,
  model fallback, budget exhaustion, streaming) runs offline.
- **Property-based** (Hypothesis): timetables are clash-free and optimal
  against brute force, eligibility matches the prerequisites, scrubbing
  never leaves contact details, the URL allowlist resists suffix tricks,
  parsers round-trip every time/day format.
- **Deployment**: the Render blueprint (database region, no inline
  secrets, a real health path), the entrypoint, the non-root image.
- **Observability**: span trees, metrics and cost against in-memory OTel
  exporters, and a check that the Grafana dashboard only queries metrics
  the app emits.
- CI requires 90% backend coverage (currently 93%), scans history for
  committed secrets (gitleaks), and runs the frontend tests.

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

Tools: `search_catalog`, `search_knowledge_base`, `course_info`, `plan_next_courses`, `build_timetable`, `ask_advisor`,
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
| GET | `/api/schedule/terms` | Terms with section data |
| GET | `/api/schedule/sections?course=&term=` | Sections for a course (no instructor data) |
| GET | `/api/offerings/{code}` | "Usually offered in …" with counts |
| POST | `/api/timetable` | `{term, courses, preferred_days, earliest_start, latest_end, busy, modality}` → clash-free timetables |
| GET | `/api/career/paths` | Career paths the app knows |
| POST | `/api/career/stream` | SSE: `{career, completed, in_progress, include_certifications, free_only}` → skills, TXST courses, checked outside resources, roadmap |
| POST | `/api/career` | Same as JSON |
| POST | `/api/advise/whatif` | `{profile, scenarios: [{type: switch_major/add_minor/fail_course/change_load, ...}]}` → graduation term per scenario |
| GET | `/api/health/live` | Liveness (process up) |
| GET | `/api/health` | Readiness: database, index, LLM, telemetry |

## Career paths (Careers tab)

"I want to be a machine learning engineer: what should I take, and what
do I learn outside class?" Seven agents answer it:

| Agent | Does | LLM? |
|---|---|---|
| Career analyst | Maps the goal to one of 9 defined careers and its core/helpful skills (rules first; an LLM may only pick from the list) | fallback only |
| Course mapper | Finds the TXST courses whose catalog title or description names each skill, keeps the sentence as evidence, ranks them, adds gateway prerequisites (e.g. CS 3358 unlocks most 4000-level courses) and marks each done / take next / later | no |
| Gap finder | Skill coverage: a course named for it, a passing mention, or nothing in the undergraduate CS catalog | no |
| Resource scout | For each gap (and a few core skills to go deeper on): a web search when `SEARCH_PROVIDER=brave` and `BRAVE_API_KEY` are set, kept only on known providers' sites, plus a curated seed list (`app/careers/data/resources.json`) | no |
| Link checker | Opens every candidate with the sandboxed browser (HTTPS, provider allowlist, redirects re-checked, page budget) in parallel. Verified = loaded and about the skill; a curated link a site won't let a bot check is shown as "not checked"; dead links, off-topic pages and unopenable search results are dropped | no |
| Mentor | A short roadmap from those facts (template without an LLM) | yes |
| Verifier | Removes any roadmap sentence naming a course code it wasn't given, or a URL | no |

The same skill map answers topic questions in the chat: "How do I learn
AI?" names no course, and the catalog says "Artificial Intelligence", not
"AI", so keyword search alone misses. The router maps the words students use
("AI", "cyber", "web dev", "SQL") to skills, picks the courses that teach
them, and the catalog agent adds a computed summary: the courses with their
prerequisites, the path to reach them, graduate-level and non-major courses
on the topic (flagged, not recommended), and a pointer to the Careers tab.

Rules the data follows: courses that "will not satisfy CS major"
requirements (e.g. CS 1309) and graduate courses are never recommended;
skills taught outside CS (linear algebra, statistics) are never mapped to
an invented TXST course number; costs are coarse labels and the UI says
to check the provider's site; outside links are marked as not affiliated.

## What-if planning

"Switch to a data science minor: when do I graduate?" The **What if…** card
in the Advisor (and `POST /api/advise/whatif`) compares up to four
scenarios with your current plan: switch major, add a minor, fail a
course, or change hours per term. Each reruns research, fact-check, audit
and roadmap on the changed profile, with no LLM, so the numbers are
reproducible. Graduation is the slower of the roadmap and the remaining
hours at your load, and a failed attempt adds its hours back. Every result
lists its assumptions (e.g. minor courses counting toward the major too).

## Observability

Each request's trace is exported over **OpenTelemetry** as a span tree (one
span per agent, LLM call and retrieval step, with real timings) plus
metrics: latency by route and pipeline, per-step p95, requests, LLM calls
by outcome, tokens by model and type, and **estimated cost** (token counts
× `LLM_PRICES`, reasoning tokens included). Point
`OTEL_EXPORTER_OTLP_ENDPOINT` at any OTLP backend (Grafana Cloud,
Honeycomb, a collector). Locally:

```bash
docker compose -f docker-compose.yml -f observability/docker-compose.yml up -d
# Grafana http://localhost:3001 (dashboard "Bobcat Advisor"), Jaeger http://localhost:16686
```

The dashboard (`observability/grafana/dashboards/bobcat-advisor.json`)
shows traffic, p50/p95 latency, 5xx rate, per-step latency, cost per
hour/request/day, tokens, model fallbacks and answer modes.

## Secrets

API keys, the database URL and telemetry auth headers are read through
`app/secrets.py`: `NAME_FILE`, then AWS Secrets Manager or Google Secret
Manager (`SECRETS_BACKEND=aws|gcp`, `SECRETS_PREFIX`), then secret files
(`/etc/secrets/NAME` — Render Secret Files — or `/run/secrets/NAME`), then
the environment. Loaded values are masked in logs, `render.yaml` holds no
secret values, and CI runs gitleaks over the whole history.

## Deployment (Render)

`render.yaml` is a Blueprint for the API (Docker), its Postgres database
(same region) and the frontend (static site). Step by step, including
secrets and checks: **[docs/DEPLOY.md](docs/DEPLOY.md)**.

- The image bakes in the index and embedding model (no download on cold
  start), is multi-stage, and runs as a non-root user.
- The entrypoint retries migrations while a new database comes up and
  explains a bad `DATABASE_URL` instead of crashing with a traceback.
- `/api/health/live` is the platform's liveness check; `/api/health`
  reports database, index, model and telemetry status.
- Memory: the API peaks around 340–390MB with hybrid retrieval. The
  reranker (~100MB) is off and the timetable uses the exact search
  instead of OR-Tools (~85MB) on the 512MB plan.

## Load test

`backend/loadtest/` holds a Locust scenario and a network-free server
harness. On one worker: 100 concurrent users at 0 failures and 22ms p95
overall, and ~234 req/s at saturation. Chat is the CPU-bound limit, and in
production the LLM provider's latency dominates. The test found and fixed
"database is locked" failures under concurrent writes. Details and caveats:
**[docs/LOAD_TEST.md](docs/LOAD_TEST.md)**.

## Project history

The project began as a single-file Gradio RAG demo, then became a
FastAPI + React + Postgres application, and is now a multi-agent system with
evals. Earlier versions also indexed third-party student reviews; those were
removed, and the app now uses only the official catalog.
