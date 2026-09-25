# Deploying to Render

`render.yaml` creates three things in one region: the API (Docker web
service), its Postgres database, and the frontend (static site).

## 1. Create the Blueprint

1. Merge the branch you want to deploy into `main`.
2. Render dashboard → **New → Blueprint** → choose this repository →
   **Apply**. Render creates `bobcat-advisor-db`, `bobcat-advisor-api` and
   `bobcat-advisor-web`. `DATABASE_URL` is wired from the database
   automatically: don't set it by hand.
3. If an older `bobcat-advisor-api` service exists (the one that failed with
   `could not translate host name "dpg-...-a"`), delete it or let the
   Blueprint take it over; that error came from its hand-copied database
   URL pointing at a database that no longer resolved.

## 2. Set the secrets

In each service's **Environment** tab, fill in the `sync: false` values:

| Service | Key | Value |
|---|---|---|
| API | `GEMINI_API_KEY` | your key (or add it as a **Secret File** named `GEMINI_API_KEY`: app/secrets.py reads `/etc/secrets/GEMINI_API_KEY`) |
| API | `CORS_ORIGINS` | the frontend's host, e.g. `bobcat-advisor-web.onrender.com` |
| API | `OTEL_EXPORTER_OTLP_ENDPOINT` | optional: your OTLP endpoint (e.g. Grafana Cloud) |
| API | `LLM_PRICES` | optional: `{"model": [usd_per_1M_input, usd_per_1M_output]}` from the provider's pricing page |
| Web | `VITE_API_BASE_URL` | the API's URL + `/api`, e.g. `https://bobcat-advisor-api.onrender.com/api` |

Put `OTEL_EXPORTER_OTLP_HEADERS` (telemetry auth) in a **Secret File**, not
an env var. After changing `VITE_API_BASE_URL`, redeploy the static site:
it's baked in at build time.

## 3. Check it

```bash
API=https://bobcat-advisor-api.onrender.com
curl $API/api/health/live                  # {"status":"ok"}: liveness (the platform's check)
curl $API/api/health                       # database "ok", index_ready true, llm_available true
curl "$API/api/health?deep=true"           # one real call per model: all "ok" (costs a little quota)
curl -X POST $API/api/chat/ask -H 'content-type: application/json' \
     -d '{"question":"What are the prerequisites for CS3360?"}'
```

Then open the frontend URL. That's the demo link.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `could not translate host name "dpg-...-a"` | `DATABASE_URL` points at a database in another region/workspace, or one that was deleted. Use the Blueprint's database (`fromDatabase`), or the database's *External* URL. |
| `FATAL: database migrations failed 6 times` | The database isn't reachable; same checks as above. The entrypoint retries for ~1 minute first. |
| Health `models_unlisted` non-empty | The key can't use a configured model: change `LLM_*_MODEL` and check `?deep=true`. |
| Frontend loads but requests fail with CORS errors | `CORS_ORIGINS` doesn't match the frontend host. |
| First request after a while takes ~30–60s | The free plan sleeps when idle. Upgrade for an always-on demo. |
| The database disappears after some weeks | Free Render Postgres databases expire; check Render's current terms and upgrade to keep one. |
