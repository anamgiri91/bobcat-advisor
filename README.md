# Bobcat Advisor

A full-stack RAG application that answers questions about Texas State
University CS professors — teaching style, exam difficulty, workload,
grading — grounded in real RateMyProfessors, Coursicle, and Reddit reviews,
plus the official course catalog.

This started as a single-file Gradio demo. This version rebuilds it into a
proper client/server application: a **React frontend**, a **FastAPI
backend**, and a **Postgres** database for chat history and feedback,
alongside the original ChromaDB retrieval pipeline.

## Architecture

```
┌──────────────┐      REST (JSON)      ┌───────────────────┐
│   React SPA   │  ───────────────────▶ │     FastAPI        │
│  (Vite, Tailwind) │ ◀─────────────────  │  app/main.py        │
└──────────────┘                       └─────────┬──────────┘
                                                  │
                        ┌─────────────────────────┼─────────────────────────┐
                        │                         │                         │
                 ┌──────▼──────┐          ┌────────▼────────┐        ┌───────▼───────┐
                 │  Postgres    │          │   ChromaDB       │        │     Groq API    │
                 │  chat history│          │   vector store    │        │  llama-3.3-70b   │
                 │  + feedback  │          │  (unchanged RAG) │        │   generation      │
                 └──────────────┘          └──────────────────┘        └─────────────────┘
```

**Why two databases?** ChromaDB stores the document embeddings used for
semantic search — that's the RAG index, and it doesn't change often.
Postgres stores everything about *usage*: every question asked, every
answer generated, latency, which source filter was applied, and user
feedback (👍/👎) on individual answers. That split mirrors how most real
RAG products are built — a specialized vector store for retrieval, a
relational store for the application's own state — rather than jamming
both into one system.

### Backend (`/backend`)

- **FastAPI** — REST API (`/api/chat/ask`, `/api/history`, `/api/feedback`)
- **SQLAlchemy + Alembic** — Postgres models and versioned migrations for
  `conversations`, `messages`, and `feedback`
- **RAG pipeline** (`app/rag/`) — chunking, cleaning, embedding, retrieval,
  and generation logic carried over from the original project:
  - `chunker.py` / `cleaner.py` / `ingest.py` — parse and clean the raw
    review corpus into chunks
  - `embed.py` — embeds chunks with `all-MiniLM-L6-v2` into ChromaDB
  - `retrieve.py` — semantic search with professor/course/source filters,
    plus a balanced retrieval mode for comparison questions
  - `generate.py` — grounded generation via Groq (`llama-3.3-70b-versatile`),
    with programmatic (non-hallucinated) source attribution

### Frontend (`/frontend`)

- **React + Vite + Tailwind** — a chat interface with:
  - Conversation history sidebar (persisted in Postgres, not local state)
  - Source filter chips (RateMyProfessors / Coursicle / Reddit / Catalog)
  - Per-answer source chips and retrieval stats (chunk count, latency)
  - 👍/👎 feedback on individual answers

## Local setup

**Requirements:** Docker + Docker Compose (simplest), or Python 3.11+ /
Node 20+ if running services natively.

### Option A — Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env      # add your GROQ_API_KEY
cp frontend/.env.example frontend/.env

docker compose up --build -d
```

A prebuilt ChromaDB index (`backend/data/chroma_db/`) and its source
`chunks.jsonl` are included, so you can skip straight to asking questions.
If you add new review documents to `backend/documents/` later, rebuild the
index with:

```bash
docker compose exec backend bash scripts/build_index.sh
```

- Frontend: http://localhost:3000
- Backend docs (Swagger): http://localhost:8000/docs

### Option B — Run natively

```bash
# 1. Postgres (or point DATABASE_URL at any Postgres instance you have)
docker run -d --name bobcat-pg -e POSTGRES_USER=bobcat -e POSTGRES_PASSWORD=bobcat \
  -e POSTGRES_DB=bobcat_advisor -p 5432:5432 postgres:16-alpine

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your GROQ_API_KEY
# data/chroma_db and data/chunks.jsonl are already included — run
# scripts/build_index.sh only if you change the documents/ corpus
alembic upgrade head           # create Postgres tables
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
cp .env.example .env
npm run dev
```

### Running the retrieval test suite

A regression test (`backend/tests/test_retrieval.py`) checks retrieval
quality against five hand-labelled expected answers and writes a full
report to `data/retrieval_report.txt`. Run it after touching the chunking,
cleaning, embedding, or retrieval code:

```bash
cd backend
python -m tests.test_retrieval
```

## Deployment

This mirrors the pattern from my [Provenance Guard](https://github.com/anamgiri91)
deployment: **Render** for the backend + managed Postgres, and a static
host (Render static site or Vercel) for the frontend.

1. **Postgres** — create a Render managed Postgres instance; copy its
   connection string into the backend's `DATABASE_URL`.
2. **Backend** — deploy `/backend` as a Render web service (Docker runtime,
   `render.yaml` can be adapted from the original single-service one).
   Set `GROQ_API_KEY`, `DATABASE_URL`, and `CORS_ORIGINS` (your frontend's
   URL) as environment variables. Run `alembic upgrade head` and
   `scripts/build_index.sh` once via a Render shell or a one-off job — the
   ChromaDB index is baked into a persistent disk so it doesn't rebuild on
   every deploy.
3. **Frontend** — deploy `/frontend` as a static site; set
   `VITE_API_BASE_URL` to the backend's public URL.

## API reference

| Method | Path                          | Description                                  |
|--------|-------------------------------|-----------------------------------------------|
| POST   | `/api/chat/ask`               | Ask a question; returns answer + sources      |
| GET    | `/api/history`                | List recent conversations                     |
| GET    | `/api/history/{id}`           | Full transcript for one conversation           |
| POST   | `/api/feedback`               | Submit 👍/👎 for an assistant message         |
| GET    | `/api/health`                 | Health check                                   |

Full interactive docs at `/docs` (Swagger) once the backend is running.

## Project history

`/docs` carries over the original project's planning and debugging notes
(`planning.md`, `problems.md`) and a retrieval-quality test report
(`retrieval_report.txt`) — kept for reference on decisions made during the
original single-file build.

## What changed from the original project

- Replaced the Gradio UI with a React SPA that talks to a real REST API
- Added a FastAPI backend with routers, Pydantic schemas, and a clean
  separation between the API layer and the RAG pipeline
- Added Postgres (via SQLAlchemy + Alembic migrations) for chat history,
  per-answer analytics (latency, retrieved chunk count), and user feedback
- Containerized the whole stack with Docker Compose
- Retrieval and generation logic (ChromaDB + sentence-transformers + Groq)
  is unchanged — the improvements here are architectural, not to the RAG
  quality itself
