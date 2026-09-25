"""
router.py
=========
Turns (question, conversation history) into a QueryPlan: intent, courses,
a standalone rewrite of the question, and which specialist agents to run.

Two implementations with one contract:
  route_llm()    fast model in JSON mode — handles paraphrase and follow-ups
                 ("does it have a lab?").
  route_rules()  deterministic fallback — used when no API key is set, the
                 LLM call fails, or the budget is exhausted. Also the
                 baseline the LLM router is evaluated against.

The LLM never gets the final word on entities: every course it returns is
validated against the catalog-derived registry.

Rule and procedure questions ("last day to drop?", "can I retake CS2308?")
get the "policy" intent and are answered from the knowledge base (app/kb).

Questions about a specific instructor ("is Dr. X a hard grader?", "who's
the best professor for CS3358?") get the "instructor" intent, which runs no
agents and returns a fixed reply: the app deliberately doesn't share
opinions, ratings or reviews of individual people.
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
    r"\b(class|classes|course|courses|cs|exam|exams|grade|homework|lecture|lab|semester|take|"
    r"prereq|prerequisite|major|minor|degree|credit|credits|hours|syllabus|workload|txst|"
    r"texas state|elective|electives|graduate|graduation|cover|covers|learn|topics?)\b",
    re.IGNORECASE,
)
_INSTRUCTOR = re.compile(
    r"\b(prof|profs|professors?|instructors?|teachers?|lecturers?|dr\.?|doctor|"
    r"who teaches|who's teaching|who is teaching|taught by|rate ?my ?prof\w*|rmp|"
    r"curves?|he|she|him|her|his|hers)\b",
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
# Academic rules, procedures, deadlines and programs: answered from the
# knowledge base (app/kb), not the course catalog.
_POLICY = re.compile(
    r"\b(drop(ping|ped)?|withdraw\w*|w grade|retake|re-take|repeat(ing)? (a|the|this)? ?(course|class)|"
    r"repeat \w*\d{4}|replace (a|my|the) (grade|d|f)|grade replacement|pass/fail|pass-fail|"
    r"credit/no credit|incomplete grade|gpa|probation|suspension|academic standing|good standing|"
    r"full[- ]time|part[- ]time|course load|overload|max(imum)? (hours|credits)|how many hours|"
    r"graduat\w*|degree audit|apply to graduate|transfer (credit|courses?)|ap credit|"
    r"advanced placement|credit by exam|clep|dual credit|core curriculum|core requirement|"
    r"(communication|math|science|humanities|social|creative arts) core|general education|"
    r"regist(er|ration)|waitlist|wait list|closed class|full class|class is full|add a class|"
    r"deadline|last day|academic calendar|finals? week|final exam schedule|census date|"
    r"honou?r code|academic (integrity|honesty|dishonesty)|plagiari\w*|cheating|"
    r"(ai|chatgpt|generative ai) (on|for|in) (assignments?|homework|exams?|projects?)|"
    r"use (ai|chatgpt)|undergraduate research|research (opportunit\w*|lab)|internships?|co-?op|"
    r"honors (college|program|thesis)|bs/ms|b\.s\./m\.s\.|accelerated|fast[- ]track|"
    r"combined (degree|program)|5000[- ]level|graduate (course|class)(es)? as an undergrad\w*|"
    r"minor in|change (my )?major|advis(or|ing) (appointment|hold)|registration hold)\b",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(r"\b(it|its|that class|this class|that course|this course|the class|"
                        r"the course|that one|this one)\b", re.IGNORECASE)


def _courses_from_history(history: list[dict]) -> list[str]:
    reg = registry()
    for msg in reversed(history[-4:]):
        courses = reg.match_courses(msg["content"])
        if courses:
            return courses
    return []


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
    courses = registry().match_courses(question)
    standalone = question

    # Follow-ups inherit the course from the conversation ("does it have a lab?").
    if history and not courses and (_FOLLOW_UP.search(question) or len(question.split()) <= 6):
        courses = _courses_from_history(history)
        if courses:
            standalone = f"{question} (about {', '.join(courses)})"

    if _GREETING.match(question):
        intent = "off_topic"
    elif _INSTRUCTOR.search(question):
        intent = "instructor"
    elif _PLAN.search(question) and _COMPLETION_VERBS.search(question):
        intent = "plan"      # "I've taken X, what next?" even if it mentions graduation
    elif _POLICY.search(question):
        intent = "policy"
    elif _PLAN.search(question):
        intent = "plan"
    elif _PREREQ.search(question):
        intent = "prereq"
    elif is_comparison_query(question):
        intent = "compare"
    elif courses or _DOMAIN_WORDS.search(question):
        intent = "course_info"
    else:
        intent = "off_topic"

    completed = _completed_courses(question) if intent == "plan" else []
    return QueryPlan(
        intent=intent,
        standalone_question=standalone,
        courses=courses,
        completed_courses=sorted(expand_completed(set(completed))) if completed else [],
        method="rules",
    )


# ---------------------------------------------------------------------------
# LLM router
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    return """You route questions for a Texas State University (TXST) computer science course advisor.

Course codes look like CS3358. Common names: data structures=CS3358, assembly=CS2318,
foundations I=CS1428, foundations II=CS2308, operating systems=CS4328, software engineering=CS3398,
computer architecture=CS3339, object oriented=CS3354.

Intents:
- course_info: what a course covers (including its syllabus: textbook, weekly topics, projects,
  grading scheme), its level or credit hours, which course teaches a topic
- compare: choosing between two or more courses
- prereq: prerequisites, what a course unlocks, whether one can take X after Y
- plan: the student lists courses they've completed and asks what to take next
- policy: academic rules and procedures (dropping/withdrawing, repeating a course, pass/fail,
  course load, GPA and academic standing, graduation, transfer/AP credit, core curriculum,
  registration, deadlines and dates, honor code and AI use), and department programs
  (undergraduate research, internships, honors, the BS/MS track, taking graduate courses)
- instructor: anything about a specific professor/instructor/teacher (opinions, grading,
  who is best, who teaches a course, comparisons between instructors)
- off_topic: anything unrelated to TXST CS courses (including greetings)

Resolve "it" and "that class" using the conversation. Write standalone_question as the
question rewritten to be understandable without the conversation.
completed_courses: only courses the student says they took.

Return JSON only:
{"intent": "...", "standalone_question": "...", "courses": [], "completed_courses": []}"""


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
        courses=courses,
        completed_courses=sorted(expand_completed(set(completed))) if completed else [],
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
        s.attributes.update(method=plan.method, intent=plan.intent, courses=plan.courses)
        return plan
