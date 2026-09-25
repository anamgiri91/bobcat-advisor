"""
state.py
========
Data passed between agents. Kept as plain dataclasses so every hop in the
pipeline is inspectable, serialisable into the trace, and easy to assert on
in tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

INTENTS = ("course_info", "compare", "prereq", "plan", "policy", "instructor", "off_topic")

# Which specialist agents run for each intent. The router can't invent new
# agents; it only picks an intent, so the plan space stays small and testable.
# "instructor" (questions about a specific teacher) runs nothing: the app
# doesn't share opinions or ratings about individual people.
AGENTS_FOR_INTENT: dict[str, list[str]] = {
    "course_info": ["catalog", "search"],
    "compare": ["catalog", "search"],
    "prereq": ["catalog"],
    "plan": ["planner"],
    # Academic rules, procedures, deadlines, department programs, honor code.
    "policy": ["kb", "calendar"],
    "instructor": [],
    "off_topic": [],
}


@dataclass
class QueryPlan:
    intent: str
    standalone_question: str
    courses: list[str] = field(default_factory=list)
    completed_courses: list[str] = field(default_factory=list)
    # Set when the request must be declined before any retrieval:
    # "private_info" | "harassment" | "system_prompt"
    refusal: str | None = None
    injection_suspected: bool = False
    method: str = "rules"           # "llm" | "rules"

    @property
    def agents(self) -> list[str]:
        return AGENTS_FOR_INTENT.get(self.intent, [])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["agents"] = self.agents
        return d


@dataclass
class Evidence:
    kind: str                  # catalog | prereq | plan | dates | a knowledge-base kind (app/kb/sources.py)
    text: str
    label: str                 # human-readable source label (shown to users)
    agent: str
    chunk_id: str | None = None
    metadata: dict = field(default_factory=dict)
    n: int = 0                 # citation number, assigned by the orchestrator

    def to_public(self) -> dict:
        return {"n": self.n, "kind": self.kind, "label": self.label,
                "agent": self.agent, "chunk_id": self.chunk_id,
                "url": self.metadata.get("url"), "snippet": self.text[:400]}


@dataclass
class AgentResult:
    agent: str
    evidence: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # facts for the synthesizer, e.g. "X is not in the catalog"
    data: dict = field(default_factory=dict)          # structured payload for the UI
    error: str | None = None
