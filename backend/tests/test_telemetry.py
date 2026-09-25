"""OpenTelemetry export: span hierarchy and timing, metrics, cost, failure
isolation, HTTP middleware, and the dashboard's metric names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry
from app.config import settings
from app.tracing import Span, register_exporter, span, start_trace, unregister_exporter

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def otel(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PRICES", json.dumps({"model-a": [1.0, 4.0]}))
    spans = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    assert telemetry.setup(tp, mp)
    yield spans, reader
    unregister_exporter(telemetry.export_trace)
    telemetry.shutdown()


def _metrics(reader) -> dict[str, list]:
    out: dict[str, list] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out[m.name] = list(m.data.data_points)
    return out


def _llm_trace():
    with start_trace(kind="chat", intent="course_info") as t:
        with span("agent.router"):
            pass
        with span("llm.synthesizer", model="model-a", prompt_tokens=1000, completion_tokens=200,
                  reasoning_tokens=300):
            pass
        t.attributes["mode"] = "llm"
    return t


# -- cost ----------------------------------------------------------------------

def test_cost_counts_reasoning_tokens_as_output(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PRICES", json.dumps({"model-a": [1.0, 4.0]}))
    s = Span("llm.synthesizer", 0, attributes={"model": "model-a", "prompt_tokens": 1_000_000,
                                                "completion_tokens": 500_000, "reasoning_tokens": 500_000})
    assert telemetry.span_cost(s) == pytest.approx(1.0 + 4.0)
    assert telemetry.span_cost(Span("agent.router", 0, attributes={"model": "model-a"})) == 0
    assert telemetry.span_cost(Span("llm.x", 0, attributes={"model": "unpriced", "prompt_tokens": 9})) == 0


def test_bad_price_config_means_zero_cost(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PRICES", "not json")
    assert telemetry.prices() == {}


def test_trace_reports_cost(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PRICES", json.dumps({"model-a": [1.0, 4.0]}))
    t = _llm_trace()
    assert t.to_dict()["cost_usd"] == pytest.approx((1000 * 1 + 500 * 4) / 1e6)


# -- setup ---------------------------------------------------------------------

def test_disabled_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.setup() is False and not telemetry.enabled()
    telemetry.export_trace(_llm_trace())            # no-op, no error


def test_otlp_providers_build_from_env(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    for protocol in ("http/protobuf", "grpc"):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
        tp, mp = telemetry._otlp_providers()
        assert tp.resource.attributes["service.name"] == "bobcat-advisor-api"
        tp.shutdown()
        mp.shutdown(timeout_millis=100)


# -- export --------------------------------------------------------------------

def test_trace_becomes_a_span_tree(otel):
    spans, _ = otel
    t = _llm_trace()
    finished = {s.name: s for s in spans.get_finished_spans()}
    root = finished["bobcat.chat"]
    assert {"agent.router", "llm.synthesizer"} <= set(finished)
    for name in ("agent.router", "llm.synthesizer"):
        child = finished[name]
        assert child.parent.span_id == root.context.span_id
        assert root.start_time <= child.start_time <= child.end_time <= root.end_time
    assert root.attributes["intent"] == "course_info" and root.attributes["mode"] == "llm"
    assert root.attributes["llm.tokens"] == 1500 and root.attributes["bobcat.trace_id"] == t.trace_id
    assert finished["llm.synthesizer"].attributes["model"] == "model-a"


def test_errors_are_marked(otel):
    spans, _ = otel
    with pytest.raises(ValueError), start_trace(kind="advise"):
        with span("agent.researcher"):
            raise ValueError("boom")
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert not finished["bobcat.advise"].status.is_ok
    assert not finished["agent.researcher"].status.is_ok


def test_metrics(otel):
    _, reader = otel
    _llm_trace()
    m = _metrics(reader)
    tokens = {p.attributes["type"]: p.value for p in m["bobcat.llm.tokens"]}
    assert tokens == {"prompt": 1000, "completion": 200, "reasoning": 300}
    assert m["bobcat.llm.cost_usd"][0].value == pytest.approx(0.003)
    assert m["bobcat.llm.calls"][0].attributes == {"model": "model-a", "outcome": "ok"}
    assert m["bobcat.request.duration_ms"][0].attributes["kind"] == "chat"
    assert {p.attributes["step"] for p in m["bobcat.agent.duration_ms"]} == {"agent.router", "llm.synthesizer"}


def test_complex_attributes_are_serialised(otel):
    spans, _ = otel
    with start_trace(kind="chat", courses=["CS3358"], plan={"a": 1}):
        pass
    root = spans.get_finished_spans()[-1]
    assert root.attributes["courses"] == ("CS3358",) and root.attributes["plan"] == '{"a": 1}'


def test_a_failing_exporter_never_breaks_a_request():
    def broken(_trace):
        raise RuntimeError("collector down")
    register_exporter(broken)
    try:
        with start_trace(kind="chat") as t:
            pass
        assert t.duration_ms is not None
    finally:
        unregister_exporter(broken)


def test_http_middleware_records_route_templates(otel, client):
    _, reader = otel
    r = client.get("/api/courses/CS3358")
    assert r.headers["X-Request-ID"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/api/health/live", headers={"x-request-id": "abc"}).headers["X-Request-ID"] == "abc"
    routes = {(p.attributes["route"], p.attributes["status_class"]) for p in _metrics(reader)["bobcat.http.server.duration_ms"]}
    assert ("/api/courses/{code}", "2xx") in routes


def test_chat_requests_are_exported_with_intent(otel, client):
    spans, _ = otel
    client.post("/api/chat/ask", json={"question": "What are the prerequisites for CS3360?"})
    root = next(s for s in spans.get_finished_spans() if s.name == "bobcat.chat")
    assert root.attributes["intent"] == "prereq" and root.attributes["mode"] == "extractive"


# -- dashboard ---------------------------------------------------------------------

def test_dashboard_queries_only_metrics_the_app_emits():
    """Every Prometheus metric the Grafana dashboard queries must exist (names
    as the collector exports them, with add_metric_suffixes disabled)."""
    import re
    dash = json.loads((REPO / "observability/grafana/dashboards/bobcat-advisor.json").read_text())
    emitted = {"bobcat_http_server_duration_ms", "bobcat_request_duration_ms",
               "bobcat_agent_duration_ms", "bobcat_requests", "bobcat_llm_calls",
               "bobcat_llm_tokens", "bobcat_llm_cost_usd"}
    exprs = [t["expr"] for p in dash["panels"] for t in p.get("targets", [])]
    assert len(exprs) >= 8
    used = {re.sub(r"_(bucket|sum|count)$", "", m) for e in exprs for m in re.findall(r"\bbobcat_[a-z_]+", e)}
    assert used and used <= emitted, used - emitted


def test_observability_configs_are_consistent():
    yaml = pytest.importorskip("yaml")
    obs = REPO / "observability"
    collector = yaml.safe_load((obs / "otel-collector.yaml").read_text())
    assert collector["exporters"]["prometheus"]["add_metric_suffixes"] is False   # dashboard relies on it
    assert set(collector["service"]["pipelines"]) == {"traces", "metrics"}
    prom = yaml.safe_load((obs / "prometheus.yml").read_text())
    target = prom["scrape_configs"][0]["static_configs"][0]["targets"][0]
    assert target.endswith(collector["exporters"]["prometheus"]["endpoint"].split(":")[-1])
    compose = yaml.safe_load((obs / "docker-compose.yml").read_text())
    assert {"otel-collector", "jaeger", "prometheus", "grafana"} <= set(compose["services"])
    endpoint = compose["services"]["backend"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert endpoint.endswith(":4318")                      # collector's OTLP/HTTP port, the default protocol
    ds = yaml.safe_load((obs / "grafana/provisioning/datasources/datasources.yaml").read_text())
    assert {d["uid"] for d in ds["datasources"]} >= {"prometheus"}
