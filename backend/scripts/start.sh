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
    echo "Check DATABASE_URL (the 'alembic: migrating database ...' line above shows" >&2
    echo "which host was used and where the setting came from):" >&2
    echo " - on Render, set it on the API service's Environment tab; editing the" >&2
    echo "   database itself doesn't change it. Save, then redeploy." >&2
    echo " - an internal dpg-...-a host only resolves from services in the same" >&2
    echo "   region and workspace; otherwise use the External Database URL." >&2
    echo " - a Secret File named DATABASE_URL overrides the environment variable." >&2
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
