"""Secret resolution order, cloud secret managers (faked), caching, and
log redaction."""

from __future__ import annotations

import logging

import pytest

from app import secrets


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    secrets.clear_cache()
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("SECRETS_BACKEND", raising=False)
    monkeypatch.delenv("SECRETS_PREFIX", raising=False)
    for name in ("DEMO_KEY", "DEMO_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    secrets.clear_cache()


class FakeAws:
    def __init__(self, values):
        self.values, self.calls = values, []

    def get_secret_value(self, SecretId):  # noqa: N803 (boto3's signature)
        self.calls.append(SecretId)
        if SecretId not in self.values:
            raise type("ResourceNotFoundException", (Exception,), {})()
        return {"SecretString": self.values[SecretId]}


class FakeGcp:
    def __init__(self, values):
        self.values = values

    def access_secret_version(self, name):
        if name not in self.values:
            raise type("NotFound", (Exception,), {})()
        payload = type("P", (), {"data": self.values[name].encode()})()
        return type("R", (), {"payload": payload})()


def test_env_is_the_fallback(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "from-env")
    assert secrets.get_secret("DEMO_KEY") == "from-env"
    assert secrets.get_secret("MISSING_KEY", "dflt") == "dflt"


def test_secret_files_beat_env(monkeypatch, _fresh):
    (_fresh / "DEMO_KEY").write_text("from-file\n")
    monkeypatch.setenv("DEMO_KEY", "from-env")
    assert secrets.get_secret("DEMO_KEY") == "from-file"


def test_explicit_file_beats_everything(monkeypatch, _fresh, tmp_path_factory):
    other = tmp_path_factory.mktemp("s") / "key"
    other.write_text("from-name-file")
    (_fresh / "DEMO_KEY").write_text("from-dir")
    monkeypatch.setenv("DEMO_KEY_FILE", str(other))
    assert secrets.get_secret("DEMO_KEY") == "from-name-file"


def test_aws_secrets_manager(monkeypatch):
    fake = FakeAws({"bobcat/DEMO_KEY": "from-aws"})
    secrets._clients["aws"] = fake
    monkeypatch.setenv("SECRETS_BACKEND", "aws")
    monkeypatch.setenv("SECRETS_PREFIX", "bobcat/")
    monkeypatch.setenv("DEMO_KEY", "from-env")
    assert secrets.get_secret("DEMO_KEY") == "from-aws"
    assert secrets.get_secret("DEMO_KEY") == "from-aws"
    assert fake.calls == ["bobcat/DEMO_KEY"]                       # cached after the first read
    assert secrets.get_secret("OTHER_KEY") is None                  # not found -> falls through


def test_gcp_secret_manager(monkeypatch):
    secrets._clients["gcp"] = FakeGcp({"projects/p1/secrets/DEMO_KEY/versions/latest": "from-gcp"})
    monkeypatch.setenv("SECRETS_BACKEND", "gcp")
    monkeypatch.setenv("GCP_PROJECT", "p1")
    assert secrets.get_secret("DEMO_KEY") == "from-gcp"


def test_manager_outage_falls_back_and_logs(monkeypatch, caplog):
    class Down:
        def get_secret_value(self, SecretId):  # noqa: N803
            raise ConnectionError("unreachable")
    secrets._clients["aws"] = Down()
    monkeypatch.setenv("SECRETS_BACKEND", "aws")
    monkeypatch.setenv("DEMO_KEY", "from-env")
    with caplog.at_level(logging.ERROR, logger="bobcat.secrets"):
        assert secrets.get_secret("DEMO_KEY") == "from-env"
    assert "ConnectionError" in caplog.text


def test_unknown_backend_is_reported(monkeypatch, caplog):
    monkeypatch.setenv("SECRETS_BACKEND", "vault9000")
    monkeypatch.setenv("DEMO_KEY", "v")
    with caplog.at_level(logging.ERROR, logger="bobcat.secrets"):
        assert secrets.get_secret("DEMO_KEY") == "v"
    assert "Unknown SECRETS_BACKEND" in caplog.text


def test_export_to_env_does_not_override(monkeypatch, _fresh):
    (_fresh / "DEMO_KEY").write_text("from-file")
    secrets.export_to_env("DEMO_KEY")
    import os
    assert os.environ["DEMO_KEY"] == "from-file"
    secrets.clear_cache()
    (_fresh / "DEMO_KEY").write_text("changed")
    secrets.export_to_env("DEMO_KEY")
    assert os.environ["DEMO_KEY"] == "from-file"


def test_loaded_secrets_are_masked_in_logs(monkeypatch, caplog):
    monkeypatch.setenv("DEMO_KEY", "sk-supersecret-123456")
    secrets.get_secret("DEMO_KEY")
    logger = logging.getLogger("bobcat.test.redact")
    secrets.install_log_redaction(logger)
    secrets.install_log_redaction(logger)                     # idempotent
    assert sum(isinstance(f, secrets.RedactSecrets) for f in logger.filters) == 1
    with caplog.at_level(logging.INFO, logger="bobcat.test.redact"):
        logger.info("calling provider with key %s", "sk-supersecret-123456")
    assert "sk-supersecret" not in caplog.text and "***" in caplog.text


def test_settings_read_keys_from_secret_files(monkeypatch, _fresh):
    """A fresh Settings picks the API key and database URL up from secret files."""
    import importlib.util

    import app.config as config
    (_fresh / "GEMINI_API_KEY").write_text("key-from-secret-file")
    (_fresh / "DATABASE_URL").write_text("postgresql://u:p@db:5432/x")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    spec = importlib.util.spec_from_file_location("app._config_copy", config.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.settings.GEMINI_API_KEY == "key-from-secret-file"
    assert fresh.settings.LLM_PROVIDER == "gemini" and fresh.settings.LLM_API_KEY == "key-from-secret-file"
    assert fresh.settings.DATABASE_URL == "postgresql://u:p@db:5432/x"


def test_cors_origins_accept_bare_hosts():
    from app.config import _origins
    assert _origins("bobcat.onrender.com, http://localhost:5173,") == [
        "https://bobcat.onrender.com", "http://localhost:5173"]


def test_source_is_reported_without_the_value(tmp_path, monkeypatch):
    """Startup logs say where DATABASE_URL came from: a stale Secret File
    silently overriding a corrected env var was otherwise invisible."""
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("SRC_A", "from-env")
    (tmp_path / "SRC_B").write_text("from-file")
    assert secrets.get_secret("SRC_A") == "from-env" and secrets.source_of("SRC_A") == "env"
    assert secrets.get_secret("SRC_B") == "from-file" and secrets.source_of("SRC_B") == "file"
    assert secrets.get_secret("SRC_C", "d") == "d" and secrets.source_of("SRC_C") == "default"
