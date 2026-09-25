"""Deployment configuration and production hardening: the Render blueprint,
container entrypoint, health endpoints, security headers and client-IP
handling for rate limiting."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.protection import RateLimiter, client_key

REPO = Path(__file__).resolve().parents[2]
yaml = pytest.importorskip("yaml")


@pytest.fixture(scope="module")
def blueprint():
    return yaml.safe_load((REPO / "render.yaml").read_text())


def _api(bp):
    return next(s for s in bp["services"] if s["name"] == "bobcat-advisor-api")


def test_database_is_declared_in_the_apis_region(blueprint):
    """The outage: an internal DB hostname from another region/project."""
    db = blueprint["databases"][0]
    api = _api(blueprint)
    assert db["region"] == api["region"]
    url = next(e for e in api["envVars"] if e["key"] == "DATABASE_URL")
    assert url["fromDatabase"] == {"name": db["name"], "property": "connectionString"}


def test_no_secret_values_in_the_blueprint(blueprint):
    for svc in blueprint["services"]:
        for env in svc.get("envVars", []):
            if any(w in env["key"] for w in ("KEY", "SECRET", "TOKEN", "PASSWORD", "HEADERS")):
                assert env.get("sync") is False and "value" not in env, env["key"]


def test_health_check_path_is_a_real_route(blueprint, client):
    path = _api(blueprint)["healthCheckPath"]
    assert client.get(path).status_code == 200


def test_frontend_is_a_static_site_with_spa_routing(blueprint):
    web = next(s for s in blueprint["services"] if s["runtime"] == "static")
    assert web["staticPublishPath"] == "dist"
    assert {"type": "rewrite", "source": "/*", "destination": "/index.html"} in web["routes"]


def test_memory_settings_fit_the_free_plan(blueprint):
    env = {e["key"]: e.get("value") for e in _api(blueprint)["envVars"]}
    assert env["RERANKER_ENABLED"] == "false" and env["TIMETABLE_SOLVER"] == "search"


def test_start_script_is_valid_and_retries_migrations():
    script = REPO / "backend/scripts/start.sh"
    subprocess.run(["sh", "-n", str(script)], check=True)
    text = script.read_text()
    assert "until alembic upgrade head" in text and "exec uvicorn" in text


def test_migrations_log_which_database_they_target(tmp_path):
    """The log names the target and the setting's source, never the password
    (the Postgres-only migrations themselves can't run on SQLite)."""
    import os
    import sys
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/m.db", "SECRETS_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO / "backend",
                       env=env, capture_output=True, text=True, timeout=120)
    assert f"alembic: migrating database {tmp_path}/m.db (DATABASE_URL from env)" in r.stderr

    from sqlalchemy import make_url
    url = make_url("postgresql+psycopg2://u:s3cr%25t@dpg-abc-a/db")
    assert url.host == "dpg-abc-a" and "s3cr" not in f"{url.host}:{url.port or 5432}/{url.database}"


def test_image_runs_as_non_root():
    text = (REPO / "backend/Dockerfile").read_text()
    assert "\nUSER app" in text and "AS build" in text


# -- health ------------------------------------------------------------------

def test_readiness_reports_database_and_version(client):
    body = client.get("/api/health").json()
    assert body["database"] == "ok" and body["version"] == settings.APP_VERSION
    assert body["status"] in ("ok", "degraded") and "telemetry" in body


def test_readiness_degrades_when_the_database_is_down(client, monkeypatch):
    monkeypatch.setattr("app.main._db_ok", lambda: False)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded" and body["database"] == "unreachable"


def test_security_headers(client):
    r = client.get("/api/courses")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


# -- client IP ------------------------------------------------------------------

def _req(xff=None, host="10.0.0.9"):
    return SimpleNamespace(headers={"x-forwarded-for": xff} if xff else {},
                           client=SimpleNamespace(host=host))


@pytest.mark.parametrize("xff,hops,expected", [
    ("203.0.113.7", 1, "203.0.113.7"),                            # proxy-added only
    ("1.2.3.4, 203.0.113.7", 1, "203.0.113.7"),                   # client forged 1.2.3.4
    ("1.2.3.4, 198.51.100.1, 203.0.113.7", 2, "198.51.100.1"),    # two trusted hops
    (None, 1, "10.0.0.9"),
    ("1.2.3.4", 0, "10.0.0.9"),                                   # header ignored
])
def test_client_key_trusts_only_proxy_added_entries(monkeypatch, xff, hops, expected):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", hops)
    assert client_key(_req(xff)) == expected


def test_forged_forwarded_for_cannot_dodge_the_rate_limit(client, monkeypatch):
    monkeypatch.setattr("app.routers.chat.rate_limiter", RateLimiter(per_minute=2))
    codes = [client.post("/api/chat/ask", json={"question": "hi"},
                         headers={"x-forwarded-for": f"9.9.9.{i}, 203.0.113.7"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]


# -- resilience -------------------------------------------------------------------

def test_a_database_failure_still_returns_the_answer(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def boom(*a, **kw):
        raise OperationalError("INSERT", {}, Exception("database is locked"))
    monkeypatch.setattr("app.services.chat_service.persist_turn", boom)
    r = client.post("/api/chat/ask", json={"question": "What is CS3358 about?"})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is False and body["answer"] and body["message_id"]
    with client.stream("POST", "/api/chat/ask/stream", json={"question": "What is CS2308 about?"}) as s:
        text = "".join(s.iter_text())
    assert '"saved": false' in text


def test_sqlite_uses_wal_and_a_busy_timeout():
    from sqlalchemy import text

    from app.database import engine
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 15000
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
