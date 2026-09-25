"""
mcp_server.py
=============
Exposes Bobcat Advisor's specialist tools over the Model Context Protocol, so
any MCP client (Claude Desktop, Claude Code, IDE agents) can query TXST CS
course and professor data directly.

The tools are the same functions the in-app agents call — the MCP client's
model becomes one more orchestrator over them. `ask_advisor` runs the full
Q&A pipeline (needs an LLM API key) and `recommend_courses` runs the advising
pipeline (browses the TXST catalog; template memo without a key); every other
tool is deterministic and works offline.

Install into a SEPARATE environment (the mcp SDK needs a newer Starlette than
the API's FastAPI pin):

    python -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt

Claude Desktop / Claude Code config:

    {
      "mcpServers": {
        "bobcat-advisor": {
          "command": "/abs/path/backend/.venv-mcp/bin/python",
          "args": ["/abs/path/backend/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve data/ and documents/ relative to this file, whatever the client's cwd.
BACKEND = Path(__file__).resolve().parent
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.advising.pipeline import advise  # noqa: E402
from app.agents.orchestrator import answer  # noqa: E402
from app.agents.specialists import source_label  # noqa: E402
from app.knowledge import catalog as cat  # noqa: E402
from app.knowledge.corpus import registry  # noqa: E402
from app.rag.index import SearchFilters, get_index  # noqa: E402
from app.tracing import start_trace  # noqa: E402

mcp = FastMCP("bobcat-advisor")


def _prof(name: str) -> str:
    matches = registry().match_professors(name)
    if not matches:
        raise ValueError(f"No reviews for '{name}'. Known: {', '.join(registry().professors)}")
    return matches[0]


def _course(code: str) -> str:
    matches = registry().match_courses(code)
    if not matches:
        raise ValueError(f"Unknown course '{code}'")
    return matches[0]


@mcp.tool()
def search_reviews(query: str, professor: str | None = None, course: str | None = None,
                   k: int = 6) -> list[dict]:
    """Hybrid (semantic + keyword) search over student reviews and Reddit posts.
    Optionally filter by professor name and/or course (e.g. "CS3358" or "data structures")."""
    filters = SearchFilters(
        professors=[_prof(professor)] if professor else None,
        courses=[_course(course)] if course else None,
        chunk_types=["review", "reddit"],
    )
    return [{"source": source_label(r["metadata"]), "text": r["text"]}
            for r in get_index().search(query, k=min(k, 15), filters=filters)]


@mcp.tool()
def course_info(course: str) -> dict:
    """Official catalog entry, full prerequisite tree, and what the course unlocks."""
    c = cat.get_course(_course(course))
    if not c:
        raise ValueError(f"{course} is not in the catalog")
    return {**c.to_dict(), "prereq_tree": cat.prereq_chain(c.code), "unlocks": cat.unlocks(c.code)}


@mcp.tool()
def plan_next_courses(completed: list[str]) -> dict:
    """Courses a student is eligible for, computed from the prerequisite graph.
    Completed courses imply their own prerequisites (CS2308 implies CS1428)."""
    codes: set[str] = set()
    for c in completed:
        codes.update(registry().match_courses(c))
    done = cat.expand_completed(codes)
    return {"completed_including_implied": sorted(done), "eligible": cat.eligible_courses(done)}


@mcp.tool()
def ask_advisor(question: str) -> dict:
    """Run the full multi-agent pipeline (router -> specialists -> cited answer ->
    verifier). Needs an LLM API key; without it, returns an extractive answer."""
    with start_trace():
        out = answer(question)
    return {"answer": out.get("answer"), "mode": out.get("mode"),
            "sources": [f"[{s['n']}] {s['label']}" for s in out.get("sources", [])]}


@mcp.tool()
def recommend_courses(completed: list[str], year: str = "freshman", major: str = "Computer Science",
                      degree: str = "BS", semester: str = "Fall", interests: list[str] | None = None,
                      target_credits: int = 15, in_progress: list[str] | None = None,
                      catalog_year: str | None = None) -> dict:
    """Recommend next-term courses for a TXST student. Browses the live TXST catalog for the
    degree requirements, fact-checks them, audits progress, and plans a balanced schedule plus a
    roadmap. Returns the advising memo, schedule, audit and fact-check report."""
    with start_trace():
        done = advise({"completed": completed, "year": year, "major": major, "degree": degree,
                       "semester": semester, "interests": interests or [],
                       "target_credits": target_credits, "in_progress": in_progress or [],
                       "catalog_year": catalog_year})
    return {k: done.get(k) for k in ("answer", "schedule", "audit", "factcheck", "flags", "visits")}


if __name__ == "__main__":
    mcp.run()
