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
  search    hybrid search over the catalog, for questions that describe a
            topic rather than name a course ("which course covers compilers?")
  planner   eligibility from the prerequisite graph
"""

from __future__ import annotations

from ..knowledge import catalog as cat
from ..rag.index import RetrievedChunk, SearchFilters, get_index
from ..tracing import span
from .state import AgentResult, Evidence, QueryPlan


def source_label(meta: dict) -> str:
    """Programmatic citation label — built from metadata, never by the LLM."""
    return f"TXST Course Catalog — {meta.get('course') or 'unknown course'}"


def _chunk_evidence(chunk: RetrievedChunk, agent: str) -> Evidence:
    meta = chunk["metadata"]
    return Evidence(
        kind="catalog",
        text=chunk["text"],
        label=source_label(meta),
        agent=agent,
        chunk_id=chunk["id"],
        metadata={"course": meta.get("course"), "scores": chunk.get("scores", {})},
    )


# ---------------------------------------------------------------------------
# Search agent
# ---------------------------------------------------------------------------

def run_search(plan: QueryPlan, source_filter: str | None = None, k: int = 4) -> AgentResult:
    """Catalog entries relevant to the question, beyond any course it names."""
    with span("agent.search", intent=plan.intent) as s:
        chunks = get_index().search(plan.standalone_question, k=k,
                                    filters=SearchFilters(chunk_types=["catalog"]))
        s.attributes["chunks"] = len(chunks)
        return AgentResult(agent="search", evidence=[_chunk_evidence(c, "search") for c in chunks],
                           data={"chunks": len(chunks)})


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
    "planner": run_planner,
}
