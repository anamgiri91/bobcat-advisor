"""
secrets.py
==========
Resolves secrets (LLM API keys, the database URL, telemetry auth headers)
from a secrets manager or mounted secret files, with plain environment
variables as the last resort.

For a secret NAME, the first of these that yields a value wins:

  1. NAME_FILE           path to a file holding the value (Docker/Kubernetes
                         secrets convention)
  2. SECRETS_BACKEND     a cloud secrets manager:
                           aws  AWS Secrets Manager, secret id SECRETS_PREFIX + NAME
                                (needs boto3 and AWS credentials)
                           gcp  Google Secret Manager, projects/GCP_PROJECT/secrets/
                                SECRETS_PREFIX + NAME/versions/latest
                                (needs google-cloud-secret-manager)
  3. secret files        SECRETS_DIR/NAME, else /etc/secrets/NAME (Render
                         Secret Files) or /run/secrets/NAME (Docker)
  4. NAME                the environment variable itself

Values are cached for the process lifetime and registered with a logging
filter that masks them, so a secret can't leak through a log line or an
exception message. This module deliberately doesn't import app.config:
config imports it.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("bobcat.secrets")

DEFAULT_DIRS = ("/etc/secrets", "/run/secrets")
_cache: dict[str, str | None] = {}
_sources: dict[str, str] = {}
_lock = threading.Lock()
_known_values: set[str] = set()
_clients: dict[str, object] = {}


def _from_file(path: str | Path) -> str | None:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _from_aws(secret_id: str) -> str | None:
    client = _clients.get("aws")
    if client is None:
        import boto3  # optional dependency
        client = _clients["aws"] = boto3.client("secretsmanager")
    try:
        return client.get_secret_value(SecretId=secret_id).get("SecretString") or None
    except Exception as e:
        if type(e).__name__ in ("ResourceNotFoundException",) or "NotFound" in type(e).__name__:
            return None
        raise


def _from_gcp(secret_id: str) -> str | None:
    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("SECRETS_BACKEND=gcp needs GCP_PROJECT")
    client = _clients.get("gcp")
    if client is None:
        from google.cloud import secretmanager  # optional dependency
        client = _clients["gcp"] = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    try:
        return client.access_secret_version(name=name).payload.data.decode("utf-8").strip() or None
    except Exception as e:
        if "NotFound" in type(e).__name__:
            return None
        raise


_BACKENDS = {"aws": _from_aws, "gcp": _from_gcp}


def _resolve(name: str) -> tuple[str | None, str]:
    if os.environ.get(f"{name}_FILE"):
        return _from_file(os.environ[f"{name}_FILE"]), f"{name}_FILE"
    backend = os.environ.get("SECRETS_BACKEND", "").strip().lower()
    if backend:
        fetch = _BACKENDS.get(backend)
        if fetch is None:
            log.error("Unknown SECRETS_BACKEND %r (use aws or gcp)", backend)
        else:
            try:
                value = fetch(os.environ.get("SECRETS_PREFIX", "") + name)
                if value:
                    return value, backend
            except Exception as e:  # manager unreachable: fall back, but say so
                log.error("Couldn't read %s from %s secrets manager: %s", name, backend, type(e).__name__)
    dirs = [os.environ["SECRETS_DIR"]] if os.environ.get("SECRETS_DIR") else list(DEFAULT_DIRS)
    for d in dirs:
        value = _from_file(Path(d) / name)
        if value:
            return value, "file"
    return (os.environ.get(name) or None), "env"


def get_secret(name: str, default: str | None = None) -> str | None:
    with _lock:
        if name not in _cache:
            value, source = _resolve(name)
            _cache[name] = value
            _sources[name] = source if value else "default"
            if value:
                _known_values.add(value)
                log.debug("secret %s loaded from %s", name, source)
        value = _cache[name]
    return value if value is not None else default


def source_of(name: str) -> str | None:
    """Where a loaded secret came from (env, file, aws, gcp, NAME_FILE or
    default): for startup diagnostics, never the value itself."""
    return _sources.get(name)


def export_to_env(*names: str) -> None:
    """Put secrets that libraries read from the environment (e.g. the OTel
    SDK's OTEL_EXPORTER_OTLP_HEADERS) into os.environ."""
    for name in names:
        value = get_secret(name)
        if value and not os.environ.get(name):
            os.environ[name] = value


def clear_cache() -> None:
    """Tests only: forget resolved values and clients."""
    with _lock:
        _cache.clear()
        _sources.clear()
        _known_values.clear()
        _clients.clear()


class RedactSecrets(logging.Filter):
    """Masks any loaded secret value (8+ chars) in log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        values = [v for v in _known_values if len(v) >= 8]
        if not values:
            return True
        msg = record.getMessage()
        masked = msg
        for v in values:
            masked = masked.replace(v, "***")
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def install_log_redaction(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger()
    if not any(isinstance(f, RedactSecrets) for f in target.filters):
        target.addFilter(RedactSecrets())
    for handler in target.handlers:
        if not any(isinstance(f, RedactSecrets) for f in handler.filters):
            handler.addFilter(RedactSecrets())
