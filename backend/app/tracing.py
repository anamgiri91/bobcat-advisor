"""
tracing.py
==========
Lightweight per-request tracing for the agent pipeline.

Every request gets a Trace; every agent step, retrieval call, and LLM call
records a Span with its duration, token usage, and any attributes worth
debugging later. The finished trace is stored on the assistant Message row
(messages.trace, JSONB) so /api/analytics can report per-agent latency,
token cost, and verifier pass rate straight from Postgres.

Why not OpenTelemetry/Langfuse directly? Both are good exporters, but the
free-tier deployment has no collector to send to. The span shape here
mirrors OTel (name, start, duration, attributes), so adding an exporter is
a one-function change in Trace.finish() rather than a rewrite.

The current trace lives in a ContextVar, so deep helpers (llm.chat) can
attach spans without the trace being threaded through every signature.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("bobcat.tracing")


@dataclass
class Span:
    name: str
    start_ms: float
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start_ms": round(self.start_ms, 1),
            "duration_ms": round(self.duration_ms, 1),
            "attributes": self.attributes,
            "error": self.error,
        }


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    spans: list[Span] = field(default_factory=list)
    # Request-level facts for exporters: kind (chat/advise/whatif), intent, mode...
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_ns: int = field(default_factory=time.time_ns)     # wall clock, for exporters
    _t0: float = field(default_factory=time.perf_counter)
    duration_ms: float | None = None                          # set when the trace ends

    @property
    def elapsed_ms(self) -> float:
        if self.duration_ms is not None:
            return self.duration_ms
        return (time.perf_counter() - self._t0) * 1000

    @property
    def total_tokens(self) -> int:
        # Includes hidden reasoning tokens: they're billed and rate-limited too.
        return sum(
            s.attributes.get("prompt_tokens", 0) + s.attributes.get("completion_tokens", 0)
            + s.attributes.get("reasoning_tokens", 0)
            for s in self.spans
        )

    @property
    def llm_calls(self) -> int:
        return sum(1 for s in self.spans
                   if s.name.startswith("llm.") and s.name != "llm.rate_limit_wait")

    @property
    def cost_usd(self) -> float:
        """Estimated LLM cost from token counts and the configured prices (LLM_PRICES)."""
        from .telemetry import span_cost
        return round(sum(span_cost(s) for s in self.spans), 6)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_ms": round(self.elapsed_ms, 1),
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "cost_usd": self.cost_usd,
            "spans": [s.to_dict() for s in self.spans],
        }


_current: ContextVar[Trace | None] = ContextVar("current_trace", default=None)


def current_trace() -> Trace | None:
    return _current.get()


# Called with each finished trace (e.g. the OpenTelemetry exporter in
# telemetry.py). Exporter failures are logged and never reach the request.
_exporters: list[Callable[[Trace], None]] = []


def register_exporter(fn: Callable[[Trace], None]) -> None:
    if fn not in _exporters:
        _exporters.append(fn)


def unregister_exporter(fn: Callable[[Trace], None]) -> None:
    if fn in _exporters:
        _exporters.remove(fn)


@contextmanager
def start_trace(**attributes: Any) -> Iterator[Trace]:
    trace = Trace(attributes=dict(attributes))
    token = _current.set(trace)
    try:
        yield trace
    except Exception as e:
        trace.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        _current.reset(token)
        trace.duration_ms = (time.perf_counter() - trace._t0) * 1000
        for export in list(_exporters):
            try:
                export(trace)
            except Exception:  # observability must never break a request
                log.exception("trace exporter failed")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """
    Time a block and record it on the current trace. Works (as a no-op
    recorder) when there is no active trace, e.g. in offline eval scripts.
    """
    trace = _current.get()
    start = trace.elapsed_ms if trace else 0.0
    s = Span(name=name, start_ms=start, attributes=dict(attributes))
    t0 = time.perf_counter()
    try:
        yield s
    except Exception as e:
        s.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        s.duration_ms = (time.perf_counter() - t0) * 1000
        if trace is not None:
            trace.spans.append(s)
