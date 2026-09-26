"""
specialists.py
==============
The specialist agents. Each is a plain function

    run_<name>(plan: QueryPlan, source_filter: str | None) -> AgentResult

that uses tools (hybrid search over the catalog, the prerequisite graph)
and returns evidence. None of them call an LLM: the only LLM steps are the
router, synthesizer, and verifier. That keeps specialists fast, free,
deterministic, and unit-testable — and it means that everything the
synthesizer cites came from a tool, not from a model.

  catalog   course descriptions and the prerequisite graph
  search    hybrid search over the catalog and course syllabi, for
            questions that describe a topic ("which course covers
            compilers?") or ask what a named course's syllabus says
  kb        hybrid search over the knowledge base: academic rules, core
            curriculum, graduate catalog, CS department, registrar and
            student handbook pages (app/kb)
  calendar  dated facts from the academic calendar, marked past/upcoming
  planner   eligibility from the prerequisite graph
"""

from __future__ import annotations

import datetime as dt

from ..careers.mapping import topic_summary
from ..config import settings
from ..kb import calendar
from ..kb.sources import KB_KINDS, KIND_NAMES
from ..knowledge import catalog as cat
from ..rag.index import RetrievedChunk, SearchFilters, get_index
from ..tracing import span
from .state import AgentResult, Evidence, QueryPlan

# Searched for policy questions. Catalog entries (e.g. the internship and
# research courses) are searched separately so they can't crowd out policy
# pages; syllabi are searched per course.
POLICY_KINDS = [k for k in KB_KINDS if k != "syllabus"]


def source_label(meta: dict) -> str:
    """Programmatic citation label — built from metadata, never by the LLM."""
    kind = meta.get("chunk_type", "catalog")
    if kind == "catalog":
        return f"TXST Course Catalog — {meta.get('course') or 'unknown course'}"
    name = KIND_NAMES.get(kind, "TXST")
    where = meta.get("section_path") or meta.get("heading") or meta.get("title") or ""
    if kind == "syllabus" and meta.get("course"):
        name = f"{name} — {meta['course']}"
    when = (f"{meta['catalog_year']} catalog" if meta.get("catalog_year")
            else f"fetched {meta.get('fetched_at', 'unknown date')}")
    return f"{name} — {where} ({when})" if where else f"{name} ({when})"


def _chunk_evidence(chunk: RetrievedChunk, agent: str) -> Evidence:
    meta = chunk["metadata"]
    return Evidence(
        kind=meta.get("chunk_type", "catalog"),
        text=chunk["text"],
        label=source_label(meta),
        agent=agent,
        chunk_id=chunk["id"],
        metadata={"course": meta.get("course"), "url": meta.get("url"),
                  "fetched_at": meta.get("fetched_at"), "scores": chunk.get("scores", {})},
    )


def _fresh(chunks: list[RetrievedChunk], today: dt.date | None = None) -> tuple[list[RetrievedChunk], int]:
    """Drop knowledge-base chunks fetched longer ago than KB_MAX_AGE_DAYS."""
    today = today or dt.date.today()
    keep, stale = [], 0
    for c in chunks:
        fetched = c["metadata"].get("fetched_at")
        if fetched and (today - dt.date.fromisoformat(fetched)).days > settings.KB_MAX_AGE_DAYS:
            stale += 1
            continue
        keep.append(c)
    return keep, stale


def _stale_note(stale: int) -> list[str]:
    return ([f"{stale} matching page(s) were skipped because they were fetched more than "
             f"{settings.KB_MAX_AGE_DAYS} days ago; tell the student to check the official page."]
            if stale else [])


# ---------------------------------------------------------------------------
# Search agent (catalog + syllabi)
# ---------------------------------------------------------------------------

def run_search(plan: QueryPlan, source_filter: str | None = None, k: int = 4) -> AgentResult:
    """Catalog entries relevant to the question, plus syllabus sections of named courses."""
    with span("agent.search", intent=plan.intent) as s:
        ix = get_index()
        q = plan.standalone_question
        chunks = ix.search(q, k=k, filters=SearchFilters(chunk_types=["catalog"]))
        if plan.courses:
            syl = ix.search(q, k=3, filters=SearchFilters(courses=plan.courses,
                                                          chunk_types=["syllabus"]), min_results=0)
        else:
            syl = ix.search(q, k=2, filters=SearchFilters(chunk_types=["syllabus"]))
        syl, stale = _fresh(syl)
        s.attributes.update(chunks=len(chunks), syllabus=len(syl))
        evidence = [_chunk_evidence(c, "search") for c in chunks + syl]
        return AgentResult(agent="search", evidence=evidence, notes=_stale_note(stale),
                           data={"chunks": len(chunks), "syllabus_sections": len(syl)})


# ---------------------------------------------------------------------------
# Knowledge-base agent (rules, procedures, programs)
# ---------------------------------------------------------------------------

def run_kb(plan: QueryPlan, source_filter: str | None = None, k: int = 6) -> AgentResult:
    with span("agent.kb", intent=plan.intent) as s:
        ix = get_index()
        q = plan.standalone_question
        chunks = ix.search(q, k=k, filters=SearchFilters(chunk_types=POLICY_KINDS))
        chunks += ix.search(q, k=2, filters=SearchFilters(chunk_types=["catalog"]))
        if plan.courses:  # "Can I use AI in CS3358?" -> that course's syllabus too
            chunks += ix.search(q, k=2, filters=SearchFilters(courses=plan.courses,
                                                              chunk_types=["syllabus"]), min_results=0)
        chunks, stale = _fresh(chunks)
        notes = _stale_note(stale)
        if not chunks:
            notes.append("No official policy pages matched. Say so and point the student to the "
                         "TXST catalog, registrar or their academic advisor.")
        s.attributes.update(chunks=len(chunks), stale=stale)
        return AgentResult(agent="kb", evidence=[_chunk_evidence(c, "kb") for c in chunks],
                           notes=notes, data={"chunks": len(chunks)})


# ---------------------------------------------------------------------------
# Calendar agent (dated facts)
# ---------------------------------------------------------------------------

def run_calendar(plan: QueryPlan, source_filter: str | None = None,
                 today: dt.date | None = None) -> AgentResult:
    with span("agent.calendar") as s:
        today = today or dt.date.today()
        facts = calendar.lookup(plan.standalone_question, today=today)
        s.attributes["facts"] = len(facts)
        if not facts:
            return AgentResult(agent="calendar")
        lines = [f"ACADEMIC CALENDAR FACTS (today is {today.isoformat()}; computed from the "
                 "registrar's published calendar):"]
        for f in facts:
            when = (f"in {f['days_from_today']} days" if f["status"] == "upcoming"
                    else f"{-f['days_from_today']} days ago")
            lines.append(f"- {f['event']}: {f['date']}" + (f" ({f['term']})" if f["term"] else "")
                         + f" — {f['status'].upper()}, {when}")
        fetched = max(f["fetched_at"] for f in facts)
        return AgentResult(agent="calendar", evidence=[Evidence(
            kind="dates", text="\n".join(lines),
            label=f"TXST Registrar — academic calendar (fetched {fetched})", agent="calendar",
            metadata={"url": facts[0]["url"]})], data={"facts": facts})


# ---------------------------------------------------------------------------
# Catalog agent
# ---------------------------------------------------------------------------

def _flatten_chain(node: dict, depth: int = 0) -> list[str]:
    lines = []
    for group in node["requires"]:
        opts = " OR ".join(
            f"{o['code']}" + (f" ({o['title']})" if o.get("title") else "") for o in group["any_of"]
        )
        cond = " (or a non-course alternative)" if group["conditional"] else ""
        lines.append(f"{'  ' * depth}- {node['code']} requires: {opts}{cond}")
        for o in group["any_of"]:
            lines.extend(_flatten_chain(o, depth + 1))
    return lines


def run_catalog(plan: QueryPlan, source_filter: str | None = None) -> AgentResult:
    with span("agent.catalog") as s:
        evidence, notes, data = [], [], {"courses": []}
        for code in plan.courses:
            course = cat.get_course(code)
            if not course:
                if code.startswith("CS"):
                    notes.append(f"{code} is not in the course catalog.")
                continue
            data["courses"].append(course.to_dict())
            text = f"{course.code}. {course.title}.\n{course.description}"
            if course.prereq_text:
                text += f"\nPrerequisite (official wording): {course.prereq_text}"
            evidence.append(Evidence(kind="catalog", text=text,
                                     label=f"TXST Course Catalog — {course.code}",
                                     agent="catalog", metadata={"course": code}))

            if plan.intent == "prereq":
                chain = _flatten_chain(cat.prereq_chain(code))
                unlocked = cat.unlocks(code)
                lines = [f"PREREQUISITE GRAPH for {code} {course.title} (computed from the catalog):"]
                lines += chain or [f"- {code} has no course prerequisites."]
                lines.append(f"- Taking {code} unlocks: "
                             + (", ".join(unlocked) if unlocked else "no catalog courses"))
                if plan.completed_courses or len(plan.courses) > 1:
                    completed = set(plan.completed_courses) | (set(plan.courses) - {code})
                    ok, missing, conditions = cat.is_eligible(code, cat.expand_completed(completed))
                    lines.append(
                        f"- With {', '.join(sorted(completed))} completed: "
                        + ("ELIGIBLE" if ok else "NOT eligible — still missing "
                           + "; ".join(" or ".join(g) for g in missing))
                    )
                    if conditions:
                        lines.append("- Also check: " + "; ".join(conditions))
                evidence.append(Evidence(kind="prereq", text="\n".join(lines),
                                         label=f"Prerequisite graph — {code}", agent="catalog",
                                         metadata={"course": code}))
        if plan.topics:
            # Ahead of the course entries: it's the direct answer to "how do I learn X?"
            evidence.insert(0, Evidence(kind="topic", text=topic_summary(plan.topics, plan.courses),
                                        label="Courses for this topic — computed from the catalog",
                                        agent="catalog"))
        s.attributes["courses"] = len(data["courses"])
        return AgentResult(agent="catalog", evidence=evidence, notes=notes, data=data)


# ---------------------------------------------------------------------------
# Planner agent
# ---------------------------------------------------------------------------

def run_planner(plan: QueryPlan, source_filter: str | None = None) -> AgentResult:
    """"What can I take next?" — eligibility is computed, never generated."""
    with span("agent.planner") as s:
        completed = set(plan.completed_courses)
        if not completed:
            return AgentResult(agent="planner", notes=[
                "The student didn't say which courses they've completed; ask them."])

        eligible = cat.eligible_courses(completed)
        # Courses the student asked about but hasn't taken (e.g. "Assembly or DS?")
        focus = [c for c in plan.courses if c not in completed]
        rows = [{**e, "focus": e["code"] in focus, "unlocks": cat.unlocks(e["code"])}
                for e in eligible]
        # Asked-about courses first, then newly unlocked core, then what each unlocks.
        rows.sort(key=lambda r: (not r["focus"], not r["newly_unlocked"], -len(r["unlocks"]),
                                 r["code"]))

        lines = [f"ELIGIBILITY (computed from the catalog prerequisite graph). "
                 f"Completed (incl. implied): {', '.join(sorted(completed))}"]
        for r in rows:
            line = f"- {r['code']} {r['title']}"
            if r["unlocks"]:
                line += f" | prerequisite for: {', '.join(r['unlocks'])}"
            if r["conditions"]:
                line += " | check: " + "; ".join(r["conditions"])
            lines.append(line)
        for c in focus:
            if c not in {r["code"] for r in rows}:
                ok, missing, _ = cat.is_eligible(c, completed)
                if not ok:
                    lines.append(f"- {c}: NOT yet eligible — missing "
                                 + "; ".join(" or ".join(g) for g in missing))

        s.attributes["eligible"] = len(rows)
        return AgentResult(
            agent="planner",
            evidence=[Evidence(kind="plan", text="\n".join(lines),
                               label="Course planner — prerequisite graph",
                               agent="planner")],
            data={"completed": sorted(completed), "eligible": rows},
        )


SPECIALISTS = {
    "catalog": run_catalog,
    "search": run_search,
    "kb": run_kb,
    "calendar": run_calendar,
    "planner": run_planner,
}
