"""
llm.py
======
The single entry point for every LLM call in the app.

Centralising calls here gives us, in one place:
  - one pooled HTTP client for an OpenAI-compatible chat API — Gemini and
    Groq both expose one, so the provider is configuration (settings.LLM_*)
    and there is no vendor SDK to pin
  - model fallback on 404 (removed / no access), 429 (rate limited), 5xx and
    timeouts (overloaded), and JSON-validation failures
  - one wait-and-retry when every model is rate limited
  - JSON-mode calls with a parse-and-retry for structured agent outputs
  - streaming for the synthesizer
  - a per-request budget (max calls / tokens) so a multi-agent plan can't
    run away with a rate-limited key
  - a tracing span per call with model, latency and token usage

Tests and offline evals swap the backend with set_backend(FakeBackend(...)),
so the whole agent graph is testable without network access or an API key.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Protocol

import httpx

from .config import settings
from .tracing import current_trace, span


class LLMUnavailable(RuntimeError):
    """No API key configured, or every model in the fallback chain failed."""


class BudgetExceeded(RuntimeError):
    """This request already used its allotted LLM calls or tokens."""


class LLMHTTPError(RuntimeError):
    """Non-2xx from the provider. status_code drives fallback/retry decisions."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class Backend(Protocol):
    def complete(
        self, model: str, messages: list[dict], max_tokens: int,
        temperature: float, json_mode: bool, reasoning_effort: str | None = None,
        timeout_s: float | None = None,
    ) -> tuple[str, dict]: ...

    def stream(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float,
        reasoning_effort: str | None = None,
    ) -> Iterator[str]: ...


# Groq only accepts reasoning_effort on reasoning models; Gemini accepts it
# on every model it serves.
_GROQ_REASONING_PREFIXES = ("openai/gpt-oss", "qwen/qwen3")


def _reasoning_fields(model: str, effort: str | None) -> dict:
    if not effort:
        return {}
    if settings.LLM_PROVIDER == "groq" and not model.startswith(_GROQ_REASONING_PREFIXES):
        return {}
    return {"reasoning_effort": effort}


def _error_message(resp: httpx.Response) -> str:
    """Both providers nest the message under "error"; Gemini wraps it in a list."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(body, list) and body:
        body = body[0]
    err = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(err, dict):
        details = json.dumps(err.get("details", ""))[:300]
        return f"{err.get('message', '')} {err.get('code', '')} {details}".strip()
    return str(err)[:500]


class OpenAICompatBackend:
    """Chat completions over plain HTTP against any OpenAI-compatible endpoint."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    def _body(self, model, messages, max_tokens, temperature, effort, **extra) -> dict:
        return {"model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, **_reasoning_fields(model, effort), **extra}

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise LLMHTTPError(resp.status_code, _error_message(resp))

    def list_models(self) -> list[str]:
        resp = self._client.get("models")
        self._raise_for_status(resp)
        return [m["id"].removeprefix("models/") for m in resp.json().get("data", [])]

    def complete(self, model, messages, max_tokens, temperature, json_mode, reasoning_effort=None,
                 timeout_s=None):
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        body = self._body(model, messages, max_tokens, temperature, reasoning_effort, **extra)
        timeout = httpx.Timeout(timeout_s, connect=10.0) if timeout_s else httpx.USE_CLIENT_DEFAULT
        try:
            resp = self._client.post("chat/completions", json=body, timeout=timeout)
        except httpx.TimeoutException as e:
            raise LLMHTTPError(408, f"timed out after {timeout_s or settings.LLM_TIMEOUT_S}s") from e
        self._raise_for_status(resp)
        data = resp.json()
        usage = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        prompt = usage.get("prompt_tokens", 0) or 0
        completion = usage.get("completion_tokens", 0) or 0
        # Hidden thinking tokens are billed (Gemini reports them only in the
        # total) and count against max_tokens: a "length" finish with little
        # visible text means the model thought itself out of budget.
        reasoning = max(0, (usage.get("total_tokens") or 0) - prompt - completion)
        return content.strip(), {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "finish_reason": choice.get("finish_reason"),
        }

    def stream(self, model, messages, max_tokens, temperature, reasoning_effort=None):
        body = self._body(model, messages, max_tokens, temperature, reasoning_effort, stream=True)
        try:
            with self._client.stream("POST", "chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_status(resp)
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        yield delta
        except httpx.TimeoutException as e:
            raise LLMHTTPError(408, f"timed out after {settings.LLM_TIMEOUT_S}s") from e


_backend: Backend | None = None


def set_backend(backend: Backend | None) -> None:
    """Override the backend (tests / evals). None resets to the default."""
    global _backend
    _backend = backend


def _get_backend() -> Backend:
    global _backend
    if _backend is None:
        if not settings.LLM_API_KEY:
            key = "GEMINI_API_KEY" if settings.LLM_PROVIDER == "gemini" else "GROQ_API_KEY"
            raise LLMUnavailable(f"{key} is not set (LLM_PROVIDER={settings.LLM_PROVIDER}). "
                                 "Add it to backend/.env.")
        _backend = OpenAICompatBackend(settings.LLM_BASE_URL, settings.LLM_API_KEY,
                                       settings.LLM_TIMEOUT_S)
    return _backend


def is_available() -> bool:
    try:
        _get_backend()
        return True
    except LLMUnavailable:
        return False


_RETRYABLE_STATUS = {404, 408, 429, 500, 502, 503, 504}


def _is_fallback_error(e: Exception) -> bool:
    """
    Per-model failures worth retrying on the next model:
      404      model removed, or this key has no access to it
      408/5xx  timeout / overloaded (Gemini returns 503 "high demand")
      429      rate limited (limits are per model on both providers)
      400 json_validate_failed  the model couldn't produce valid JSON (e.g.
               a reasoning model that spent its token budget thinking)
    """
    status = getattr(e, "status_code", None)
    if status in _RETRYABLE_STATUS or type(e).__name__ in ("NotFoundError", "RateLimitError"):
        return True
    return status == 400 and "json_validate_failed" in str(e)


def _rate_limit_wait(e: Exception) -> float | None:
    """
    Seconds the provider asks us to wait on a 429, if any. Groq says
    "try again in 1.695s"; Gemini says "retry in 12.5s" and/or sends
    RetryInfo {"retryDelay": "12s"}.
    """
    if getattr(e, "status_code", None) != 429 and type(e).__name__ != "RateLimitError":
        return None
    text = str(e)
    m = re.search(r"(?:try again|retry) in (?:(\d+)m)?([\d.]+)(ms|s)", text)
    if m:
        seconds = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return seconds + 60 * int(m.group(1) or 0)
    m = re.search(r'retryDelay\\?"?:\s*\\?"([\d.]+)s', text)
    if m:
        return float(m.group(1))
    return 2.0


def _wait_before_retry(errors: list[Exception]) -> float | None:
    """
    If every model in the chain was rate limited, how long to wait before one
    retry of the chain — or None if we shouldn't (any other error, or a wait
    longer than LLM_MAX_RATE_LIMIT_WAIT_S: a user shouldn't sit through a
    minute-long stall; the degraded extractive answer is better).
    """
    waits = [_rate_limit_wait(e) for e in errors]
    if not waits or any(w is None for w in waits):
        return None
    wait = min(waits) + 0.25
    return wait if wait <= settings.LLM_MAX_RATE_LIMIT_WAIT_S else None


def configured_models() -> list[str]:
    return sorted({settings.LLM_MODEL, settings.LLM_FALLBACK_MODEL, settings.LLM_FAST_MODEL})


_available: list[str] | None = None


def missing_models() -> list[str] | None:
    """
    Configured models absent from the key's model list (None if unchecked).
    Cheap, but necessary-not-sufficient: Gemini lists gemini-2.5-* for keys
    that get 404 "no longer available to new users". probe_models() is the
    authoritative check.
    """
    global _available
    try:
        backend = _get_backend()
    except LLMUnavailable:
        return None
    if not hasattr(backend, "list_models"):
        return None
    if _available is None:
        try:
            _available = backend.list_models()
        except Exception:
            return None
    return [m for m in configured_models() if m not in _available]


def probe_models() -> dict[str, str]:
    """One tiny real call per configured model: "ok" or the error. Costs quota."""
    backend = _get_backend()
    out = {}
    for m in configured_models():
        try:
            backend.complete(m, [{"role": "user", "content": "Reply with OK."}], 64, 0.0, False,
                             reasoning_effort=settings.LLM_FAST_REASONING_EFFORT)
            out[m] = "ok"
        except Exception as e:
            out[m] = str(e)[:200]
    return out


def _check_budget() -> None:
    trace = current_trace()
    if trace is None:
        return
    if trace.llm_calls >= settings.MAX_LLM_CALLS_PER_REQUEST:
        raise BudgetExceeded(f"LLM call budget ({settings.MAX_LLM_CALLS_PER_REQUEST}) used")
    if trace.total_tokens >= settings.MAX_TOKENS_PER_REQUEST:
        raise BudgetExceeded(f"token budget ({settings.MAX_TOKENS_PER_REQUEST}) used")


def _model_chain(model: str) -> list[str]:
    chain = [model]
    if settings.LLM_FALLBACK_MODEL not in chain:
        chain.append(settings.LLM_FALLBACK_MODEL)
    return chain


def chat(
    messages: list[dict],
    *,
    agent: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    fast: bool = False,
    timeout_s: float | None = None,
    fallback: bool = True,
) -> str:
    """
    One completion, with fallback, budget check and a tracing span.

    fast=True     fast-path reasoning effort (router, verifier, taggers)
    timeout_s     per-call deadline, overriding LLM_TIMEOUT_S
    fallback      False = don't try LLM_FALLBACK_MODEL. For calls whose
                  caller has a deterministic fallback (rules router, skipping
                  verification), a second slow model is worse than giving up.
    """
    effort = settings.LLM_FAST_REASONING_EFFORT if fast else settings.LLM_REASONING_EFFORT
    _check_budget()
    backend = _get_backend()
    chain = _model_chain(model or settings.LLM_MODEL) if fallback else [model or settings.LLM_MODEL]

    errors: list[Exception] = []
    for attempt in range(2):
        errors = []
        for m in chain:
            with span(f"llm.{agent}", model=m, json_mode=json_mode, attempt=attempt,
                      reasoning_effort=effort or None) as s:
                try:
                    kwargs = {"reasoning_effort": effort}
                    if timeout_s:
                        kwargs["timeout_s"] = timeout_s
                    text, usage = backend.complete(m, messages, max_tokens, temperature, json_mode,
                                                   **kwargs)
                    s.attributes.update(usage)
                    return text
                except Exception as e:
                    if not _is_fallback_error(e):
                        raise
                    s.attributes["fell_back"] = True
                    s.attributes["error"] = str(e)[:300]
                    errors.append(e)
        wait = _wait_before_retry(errors) if attempt == 0 else None
        if wait is None:
            break
        with span("llm.rate_limit_wait", seconds=round(wait, 2)):
            time.sleep(wait)
    raise LLMUnavailable(f"All models failed: {errors[-1] if errors else 'unknown'}")


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose or code fences despite JSON mode.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def chat_json(messages: list[dict], *, agent: str, model: str | None = None,
              max_tokens: int = 512, fast: bool = True, timeout_s: float | None = None,
              fallback: bool = True) -> dict:
    """Completion parsed as a JSON object. Retries once on invalid JSON."""
    opts = dict(agent=agent, model=model, max_tokens=max_tokens, temperature=0.0,
                json_mode=True, fast=fast, timeout_s=timeout_s, fallback=fallback)
    text = chat(messages, **opts)
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        repair = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."},
        ]
        text = chat(repair, **{**opts, "agent": f"{agent}.repair"})
        return _extract_json(text)


def stream(
    messages: list[dict],
    *,
    agent: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> Iterator[str]:
    """
    Stream tokens. Falls back to the next model only if the failure happens
    before the first token (after that, the client has already seen text).
    Token usage isn't reported on streams, so it's estimated (~4 chars
    per token) to keep the budget and analytics roughly honest.
    """
    _check_budget()
    backend = _get_backend()
    chain = _model_chain(model or settings.LLM_MODEL)
    prompt_chars = sum(len(m["content"]) for m in messages)
    effort = settings.LLM_REASONING_EFFORT

    errors: list[Exception] = []
    for attempt in range(2):
        errors = []
        for m in chain:
            with span(f"llm.{agent}", model=m, streamed=True, attempt=attempt,
                      reasoning_effort=effort or None) as s:
                emitted = 0
                try:
                    for delta in backend.stream(m, messages, max_tokens, temperature,
                                                reasoning_effort=effort):
                        emitted += len(delta)
                        yield delta
                    s.attributes.update(
                        prompt_tokens=prompt_chars // 4, completion_tokens=emitted // 4,
                        estimated_tokens=True,
                    )
                    return
                except Exception as e:
                    if emitted or not _is_fallback_error(e):
                        raise
                    s.attributes["fell_back"] = True
                    s.attributes["error"] = str(e)[:300]
                    errors.append(e)
        wait = _wait_before_retry(errors) if attempt == 0 else None
        if wait is None:
            break
        with span("llm.rate_limit_wait", seconds=round(wait, 2)):
            time.sleep(wait)
    raise LLMUnavailable(f"All models failed: {errors[-1] if errors else 'unknown'}")
