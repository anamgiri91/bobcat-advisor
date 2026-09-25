"""
guardrails.py
=============
Input/output safety checks. Deterministic on purpose: these run before and
after every LLM call, cost nothing, and can't be talked out of their job.

Threats specific to this app
----------------------------
1. Requests for private information about real people (home address,
   phone, salary). Declined before retrieval.
2. Requests to generate harassment ("roast", "mean tweet") about anyone.
   Declined before retrieval.
3. Questions about individual instructors. The app has no data about
   people and deliberately doesn't share opinions or ratings of them; the
   router sends these to a fixed reply (REFUSALS["instructor"]).
4. Prompt injection. Two surfaces: the user's question ("ignore previous
   instructions...") and fetched web pages, which are untrusted third-party
   text placed inside the prompt. Evidence is wrapped in <evidence> tags,
   instruction-like lines are neutralised, and the system prompt tells the
   model evidence is data.
5. PII leaking into answers or logs. Redacted on the way out.
"""

from __future__ import annotations

import re

_PRIVATE_INFO = re.compile(
    r"\b(home address|where does .{1,40} live|phone number|cell ?phone|salary|"
    r"how much does .{1,40} (make|earn)|social security|ssn|date of birth|"
    r"personal email|married|wife|husband|kids|children|religion|age of)\b",
    re.IGNORECASE,
)
_HARASSMENT = re.compile(
    r"\b(roast|mean tweet|insult|trash[- ]talk|make fun of|humiliate|"
    r"write (something|a post) (mean|nasty)|dox)\b",
    re.IGNORECASE,
)
_SYSTEM_PROMPT = re.compile(
    r"\b(system prompt|your (instructions|prompt|rules)|initial prompt|"
    r"repeat (the|your) (text|words) above)\b",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above|earlier) (instructions|rules)|"
    r"disregard (the |your )?(instructions|rules|context)|you are now|"
    r"new instructions:|act as (?!a student)|just say (yes|no)|"
    r"pretend (you are|to be)|jailbreak)",
    re.IGNORECASE,
)

REFUSALS = {
    "private_info": (
        "I can't help with personal or private information about anyone. I can help "
        "with TXST CS courses: what they cover, prerequisites, and planning your next "
        "semester."
    ),
    "harassment": (
        "I won't write content meant to mock or attack anyone. I'm happy to help with "
        "course planning instead."
    ),
    "system_prompt": (
        "I can't share my internal instructions, but I'm happy to answer questions "
        "about TXST CS courses."
    ),
    "instructor": (
        "I don't share opinions, ratings or reviews of individual instructors. I can "
        "tell you what a course covers, its prerequisites, and what it unlocks, or build "
        "a full plan for next semester in the Advisor tab. For who is teaching a section, "
        "check the official TXST class schedule."
    ),
    "off_topic": (
        "I'm Bobcat Advisor — I help with Texas State CS courses: what they cover, "
        "prerequisites, and planning your next semester. Try asking something like "
        "“What do I need before CS3358?”"
    ),
    "greeting": (
        "Hi! I answer questions about Texas State CS courses — what they cover, "
        "prerequisites, what they unlock, and what to take next. What are you trying to "
        "decide?"
    ),
    "no_evidence": (
        "I couldn't find enough information in the retrieved sources to answer "
        "that question."
    ),
}


def classify_request(question: str) -> str | None:
    """Return a refusal category for requests that must be declined, else None."""
    if _SYSTEM_PROMPT.search(question):
        return "system_prompt"
    if _PRIVATE_INFO.search(question):
        return "private_info"
    if _HARASSMENT.search(question):
        return "harassment"
    return None


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def neutralise_evidence(text: str) -> str:
    """
    Defang instruction-like sentences inside untrusted text (fetched web
    pages). The text is kept but marked so the model reads it as quoted
    content, not as an instruction.
    """
    return _INJECTION.sub(lambda m: f"[quoted text: {m.group(0)}]", text)


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[email removed]", text)
    return _PHONE.sub("[phone removed]", text)
