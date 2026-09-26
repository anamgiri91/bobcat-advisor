"""
pipeline.py
===========
The course-recommendation pipeline: seven agents, one event stream.

    student form
         │
    ┌────▼─────┐  normalise courses / year / load, raise advisor flags
    │  Intake  │
    └────┬─────┘
    ┌────▼─────┐  browses the live TXST catalog: program page, four-year
    │Researcher│  plan, course pages          (tools: sandboxed browser,
    └────┬─────┘                              parsers, LLM extraction fallback)
    ┌────▼─────┐  every fact: official source? quoted verbatim on the page?
    │Fact-check│  agrees with the catalog snapshot? right catalog year?
    └────┬─────┘  -> verified / conflict / dropped
    ┌────▼─────┐
    │ Auditor  │  requirements done / in progress / remaining, hours left
    └────┬─────┘
    ┌────▼─────┐  eligibility (both sources), priority, interests,
    │Scheduler │  workload balance, multi-term roadmap
    └────┬─────┘
    ┌────▼─────┐  clash-free weekly timetables from the class schedule
    │Timetable │  (OR-Tools CP-SAT; busy times, preferred days, modality)
    └────┬─────┘
    ┌────▼─────┐
    │ Advisor  │  streamed, cited advising memo (template fallback)
    └────┬─────┘
    ┌────▼─────┐
    │ Verifier │  claim-level check of the memo, unsupported lines removed
    └──────────┘

Only the researcher's extraction fallback, the advisor and the verifier
use an LLM; everything that decides (what's required, what's eligible,
what to take) is deterministic and unit-tested.

Events (SSE wire format, see routers/advise.py):
  agent         {agent, status: start|done, ms, ...summary}
  profile       normalised profile + intake flags
  browse        one page visit {url, ok, status, ms, cached, error}
  research      what the researcher found (program, counts, errors)
  factcheck     the fact-check report
  audit         the degree audit
  schedule      the recommended schedule + roadmap
  timetable     timetable options for the recommended courses (if sections are loaded)
  sources       numbered evidence the memo cites
  token         streamed memo text
  verification  verifier output
  revision      final memo if the verifier changed it
  done          everything above, always last
"""

from __future__ import annotations

import contextvars
import queue
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from ..agents.verifier import check_citations, revise, verify_claims
from ..config import settings
from ..guardrails import redact_pii
from ..structured.schedule import terms as schedule_terms
from ..structured.timetable import Preferences, TimetableResult, build_timetable
from ..tracing import current_trace, span
from .advisor import advise_stream, build_evidence, extractive_memo
from .audit import audit as run_audit
from .factcheck import fact_check
from .profile import build_profile
from .research import ResearchResult, research
from .scheduler import plan_schedule
from .web import Browser

AGENTS = ["intake", "researcher", "fact_checker", "auditor", "scheduler", "timetable", "advisor",
          "verifier"]
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="advise")


def _research_live(profile, browser: Browser, events: queue.Queue) -> Iterator[dict]:
    """Run the researcher in a worker thread, yielding page visits as they happen."""
    ctx = contextvars.copy_context()
    future = _pool.submit(ctx.run, research, profile, browser)
    deadline = time.monotonic() + settings.WEB_TIMEOUT_S * (settings.WEB_MAX_PAGES + 2)
    while not future.done() and time.monotonic() < deadline:
        try:
            yield events.get(timeout=0.1)
        except queue.Empty:
            pass
    while not events.empty():
        yield events.get_nowait()
    try:
        result = future.result(timeout=max(0.0, deadline - time.monotonic()))
    except Exception as e:
        result = ResearchResult(errors=[f"Research failed: {type(e).__name__}: {e}"])
    yield {"type": "_result", "result": result}


def run(raw: dict) -> Iterator[dict]:
    """The pipeline as an event stream. Must be called inside tracing.start_trace()."""
    t = time.perf_counter()

    def elapsed() -> int:
        nonlocal t
        ms, t = (time.perf_counter() - t) * 1000, time.perf_counter()
        return round(ms)

    # -- intake -----------------------------------------------------------
    yield {"type": "agent", "agent": "intake", "status": "start"}
    with span("agent.intake"):
        profile, flags = build_profile(raw)
    yield {"type": "profile", "profile": profile.to_dict(), "flags": flags}
    yield {"type": "agent", "agent": "intake", "status": "done", "ms": elapsed(),
           "summary": f"{profile.year}, {len(profile.completed)} courses completed"}

    # -- web research -----------------------------------------------------
    yield {"type": "agent", "agent": "researcher", "status": "start"}
    events: queue.Queue = queue.Queue()
    browser = Browser(on_visit=lambda v: events.put({"type": "browse", **v.to_dict()}))
    found = ResearchResult()
    for ev in _research_live(profile, browser, events):
        if ev["type"] == "_result":
            found = ev["result"]
        else:
            yield ev
    yield {"type": "research", "program": asdict(found.program) if found.program else None,
           "method": found.method,
           "requirements": len(found.requirements), "pools": len(found.pools),
           "sequence_terms": len(found.sequence), "courses": sorted(found.courses),
           "errors": found.errors, "notes": found.notes}
    yield {"type": "agent", "agent": "researcher", "status": "done", "ms": elapsed(),
           "summary": f"{len(browser.visits)} pages, {len(found.requirements)} requirements",
           "error": "; ".join(found.errors[:2]) or None}

    # -- fact check -------------------------------------------------------
    yield {"type": "agent", "agent": "fact_checker", "status": "start"}
    report, verified = fact_check(found, profile, browser.pages)
    report_d = report.to_dict()
    yield {"type": "factcheck", **report_d}
    s = report.summary
    yield {"type": "agent", "agent": "fact_checker", "status": "done", "ms": elapsed(),
           "summary": f"{s['verified']} verified, {s['conflicts']} conflicts, "
                      f"{s['unverified']} dropped"}

    # -- audit ------------------------------------------------------------
    yield {"type": "agent", "agent": "auditor", "status": "start"}
    degree_audit = run_audit(profile, verified, report)
    audit_d = degree_audit.to_dict()
    yield {"type": "audit", "audit": audit_d}
    yield {"type": "agent", "agent": "auditor", "status": "done", "ms": elapsed(),
           "summary": (f"{len(degree_audit.remaining)} requirements remaining"
                       if degree_audit.available else "requirements unavailable")}

    # -- schedule ---------------------------------------------------------
    yield {"type": "agent", "agent": "scheduler", "status": "start"}
    plan = plan_schedule(profile, verified, degree_audit, report)
    plan_d = plan.to_dict()
    yield {"type": "schedule", "schedule": plan_d}
    yield {"type": "agent", "agent": "scheduler", "status": "done", "ms": elapsed(),
           "summary": f"{len(plan.courses)} courses, {plan.total_hours} hrs"}

    # -- timetable --------------------------------------------------------
    timetable: TimetableResult | None = None
    timetable_d = None
    yield {"type": "agent", "agent": "timetable", "status": "start"}
    if plan.courses and profile.planned_term in schedule_terms():
        prefs = Preferences.from_raw({
            "preferred_days": profile.preferred_days, "earliest_start": profile.earliest_start,
            "latest_end": profile.latest_end, "busy": profile.busy, "modality": profile.modality})
        timetable = build_timetable([x for c in plan.courses for x in (c.code, *c.bundle)],
                                    profile.planned_term, prefs)
        timetable_d = timetable.to_dict()
        yield {"type": "timetable", "timetable": timetable_d, "preferences": prefs.describe()}
        summary = (f"{len(timetable.options)} option(s)" if timetable.options else "no clash-free option")
    else:
        summary = f"no section data for {profile.planned_term}"
    yield {"type": "agent", "agent": "timetable", "status": "done", "ms": elapsed(), "summary": summary}

    # -- advisor ----------------------------------------------------------
    evidence = build_evidence(profile, flags, verified, report, degree_audit, plan, timetable)
    sources = [e.to_public() for e in evidence]
    yield {"type": "sources", "sources": sources}
    yield {"type": "agent", "agent": "advisor", "status": "start"}
    mode, parts = "llm", []
    try:
        for delta in advise_stream(profile, evidence):
            parts.append(delta)
            yield {"type": "token", "text": delta}
        memo = "".join(parts).strip()
        if not memo:
            raise ValueError("empty memo")
    except Exception as e:  # no key, budget, provider down, mid-stream failure
        mode = "extractive"
        memo = extractive_memo(profile, flags, evidence, degree_audit, plan)
        with span("agent.advisor.fallback", reason=f"{type(e).__name__}: {e}"[:300]):
            pass
        if not parts:
            yield {"type": "token", "text": memo}
    streamed = "".join(parts).strip() if parts else memo
    yield {"type": "agent", "agent": "advisor", "status": "done", "ms": elapsed(), "mode": mode}

    # -- verification -----------------------------------------------------
    verification: dict = {"citations": check_citations(memo, len(evidence)), "claims": None}
    final = memo
    if mode == "llm" and settings.VERIFIER_ENABLED:
        yield {"type": "agent", "agent": "verifier", "status": "start"}
        with span("agent.verifier") as sp:
            try:
                claims = verify_claims(memo, evidence)
                verification["claims"] = claims
                sp.attributes["pass_rate"] = claims["pass_rate"]
                if claims["unsupported"] and claims["pass_rate"] >= 0.5:
                    final = revise(memo, claims["unsupported"])
            except Exception as e:
                verification["claims"] = {"method": "skipped", "error": type(e).__name__}
        yield {"type": "agent", "agent": "verifier", "status": "done", "ms": elapsed()}
    yield {"type": "verification", "verification": verification}

    final = redact_pii(final)
    if final != streamed:
        yield {"type": "revision", "answer": final}

    yield {
        "type": "done", "answer": final, "mode": mode, "sources": sources,
        "profile": profile.to_dict(), "flags": flags,
        "research": verified.to_dict(), "factcheck": report_d,
        "audit": audit_d, "schedule": plan_d, "timetable": timetable_d,
        "visits": [v.to_dict() for v in browser.visits], "verification": verification,
    }


def advise(raw: dict) -> dict:
    """Run to completion and return the final `done` event (non-streaming callers)."""
    final: dict = {}
    for event in run(raw):
        if event["type"] == "done":
            final = event
    trace = current_trace()
    if trace is not None:
        final["trace"] = trace.to_dict()
    return final
