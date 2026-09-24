"""
orchestrator.py
===============
Runs the multi-agent pipeline for one question and yields events as it goes.

    question + history
          │
    ┌─────▼──────┐  guardrails (deterministic) + LLM router, rules fallback
    │   Router   │  -> QueryPlan: intent, entities, standalone question
    └─────┬──────┘
          │  intent picks specialists (state.AGENTS_FOR_INTENT)
    ┌─────▼─────────────────────────────────────────────┐
    │ reviews │ stats │ catalog │ planner   (parallel)  │  tools only, no LLM
    └─────┬─────────────────────────────────────────────┘
          │  evidence, numbered [1..n]
    ┌─────▼──────┐
    │ Synthesizer│  streamed, cites [n]      (extractive fallback, no LLM)
    └─────┬──────┘
    ┌─────▼──────┐
    │  Verifier  │  citation check + LLM claim check -> drop unsupported
    └────────────┘

Why a hand-written state machine instead of LangGraph/CrewAI? The graph is
small and fixed (route -> fan-out -> synthesize -> verify); a framework
would add ~100MB of dependencies to a 512MB container and hide the
control flow this project is meant to demonstrate. Each node is a plain
function with typed inputs/outputs, so swapping to LangGraph later is
mechanical.

Events (also the SSE wire format, see routers/chat.py):
  plan          the QueryPlan
  agent         {agent, status: start|done, evidence, notes, ms}
  sources       numbered evidence list (citations resolve against this)
  token         {text}                   streamed answer text
  verification  {citations, claims}      verifier output
  revision      {answer}                 final answer if the verifier changed it
  done          {answer, trace, ...}     always last
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from ..guardrails import REFUSALS, redact_pii
from ..knowledge.corpus import registry
from ..tracing import current_trace, span
from .router import route
from .specialists import SPECIALISTS
from .state import AgentResult, Evidence, QueryPlan
from .synthesizer import extractive_answer, synthesize_stream
from .verifier import check_citations, revise, verify_claims

MAX_EVIDENCE = 16
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")

# Order evidence so computed facts come first (they anchor numbers), then
# catalog/prereq facts, then opinions.
_KIND_ORDER = {"stats": 0, "plan": 1, "prereq": 2, "catalog": 3, "review": 4, "reddit": 5}


def _canned_answer(plan: QueryPlan, question: str) -> str | None:
    """Answers that must not go through retrieval or an LLM."""
    if plan.refusal:
        return REFUSALS[plan.refusal]
    if plan.intent == "off_topic":
        greeting = len(question.split()) <= 3 and not question.strip().endswith("?")
        return REFUSALS["greeting" if greeting else "off_topic"]
    if plan.unknown_professors and not plan.professors:
        names = ", ".join(plan.unknown_professors)
        known = ", ".join(registry().professors)
        return (f"I don't have any reviews for {names}, so I can't say what students think. "
                f"I currently have reviews for: {known}.")
    return None


def _run_specialists(plan: QueryPlan, source_filter: str | None) -> Iterator[tuple[str, AgentResult, float]]:
    """Fan out in parallel; each thread runs in a copy of the current context
    so its spans land on this request's trace."""
    futures = {}
    for name in plan.agents:
        ctx = contextvars.copy_context()
        t0 = time.perf_counter()
        futures[name] = (_pool.submit(ctx.run, SPECIALISTS[name], plan, source_filter), t0)
    for name, (fut, t0) in futures.items():
        try:
            result = fut.result(timeout=20)
        except Exception as e:
            result = AgentResult(agent=name, error=f"{type(e).__name__}: {e}")
        yield name, result, (time.perf_counter() - t0) * 1000


def run(question: str, history: list[dict] | None = None,
        source_filter: str | None = None) -> Iterator[dict]:
    """The pipeline as an event stream. Must be called inside tracing.start_trace()."""
    history = history or []
    plan = route(question, history)
    yield {"type": "plan", "plan": plan.to_dict()}

    canned = _canned_answer(plan, question)
    if canned:
        yield {"type": "done", "answer": canned, "sources": [], "plan": plan.to_dict(),
               "verification": None, "mode": "canned"}
        return

    # -- specialists ------------------------------------------------------
    evidence: list[Evidence] = []
    notes: list[str] = []
    agent_data: dict = {}
    for name in plan.agents:
        yield {"type": "agent", "agent": name, "status": "start"}
    for name, result, ms in _run_specialists(plan, source_filter):
        evidence.extend(result.evidence)
        notes.extend(result.notes)
        if result.data:
            agent_data[name] = result.data
        yield {"type": "agent", "agent": name, "status": "done", "ms": round(ms),
               "evidence": len(result.evidence), "notes": result.notes, "error": result.error}
    if plan.unknown_professors:
        notes.append(f"No reviews exist for {', '.join(plan.unknown_professors)}; say so.")

    # Deduplicate identical chunks returned by two agents, order, cap, number.
    seen: set[str] = set()
    unique = []
    for e in sorted(evidence, key=lambda e: _KIND_ORDER.get(e.kind, 9)):
        key = e.chunk_id or e.label + e.text[:50]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    evidence = unique[:MAX_EVIDENCE]
    for i, e in enumerate(evidence, 1):
        e.n = i
    sources = [e.to_public() for e in evidence]
    yield {"type": "sources", "sources": sources, "agent_data": agent_data}

    if not any(e.kind in ("review", "reddit", "catalog", "stats", "prereq", "plan") for e in evidence):
        yield {"type": "done", "answer": REFUSALS["no_evidence"], "sources": sources,
               "plan": plan.to_dict(), "verification": None, "mode": "no_evidence",
               "agent_data": agent_data}
        return

    # -- synthesis --------------------------------------------------------
    mode = "llm"
    parts: list[str] = []
    try:
        for delta in synthesize_stream(plan, evidence, notes, history):
            parts.append(delta)
            yield {"type": "token", "text": delta}
        answer = "".join(parts).strip()
    except Exception as e:
        # No key, budget, every model down — or a failure mid-stream (e.g. a
        # read timeout after half the answer). A half answer isn't safe to
        # ship, so fall back to the extractive answer; if text was already
        # streamed, the `revision` event below replaces it in the client.
        mode = "extractive"
        answer = extractive_answer(plan, evidence, notes)
        with span("agent.synthesizer.fallback", reason=f"{type(e).__name__}: {e}"[:300],
                  partial_chars=sum(len(p) for p in parts)):
            pass
        if not parts:
            yield {"type": "token", "text": answer}
    streamed = "".join(parts).strip() if parts else answer

    # -- verification -----------------------------------------------------
    verification: dict = {"citations": check_citations(answer, len(evidence)), "claims": None}
    final = answer
    if mode == "llm" and settings.VERIFIER_ENABLED:
        with span("agent.verifier") as s:
            try:
                claims = verify_claims(answer, evidence)
                verification["claims"] = claims
                s.attributes["pass_rate"] = claims["pass_rate"]
                # Guard against a verifier that rejects everything (it's a
                # small model): only revise when most of the answer survives.
                if claims["unsupported"] and claims["pass_rate"] >= 0.5:
                    final = revise(answer, claims["unsupported"])
            except Exception as e:  # budget, invalid JSON, provider down
                verification["claims"] = {"method": "skipped", "error": f"{type(e).__name__}"}
                s.attributes["skipped"] = str(e)
    yield {"type": "verification", "verification": verification}

    final = redact_pii(final)
    if final != streamed:
        yield {"type": "revision", "answer": final}

    yield {"type": "done", "answer": final, "sources": sources, "plan": plan.to_dict(),
           "verification": verification, "mode": mode, "agent_data": agent_data}


def answer(question: str, history: list[dict] | None = None,
           source_filter: str | None = None) -> dict:
    """Run to completion and return the final `done` event (non-streaming callers)."""
    final: dict = {}
    for event in run(question, history, source_filter):
        if event["type"] == "done":
            final = event
    trace = current_trace()
    if trace is not None:
        final["trace"] = trace.to_dict()
    return final
