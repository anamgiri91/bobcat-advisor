"""
telemetry.py
============
OpenTelemetry export for traces and metrics.

Every request already records an in-process Trace (tracing.py). When
telemetry is enabled, each finished Trace is replayed as OTel spans (one
root span per request, one child per agent/LLM/retrieval step, with the
real start and end times) and summarised into metrics:

  bobcat.http.server.duration_ms   histogram   route, method, status_class
  bobcat.request.duration_ms       histogram   kind, intent, mode, error
  bobcat.agent.duration_ms         histogram   step (e.g. agent.router, llm.synthesizer)
  bobcat.requests                  counter     kind, mode
  bobcat.llm.calls                 counter     model, outcome (ok | fell_back | error)
  bobcat.llm.tokens                counter     model, type (prompt | completion | reasoning)
  bobcat.llm.cost_usd              counter     model

Configuration uses the standard OTel environment variables, so any backend
works (the bundled collector + Prometheus + Grafana + Jaeger stack in
observability/, Grafana Cloud, Honeycomb, Datadog...):

  OTEL_EXPORTER_OTLP_ENDPOINT   e.g. http://otel-collector:4318  (enables export)
  OTEL_EXPORTER_OTLP_PROTOCOL   http/protobuf (default) | grpc
  OTEL_EXPORTER_OTLP_HEADERS    e.g. authorization=Basic ...      (keep in a secret)
  OTEL_SERVICE_NAME             default bobcat-advisor-api

Cost is an estimate from token counts and LLM_PRICES (USD per million
input/output tokens per model), because providers don't return prices. It
counts hidden reasoning tokens as output, since they are billed.

Nothing here can fail a request: exporting happens after the response's
trace ends, in the SDK's background batch processors.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from .config import settings
from .tracing import Span, Trace, register_exporter

log = logging.getLogger("bobcat.telemetry")

_state: dict[str, Any] = {"enabled": False}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def prices() -> dict[str, tuple[float, float]]:
    """{model: (usd per 1M input tokens, usd per 1M output tokens)} from LLM_PRICES."""
    try:
        raw = json.loads(settings.LLM_PRICES or "{}")
        return {m: (float(p[0]), float(p[1])) for m, p in raw.items()}
    except (ValueError, TypeError, IndexError, AttributeError):
        log.warning("LLM_PRICES is not valid JSON of {model: [input, output]}; cost is 0")
        return {}


def span_cost(s: Span) -> float:
    model = s.attributes.get("model")
    if not model or not s.name.startswith("llm."):
        return 0.0
    p_in, p_out = prices().get(model, (0.0, 0.0))
    a = s.attributes
    out_tokens = a.get("completion_tokens", 0) + a.get("reasoning_tokens", 0)
    return (a.get("prompt_tokens", 0) * p_in + out_tokens * p_out) / 1_000_000


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return _state["enabled"]


def setup(tracer_provider=None, meter_provider=None) -> bool:
    """
    Enable export. With no providers given, builds OTLP exporters from the
    standard environment variables, and does nothing unless an endpoint is
    configured. Tests pass in-memory providers.
    """
    with _lock:
        if _state["enabled"]:
            return True
        if tracer_provider is None and meter_provider is None:
            if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or not settings.OTEL_ENABLED:
                return False
            tracer_provider, meter_provider = _otlp_providers()

        tracer = tracer_provider.get_tracer("bobcat-advisor")
        meter = meter_provider.get_meter("bobcat-advisor")
        _state.update(
            enabled=True, tracer=tracer, tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            http=meter.create_histogram("bobcat.http.server.duration_ms", unit="ms",
                                        description="HTTP request latency"),
            request=meter.create_histogram("bobcat.request.duration_ms", unit="ms",
                                           description="Pipeline latency per request"),
            step=meter.create_histogram("bobcat.agent.duration_ms", unit="ms",
                                        description="Latency per agent / LLM / retrieval step"),
            requests=meter.create_counter("bobcat.requests", description="Pipeline requests"),
            llm_calls=meter.create_counter("bobcat.llm.calls", description="LLM calls"),
            tokens=meter.create_counter("bobcat.llm.tokens", description="LLM tokens"),
            cost=meter.create_counter("bobcat.llm.cost_usd", description="Estimated LLM cost (USD)"),
        )
    register_exporter(export_trace)
    log.info("OpenTelemetry export enabled")
    return True


def _otlp_providers():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf") == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "bobcat-advisor-api"),
        "service.version": settings.APP_VERSION,
        "deployment.environment": settings.ENVIRONMENT,
    })
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
    return tracer_provider, MeterProvider(resource=resource, metric_readers=[reader])


def shutdown() -> None:
    """Flush and stop exporters (called on app shutdown)."""
    with _lock:
        if not _state["enabled"]:
            return
        for key in ("tracer_provider", "meter_provider"):
            provider = _state.get(key)
            if provider is not None and hasattr(provider, "shutdown"):
                try:
                    provider.shutdown()
                except Exception:
                    log.exception("telemetry shutdown failed")
        _state.clear()
        _state["enabled"] = False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_SAFE = (str, bool, int, float)


def _attrs(d: dict) -> dict:
    """OTel attributes must be primitives or homogeneous lists of them."""
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, _SAFE):
            out[k] = v
        elif isinstance(v, list | tuple) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
        else:
            out[k] = json.dumps(v, default=str)[:500]
    return out


def export_trace(trace: Trace) -> None:
    if not _state["enabled"]:
        return
    from opentelemetry import trace as ot
    from opentelemetry.trace import Status, StatusCode

    tracer = _state["tracer"]
    kind = str(trace.attributes.get("kind", "request"))
    start = trace.started_ns
    end = start + int((trace.duration_ms or 0) * 1e6)
    root_attrs = _attrs({**trace.attributes, "bobcat.trace_id": trace.trace_id,
                         "llm.tokens": trace.total_tokens, "llm.calls": trace.llm_calls,
                         "llm.cost_usd": trace.cost_usd})
    root = tracer.start_span(f"bobcat.{kind}", start_time=start, attributes=root_attrs)
    if trace.error:
        root.set_status(Status(StatusCode.ERROR, trace.error[:200]))
    ctx = ot.set_span_in_context(root)
    for s in trace.spans:
        s_start = start + int(s.start_ms * 1e6)
        child = tracer.start_span(s.name, context=ctx, start_time=s_start, attributes=_attrs(s.attributes))
        if s.error:
            child.set_status(Status(StatusCode.ERROR, s.error[:200]))
        child.end(end_time=s_start + int(s.duration_ms * 1e6))
    root.end(end_time=end)
    _record_metrics(trace, kind)


def _record_metrics(trace: Trace, kind: str) -> None:
    labels = {"kind": kind, "intent": str(trace.attributes.get("intent", "")),
              "mode": str(trace.attributes.get("mode", "")), "error": bool(trace.error)}
    _state["request"].record(trace.duration_ms or 0.0, labels)
    _state["requests"].add(1, {"kind": kind, "mode": labels["mode"]})
    for s in trace.spans:
        _state["step"].record(s.duration_ms, {"step": s.name})
        if not s.name.startswith("llm.") or s.name == "llm.rate_limit_wait":
            continue
        model = str(s.attributes.get("model", "unknown"))
        outcome = "error" if s.error else "fell_back" if s.attributes.get("fell_back") else "ok"
        _state["llm_calls"].add(1, {"model": model, "outcome": outcome})
        for t in ("prompt", "completion", "reasoning"):
            n = s.attributes.get(f"{t}_tokens", 0)
            if n:
                _state["tokens"].add(n, {"model": model, "type": t})
        cost = span_cost(s)
        if cost:
            _state["cost"].add(cost, {"model": model})


def record_http(route: str, method: str, status: int, duration_ms: float) -> None:
    if _state["enabled"]:
        _state["http"].record(duration_ms, {"route": route, "method": method,
                                            "status_class": f"{status // 100}xx"})
