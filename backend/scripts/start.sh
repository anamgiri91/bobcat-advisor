#!/bin/sh
# Container entrypoint: run migrations (retrying while a freshly created
# database comes up), then serve. A bad DATABASE_URL fails fast with a
# message that says what to check, instead of a bare traceback.
set -eu

attempt=1
max=${MIGRATION_ATTEMPTS:-6}
delay=2
until alembic upgrade head; do
  if [ "$attempt" -ge "$max" ]; then
    echo "FATAL: database migrations failed $max times." >&2
    echo "Check DATABASE_URL: its host must be reachable from this service" >&2
    echo "(on Render, an internal dpg-...-a host only resolves in the same region)." >&2
    exit 1
  fi
  echo "Migrations failed (attempt $attempt/$max); retrying in ${delay}s..." >&2
  sleep "$delay"
  attempt=$((attempt + 1))
  delay=$((delay * 2))
done

# One worker: the index and models live in process memory (512MB plans).
# Proxy headers: Render's edge terminates TLS and forwards the client IP.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips='*' \
  --timeout-graceful-shutdown 20
