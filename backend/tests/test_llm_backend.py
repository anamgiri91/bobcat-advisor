"""OpenAI-compatible HTTP backend: parsing, errors, streaming, retry-delay formats."""

import json

import httpx
import pytest

from app import llm


def _backend(handler) -> llm.OpenAICompatBackend:
    b = llm.OpenAICompatBackend("https://example.test/v1", "key", timeout_s=5)
    b._client = httpx.Client(base_url="https://example.test/v1/", transport=httpx.MockTransport(handler))
    return b


def test_complete_parses_content_and_usage():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": ' {"ok": true} '}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 40}})

    text, usage = _backend(handler).complete("m", [{"role": "user", "content": "x"}], 50, 0.0, True,
                                             reasoning_effort="minimal")
    assert text == '{"ok": true}'
    assert usage["prompt_tokens"] == 12 and usage["completion_tokens"] == 3
    assert usage["reasoning_tokens"] == 25  # hidden thinking: total - prompt - completion
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["reasoning_effort"] == "minimal"
    assert seen["body"]["model"] == "m"


def test_gemini_list_shaped_error_is_parsed():
    def handler(req):
        return httpx.Response(404, json=[{"error": {
            "code": 404, "message": "This model models/gemini-2.5-flash is no longer available"}}])

    with pytest.raises(llm.LLMHTTPError) as e:
        _backend(handler).complete("gemini-2.5-flash", [], 5, 0.0, False)
    assert e.value.status_code == 404 and "no longer available" in str(e.value)
    assert llm._is_fallback_error(e.value)


def test_overload_and_timeout_are_fallback_errors():
    assert llm._is_fallback_error(llm.LLMHTTPError(503, "high demand"))
    assert llm._is_fallback_error(llm.LLMHTTPError(408, "timed out"))
    assert not llm._is_fallback_error(llm.LLMHTTPError(401, "bad key"))


def test_stream_yields_deltas_until_done():
    sse = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    b = _backend(lambda req: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}))
    assert "".join(b.stream("m", [], 10, 0.0)) == "Hello"


def test_stream_error_status_raises():
    b = _backend(lambda req: httpx.Response(429, json={"error": {"message": "Please retry in 3s"}}))
    with pytest.raises(llm.LLMHTTPError) as e:
        list(b.stream("m", [], 10, 0.0))
    assert llm._rate_limit_wait(e.value) == 3.0


@pytest.mark.parametrize("msg,expected", [
    ("Please try again in 1.695s.", 1.695),          # Groq
    ("Please try again in 1m2.5s.", 62.5),
    ("Please retry in 12.5s.", 12.5),                # Gemini message
    ('details: [{"retryDelay": "7s"}]', 7.0),        # Gemini RetryInfo
    ("quota exceeded", 2.0),                         # no hint
])
def test_rate_limit_wait_formats(msg, expected):
    assert llm._rate_limit_wait(llm.LLMHTTPError(429, msg)) == pytest.approx(expected)
