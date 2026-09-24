"""
specialists.py
==============
The four specialist agents. Each is a plain function

    run_<name>(plan: QueryPlan, source_filter: str | None) -> AgentResult

that uses tools (hybrid retrieval, the stats table, the prerequisite graph)
and returns evidence. None of them call an LLM: the only LLM steps are the
router, synthesizer, and verifier. That keeps specialists fast, free,
deterministic, and unit-testable — and it means that everything the
synthesizer cites came from a tool, not from a model.

  reviews   hybrid RAG over reviews + Reddit (balanced per professor for
            comparisons)
  stats     exact counts/averages from stats.py
  catalog   course descriptions and the prerequisite graph
  planner   eligibility from the prerequisite graph, ranked with review
            stats (difficulty, best-rated professors per course)
"""

from __future__ import annotations

from ..guardrails import neutralise_evidence
from ..knowledge import catalog as cat
from ..knowledge.stats import compute_stats, professors_for_course, stats_to_text
from ..rag.index import RetrievedChunk, SearchFilters, get_index
from ..tracing import span
from .state import AgentResult, Evidence, QueryPlan

SOURCE_NAMES = {
    "rmp": "RateMyProfessors",
    "coursicle": "Coursicle",
    "reddit": "Reddit r/txstate",
    "official": "TXST Course Catalog",
}


def source_label(meta: dict) -> str:
    """Programmatic citation label — built from metadata, never by the LLM."""
    src = meta.get("source_dir", "")
    name = SOURCE_NAMES.get(src, "Unknown source")
    course = meta.get("course") or ""
    prof = meta.get("professor") or ""
    if src == "official":
        return f"{name} — {course}"
    if src == "reddit":
        who = f"{prof} · " if prof and prof.lower() != "unknown" else ""
        return f"{name} — {who}{course}".rstrip(" —·")
    parts = [p for p in (prof, course) if p]
    date = meta.get("date")
    if date and date != "unknown":
        parts.append(date)
    return f"{name} — {' · '.join(parts)}"


def _chunk_evidence(chunk: RetrievedChunk, agent: str) -> Evidence:
    meta = chunk["metadata"]
    kind = {"catalog": "catalog", "reddit": "reddit"}.get(meta.get("chunk_type"), "review")
    return Evidence(
        kind=kind,
        text=neutralise_evidence(chunk["text"]),
        label=source_label(meta),
        agent=agent,
        chunk_id=chunk["id"],
        metadata={"professor": meta.get("professor"), "course": meta.get("course"),
                  "source": meta.get("source_dir"), "scores": chunk.get("scores", {})},
    )


# ---------------------------------------------------------------------------
# Reviews agent
# ---------------------------------------------------------------------------

def run_reviews(plan: QueryPlan, source_filter: str | None = None, k: int = 8) -> AgentResult:
    ix = get_index()
    q = plan.standalone_question
    sources = [source_filter] if source_filter else None
    courses = plan.courses or None
    notes: list[str] = []

    with span("agent.reviews", intent=plan.intent) as s:
        if plan.intent == "compare":
            chunks = ix.balanced(q, plan.professors or None, courses, per_professor=3,
                                 sources=sources)
        elif plan.intent == "course_info":
            chunks = ix.search(q, k=k, filters=SearchFilters(
                courses=courses, sources=sources, chunk_types=["review", "reddit"]))
        else:
            chunks = ix.search(q, k=k, filters=SearchFilters(
                professors=plan.professors or None, courses=courses, sources=sources,
                chunk_types=["review", "reddit"]))

        # Tell the synthesizer when the course filter had to be relaxed, so
        # it doesn't present other-course reviews as course-specific.
        if plan.professors and plan.courses and chunks:
            on_course = [c for c in chunks if c["metadata"].get("course") in plan.courses]
            if len(on_course) < len(chunks):
                notes.append(
                    f"Only {len(on_course)} of the {len(chunks)} retrieved reviews are "
                    f"specifically about {', '.join(plan.courses)}; the rest are about "
                    "the same professor's other courses — say so if you use them."
                )
        for prof in plan.professors:
            if not any(c["metadata"].get("professor") == prof for c in chunks):
                notes.append(f"No reviews of {prof} matched this question.")

        s.attributes["chunks"] = len(chunks)
        evidence = [_chunk_evidence(c, "reviews") for c in chunks]
        return AgentResult(agent="reviews", evidence=evidence, notes=notes,
                           data={"chunks": len(chunks)})


# ---------------------------------------------------------------------------
# Stats agent
# ---------------------------------------------------------------------------

def run_stats(plan: QueryPlan, source_filter: str | None = None) -> AgentResult:
    with span("agent.stats") as s:
        course = plan.courses[0] if plan.courses else None
        payloads: list[dict] = []

        if plan.professors:
            for prof in plan.professors:
                st = compute_stats(prof, course)
                if course and st["review_count"] == 0:
                    # No reviews for this pair: fall back to the professor overall
                    payloads.append(compute_stats(prof, None))
                    payloads[-1]["note"] = f"No {course} reviews of {prof}; overall stats shown."
                else:
                    payloads.append(st)
        elif course:
            if plan.intent == "compare":
                payloads = [p for p in professors_for_course(course) if p["review_count"] >= 2][:5]
            payloads.insert(0, compute_stats(None, course))

        evidence, notes = [], []
        for p in payloads:
            if p["review_count"] == 0:
                who = " ".join(x for x in (p["professor"], p["course"]) if x)
                notes.append(f"There are no reviews for {who} in the dataset.")
                continue
            text = stats_to_text(p)
            if p.get("note"):
                text = p["note"] + "\n" + text
            label = "Computed statistics — " + " · ".join(
                x for x in (p["professor"], p["course"]) if x)
            evidence.append(Evidence(kind="stats", text=text, label=label, agent="stats",
                                     metadata={"professor": p["professor"], "course": p["course"]}))
        s.attributes["tables"] = len(evidence)
        return AgentResult(agent="stats", evidence=evidence, notes=notes,
                           data={"stats": payloads})


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
    """
    "What can I take next?" — eligibility is computed, never generated.
    Each eligible course is annotated with review stats so the synthesizer
    can reason about difficulty and professor choice with real numbers.
    """
    with span("agent.planner") as s:
        completed = set(plan.completed_courses)
        if not completed:
            return AgentResult(agent="planner", notes=[
                "The student didn't say which courses they've completed; ask them."])

        eligible = cat.eligible_courses(completed)
        # Courses the student asked about but hasn't taken (e.g. "Assembly or DS?")
        focus = [c for c in plan.courses if c not in completed]
        rows = []
        for e in eligible:
            st = compute_stats(None, e["code"])
            top = [p for p in professors_for_course(e["code"])
                   if p["review_count"] >= 3 and p["avg_quality"] is not None]
            top.sort(key=lambda p: -p["avg_quality"])
            rows.append({
                **e,
                "review_count": st["review_count"],
                "avg_difficulty": st["avg_difficulty"],
                "avg_quality": st["avg_quality"],
                "top_professors": [
                    {"name": p["professor"], "avg_quality": p["avg_quality"],
                     "avg_difficulty": p["avg_difficulty"], "n": p["review_count"]}
                    for p in top[:3]
                ],
                "focus": e["code"] in focus,
            })
        # Newly unlocked core courses first, then by review volume
        rows.sort(key=lambda r: (not r["focus"], not r["newly_unlocked"], -r["review_count"]))

        lines = [f"ELIGIBILITY (computed from the catalog prerequisite graph). "
                 f"Completed (incl. implied): {', '.join(sorted(completed))}"]
        for r in rows:
            line = f"- {r['code']} {r['title']}"
            if r["review_count"]:
                line += (f" | {r['review_count']} reviews, avg difficulty "
                         f"{r['avg_difficulty']}/5, avg quality {r['avg_quality']}/5")
                if r["top_professors"]:
                    line += " | professors by RMP quality: " + ", ".join(
                        f"{p['name']} ({p['avg_quality']}/5, n={p['n']})" for p in r["top_professors"])
            else:
                line += " | no reviews in dataset"
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
                               label="Course planner — prerequisite graph + review stats",
                               agent="planner")],
            data={"completed": sorted(completed), "eligible": rows},
        )


SPECIALISTS = {
    "reviews": run_reviews,
    "stats": run_stats,
    "catalog": run_catalog,
    "planner": run_planner,
}
