"""
router.py
=========
Turns (question, conversation history) into a QueryPlan: intent, entities,
a standalone rewrite of the question, and which specialist agents to run.

Two implementations with one contract:
  route_llm()    fast model in JSON mode — handles paraphrase, pronouns
                 ("does he curve?"), and names we've never seen.
  route_rules()  deterministic fallback — used when no API key is set, the
                 LLM call fails, or the budget is exhausted. Also the
                 baseline the LLM router is evaluated against.

The LLM never gets the final word on entities: every professor/course it
returns is validated against the data-derived registry. A name that doesn't
resolve becomes `unknown_professors`, which short-circuits to an honest
"no reviews for X" instead of letting the synthesizer improvise.
"""

from __future__ import annotations

import re

from .. import llm
from ..config import settings
from ..guardrails import classify_request, looks_like_injection
from ..knowledge.catalog import expand_completed
from ..knowledge.corpus import registry
from ..rag.retrieve import is_comparison_query
from ..tracing import span
from .state import INTENTS, QueryPlan

# ---------------------------------------------------------------------------
# Rule-based router
# ---------------------------------------------------------------------------

_DOMAIN_WORDS = re.compile(
    r"\b(prof|professor|dr|teach|teaches|teaching|class|classes|course|courses|cs|"
    r"exam|exams|test|quiz|grade|grading|curve|homework|lecture|semester|take|"
    r"prereq|prerequisite|major|credit|syllabus|workload|txst|texas state|"
    r"office hours|attendance|easy|hard|difficult)\b",
    re.IGNORECASE,
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|yo|sup|thanks|thank you|good (morning|evening))\W*$", re.I)
_PLAN = re.compile(
    r"\b(i'?ve (taken|finished|passed|completed|done)|i have (taken|finished|passed|completed)|"
    r"i (took|finished|passed|completed)|done with|what can i take|eligible|"
    r"plan my|next semester|what should i take next|take next)\b",
    re.IGNORECASE,
)
_COMPLETION_VERBS = re.compile(
    r"\b(taken|took|finished|passed|completed|done with|already had)\b", re.IGNORECASE
)
_PREREQ = re.compile(
    r"\b(prereq\w*|pre-?requisite\w*|need before|needed before|required before|"
    r"required for|unlock\w*|chain|right after|before taking|do i need)\b",
    re.IGNORECASE,
)
_COMPARATIVE = re.compile(r"\b(easier|harder|better|worse|compare\w*|vs\.?|versus|instead)\b", re.I)
_PRONOUN = re.compile(r"\b(he|she|him|her|his|they|them|their|it|its|that class|this class|"
                      r"that course|the class|the course)\b", re.IGNORECASE)
_UNKNOWN_NAME = re.compile(r"\b(?i:professor|prof\.?|dr\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
_ASPECTS = {
    "curve": r"curv", "attendance": r"attend", "exams": r"exam|test|midterm|final",
    "workload": r"workload|homework|assignment|busy|time", "grading": r"grad",
    "lectures": r"lecture|teach|explain", "helpfulness": r"office hours|help",
    "quizzes": r"quiz",
}


def _entities_from_history(history: list[dict]) -> tuple[list[str], list[str]]:
    reg = registry()
    for msg in reversed(history[-4:]):
        profs = reg.match_professors(msg["content"])
        courses = reg.match_courses(msg["content"])
        if profs or courses:
            return profs, courses
    return [], []


def _completed_courses(question: str) -> list[str]:
    """Courses in sentences that say the student took them."""
    reg = registry()
    done: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+", question):
        if _COMPLETION_VERBS.search(sentence):
            # Stop at "should I take X or Y" style clauses in the same sentence
            head = re.split(r"\b(should|can|what|which)\b", sentence, maxsplit=1, flags=re.I)[0]
            done.extend(c for c in reg.match_courses(head) if c not in done)
    return done


def route_rules(question: str, history: list[dict] | None = None) -> QueryPlan:
    history = history or []
    reg = registry()
    profs = reg.match_professors(question)
    courses = reg.match_courses(question)
    standalone = question

    # Follow-ups: inherit entities from the conversation when the question
    # leans on it ("does he curve?", "is Seaman easier for that class?").
    if history and (_PRONOUN.search(question) or len(question.split()) <= 6
                    or _COMPARATIVE.search(question)):
        h_profs, h_courses = _entities_from_history(history)
        inherit_profs = not profs or (_COMPARATIVE.search(question) and len(profs) == 1)
        if inherit_profs:
            profs = profs + [p for p in h_profs if p not in profs]
        if not courses:
            courses = h_courses
        if profs or courses:
            standalone = f"{question} (about {', '.join(profs + courses)})"

    unknown = [
        m for m in _UNKNOWN_NAME.findall(question)
        if not reg.match_professors(m) and m.split()[0].lower() not in ("the", "my")
    ]

    if _GREETING.match(question):
        intent = "off_topic"
    elif _PLAN.search(question):
        intent = "plan"
    elif _PREREQ.search(question):
        intent = "prereq"
    elif len(profs) >= 2 or (is_comparison_query(question) and courses and not profs):
        intent = "compare"
    elif profs or unknown:
        intent = "professor_info"
    elif courses:
        intent = "course_info"
    elif _DOMAIN_WORDS.search(question):
        intent = "course_info" if not history else "professor_info"
    else:
        intent = "off_topic"

    completed = _completed_courses(question) if intent == "plan" else []
    if intent in ("prereq", "plan"):
        profs = []  # inherited professors are irrelevant to catalog questions

    return QueryPlan(
        intent=intent,
        standalone_question=standalone,
        professors=profs,
        courses=courses,
        completed_courses=sorted(expand_completed(set(completed))) if completed else [],
        unknown_professors=unknown,
        aspects=[a for a, p in _ASPECTS.items() if re.search(p, question, re.I)],
        method="rules",
    )


# ---------------------------------------------------------------------------
# LLM router
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    reg = registry()
    return f"""You route questions for a Texas State University CS course/professor advisor.

Known professors (reviews exist only for these): {", ".join(reg.professors)}.
Course codes look like CS3358. Common names: data structures=CS3358, assembly=CS2318,
foundations I=CS1428, foundations II=CS2308, operating systems=CS4328, software engineering=CS3398,
computer architecture=CS3339, object oriented=CS3354.

Intents:
- professor_info: about one professor (teaching, exams, curve, workload, grades...)
- compare: choosing between professors, or "best professor for <course>"
- course_info: about a course in general (content, difficulty) with no specific professor
- prereq: prerequisites, what a course unlocks, whether one can take X after Y
- plan: the student lists courses they've completed and asks what to take next
- off_topic: anything unrelated to TXST CS courses/professors (including greetings)

Resolve pronouns and "that class" using the conversation. Write standalone_question as the
question rewritten to be understandable without the conversation.
List every professor name mentioned (as written, full name if known) — including names
that are NOT in the known list. completed_courses: only courses the student says they took.

Return JSON only:
{{"intent": "...", "standalone_question": "...", "professors": [], "courses": [],
  "completed_courses": [], "aspects": []}}"""


def route_llm(question: str, history: list[dict] | None = None) -> QueryPlan:
    history = history or []
    convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
    user = (f"Conversation so far:\n{convo}\n\n" if convo else "") + f"Question: {question}"
    data = llm.chat_json(
        [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": user}],
        agent="router", model=settings.LLM_FAST_MODEL, max_tokens=400,
        timeout_s=settings.LLM_FAST_TIMEOUT_S, fallback=False,
    )

    reg = registry()
    standalone = str(data.get("standalone_question") or question)
    intent = data.get("intent") if data.get("intent") in INTENTS else None

    profs: list[str] = []
    unknown: list[str] = []
    for name in data.get("professors") or []:
        if not isinstance(name, str) or not name.strip():
            continue
        matched = reg.match_professors(name)
        if matched:
            profs.extend(p for p in matched if p not in profs)
        else:
            unknown.append(name.strip())
    # The registry also scans the rewritten question: cheap recall insurance.
    profs.extend(p for p in reg.match_professors(standalone) if p not in profs)

    def _codes(values) -> list[str]:
        out: list[str] = []
        for v in values or []:
            for c in reg.match_courses(str(v)):
                if c not in out:
                    out.append(c)
        return out

    courses = _codes(data.get("courses"))
    courses.extend(c for c in reg.match_courses(standalone) if c not in courses)
    completed = _codes(data.get("completed_courses"))

    if intent is None:
        raise ValueError(f"router returned invalid intent: {data.get('intent')!r}")

    return QueryPlan(
        intent=intent,
        standalone_question=standalone,
        professors=profs,
        courses=courses,
        completed_courses=sorted(expand_completed(set(completed))) if completed else [],
        unknown_professors=unknown,
        aspects=[str(a) for a in data.get("aspects") or []][:5],
        method="llm",
    )


def route(question: str, history: list[dict] | None = None, use_llm: bool | None = None) -> QueryPlan:
    """Guardrails first, then the LLM router, falling back to rules on any failure."""
    with span("agent.router") as s:
        refusal = classify_request(question)
        if use_llm is None:
            use_llm = llm.is_available()

        plan: QueryPlan | None = None
        if use_llm and refusal is None:
            try:
                plan = route_llm(question, history)
            except Exception as e:  # invalid JSON, budget, network: degrade, don't fail
                s.attributes["llm_error"] = f"{type(e).__name__}: {e}"
        if plan is None:
            plan = route_rules(question, history)

        plan.refusal = refusal
        plan.injection_suspected = looks_like_injection(question)
        s.attributes.update(method=plan.method, intent=plan.intent,
                            professors=plan.professors, courses=plan.courses)
        return plan
