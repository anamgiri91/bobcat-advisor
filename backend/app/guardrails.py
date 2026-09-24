"""
guardrails.py
=============
Input/output safety checks. Deterministic on purpose: these run before and
after every LLM call, cost nothing, and can't be talked out of their job.

Threats specific to this app
----------------------------
1. Requests for private information about real people (home address,
   phone, salary). Declined before retrieval.
2. Requests to generate harassment ("roast", "mean tweet") about a named
   professor. Declined: the corpus is opinions about real people, and the
   product's job is to help students choose, not to amplify attacks.
3. Prompt injection. Two surfaces: the user's question ("ignore previous
   instructions...") and — less obvious — the scraped reviews themselves,
   which are untrusted third-party text placed inside the prompt. Review
   text is wrapped in <evidence> tags, instruction-like lines are
   neutralised, and the system prompt tells the model evidence is data.
4. PII leaking into answers or logs (emails/phones occasionally appear in
   scraped text). Redacted on the way out.
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
        "I can't help with personal or private information about professors. "
        "I can only share what students say about their teaching — for example "
        "workload, exams, grading, or how their classes are run."
    ),
    "harassment": (
        "I won't write content meant to mock or attack a professor. If it helps, "
        "I can give you a balanced summary of what students say about their "
        "classes, including the criticisms."
    ),
    "system_prompt": (
        "I can't share my internal instructions, but I'm happy to answer questions "
        "about TXST CS courses and professors."
    ),
    "off_topic": (
        "I'm Bobcat Advisor — I can only help with Texas State CS courses and "
        "professors: reviews, comparisons, prerequisites, and planning your next "
        "semester. Try asking something like “Who's best for CS3358?”"
    ),
    "greeting": (
        "Hi! I answer questions about Texas State CS professors and courses — "
        "teaching style, exams, workload, grading, prerequisites, and what to take "
        "next. What are you trying to decide?"
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
    Defang instruction-like sentences inside scraped reviews. The text is
    kept (it may still be a legitimate opinion) but marked so the model
    reads it as quoted content, not as an instruction.
    """
    return _INJECTION.sub(lambda m: f"[quoted text: {m.group(0)}]", text)


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[email removed]", text)
    return _PHONE.sub("[phone removed]", text)
