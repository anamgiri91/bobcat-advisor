# Load test

`backend/loadtest/locustfile.py` drives the API with a traffic mix like
the UI's: catalog lookups, chat questions (half repeated, half unique so the
answer cache is bypassed), plans, timetables, advising runs and what-if
comparisons. Raw results are in `backend/loadtest/results/`.

## What these numbers measure

The app's own overhead: routing, retrieval, the prerequisite graph,
advising (research parsing, fact-check, audit, schedule, timetable) and
database writes. **Not** the LLM provider: the runs had no API key, so chat
answers were extractive. With an LLM, answer latency is dominated by the
provider (3–28s per call was measured against Gemini during development;
see ARCHITECTURE.md). Also:

| | Load test | Production (Render free plan) |
|---|---|---|
| Machine | 4 vCPU cloud sandbox | shared CPU, 512MB |
| Workers | 1 uvicorn worker | 1 uvicorn worker |
| Database | SQLite (WAL, busy timeout) | Postgres |
| Retrieval | keyword (BM25) only: the embedding model couldn't be downloaded in the sandbox | hybrid (adds a ~4ms query embedding) |
| Catalog pages | sample pages served in-process (no network) | live TXST catalog, cached per page |

Treat the results as the app's ceiling on one worker, not as a production
SLA. Re-run against a deployed instance with
`locust -f loadtest/locustfile.py --host https://<api-host>` (and lift the
per-IP rate limit for the test, or it will be what you measure).

## How to run

```bash
cd backend
python loadtest/serve_loadtest.py &                     # SQLite, sample data, no outbound calls
locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \
       --headless -u 100 -r 10 -t 60s --csv results/u100
LOCUST_NO_WAIT=1 locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \
       --headless -u 20 -r 20 -t 40s --csv results/stress   # saturation
```

## Results

### 100 concurrent users, 1–3s think time (60s, ~47 req/s offered)

| Endpoint | Requests | Failures | p50 | p95 | p99 |
|---|---|---|---|---|---|
| `POST /api/chat/ask` | 657 | 0 | 9 ms | 33 ms | 77 ms |
| `POST /api/advise` | 166 | 0 | 11 ms | 36 ms | 48 ms |
| `POST /api/advise/whatif` (2 scenarios) | 172 | 0 | 15 ms | 33 ms | 63 ms |
| `POST /api/timetable` | 366 | 0 | 4 ms | 19 ms | 40 ms |
| `POST /api/plan` | 355 | 0 | 3 ms | 12 ms | 30 ms |
| `GET /api/courses/{code}` | 541 | 0 | 3 ms | 10 ms | 28 ms |
| **All** | **2,762** | **0** | **4 ms** | **22 ms** | **47 ms** |

### Saturation: 20 users, no think time (40s)

| Endpoint | Requests | Failures | req/s | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| `POST /api/chat/ask` | 2,290 | 0 | 58.6 | 120 ms | 1,000 ms | 2,100 ms |
| `POST /api/advise` | 591 | 0 | 15.1 | 46 ms | 88 ms | 140 ms |
| `POST /api/advise/whatif` | 559 | 0 | 14.3 | 49 ms | 92 ms | 140 ms |
| `POST /api/timetable` | 1,132 | 0 | 29.0 | 27 ms | 53 ms | 81 ms |
| **All** | **9,152** | **0** | **234** | **31 ms** | **240 ms** | **1,100 ms** |

Peak memory of the server process: ~160 MB (keyword retrieval; the
embedding model adds roughly 90 MB, per the measurements in ARCHITECTURE.md).

## What the test found and changed

1. **"database is locked" under concurrent chat writes (SQLite).** The
   first saturation run had 2 chat 500s. Fixes: SQLite now uses WAL and a
   15s busy timeout, and a failed history write no longer fails the
   request — the answer is returned with `saved: false` and the error is
   logged. Postgres (production) gets an explicit pool size, pre-ping and
   connection recycling. Re-run: 0 failures in 9,152 requests.
2. **Chat is the throughput limit** at ~59 req/s on one worker: it is
   CPU-bound Python (routing, BM25 over the index, extractive synthesis)
   and holds the GIL, so at saturation queueing shows up as a 1s p95. With
   an LLM the provider's latency dominates long before this ceiling.
   Scaling is more workers (`WEB_CONCURRENCY`) or instances — each worker
   holds its own index and model, so on a 512MB plan that means a bigger
   plan, not more workers.
3. Earlier profiling (commit "perf: precompute the reverse prerequisite
   graph...") had already cut the advising pipeline from ~26ms to ~15ms.
