"""
verifier.py
===========
Checks the drafted answer against the evidence it cites.

Two layers:
  1. check_citations()  deterministic — every [n] must exist, and we measure
                        how many factual sentences carry a citation at all.
  2. verify_claims()    LLM judge (fast model) — for each sentence, is it
                        supported by the evidence it cites? Unsupported
                        sentences are removed from the final answer.

Removing instead of regenerating is deliberate: a regeneration costs a
second synthesis call (the most expensive one) and can introduce new
unsupported claims; deleting a flagged sentence can only make the answer
more conservative. The pass rate is logged per answer, making
"faithfulness in production" a number on the analytics page rather than a
hope.
"""

from __future__ import annotations

import re

from .. import llm
from ..config import settings
from .state import Evidence

_CITE = re.compile(r"\[(\d+)\]")
_NO_INFO = "couldn't find enough information"


def split_sentences(text: str) -> list[str]:
    """Sentence-ish units; bullet lines count as their own unit."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        units.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z*\"“(])", line) if s.strip())
    return units


def _is_claim(sentence: str) -> bool:
    """Headings, questions back to the user, and the no-info line aren't claims."""
    s = sentence.strip("*_#- ").strip()
    if len(s.split()) < 5 or s.endswith("?") or s.endswith(":"):
        return False
    return _NO_INFO not in s.lower()


def check_citations(answer: str, n_evidence: int) -> dict:
    sentences = [s for s in split_sentences(answer) if _is_claim(s)]
    cited = [s for s in sentences if _CITE.search(s)]
    invalid = sorted({int(n) for n in _CITE.findall(answer) if not 1 <= int(n) <= n_evidence})
    return {
        "claims": len(sentences),
        "cited_claims": len(cited),
        "citation_coverage": round(len(cited) / len(sentences), 3) if sentences else 1.0,
        "invalid_citations": invalid,
    }


VERIFIER_PROMPT = """You check whether each sentence of an answer is supported by the evidence \
it cites. A sentence is SUPPORTED if the cited evidence states or directly implies it \
(paraphrase and reasonable summarisation of several sources are fine). It is UNSUPPORTED if it \
adds facts, numbers, or strong generalisations the cited evidence doesn't contain, or cites \
nothing while making a factual claim. Recommendations are supported if they follow from \
supported claims.

Return JSON: {"verdicts": [{"i": <sentence number>, "supported": true|false, "reason": "<short>"}]}"""


def verify_claims(answer: str, evidence: list[Evidence]) -> dict:
    sentences = split_sentences(answer)
    claims = [(i, s) for i, s in enumerate(sentences, 1) if _is_claim(s)]
    if not claims:
        return {"method": "llm", "checked": 0, "unsupported": [], "pass_rate": 1.0}

    by_n = {e.n: e for e in evidence}
    cited_ns = sorted({int(n) for _, s in claims for n in _CITE.findall(s) if int(n) in by_n})
    ev_text = "\n\n".join(f"[{n}] {by_n[n].text[:700]}" for n in cited_ns) or "(no evidence cited)"
    numbered = "\n".join(f"{i}. {s}" for i, s in claims)

    data = llm.chat_json(
        [{"role": "system", "content": VERIFIER_PROMPT},
         {"role": "user", "content": f"EVIDENCE:\n{ev_text}\n\nANSWER SENTENCES:\n{numbered}"}],
        agent="verifier", model=settings.LLM_FAST_MODEL, max_tokens=800,
        timeout_s=settings.LLM_FAST_TIMEOUT_S, fallback=False,
    )
    claim_ids = {i for i, _ in claims}
    unsupported = []
    for v in data.get("verdicts", []):
        if isinstance(v, dict) and v.get("supported") is False and v.get("i") in claim_ids:
            unsupported.append({"i": v["i"], "sentence": sentences[v["i"] - 1],
                                "reason": str(v.get("reason", ""))[:200]})
    return {
        "method": "llm",
        "checked": len(claims),
        "unsupported": unsupported,
        "pass_rate": round(1 - len(unsupported) / len(claims), 3),
    }


def revise(answer: str, unsupported: list[dict]) -> str:
    """Drop unsupported sentences; say that we did."""
    if not unsupported:
        return answer
    revised = answer
    for u in unsupported:
        revised = revised.replace(u["sentence"], "")
    revised = re.sub(r"[ \t]+\n", "\n", revised)
    revised = re.sub(r"\n{3,}", "\n\n", revised).strip()
    # A bullet line that lost its only sentence leaves a bare "-"
    revised = re.sub(r"^\s*[-*]\s*$", "", revised, flags=re.MULTILINE).strip()
    n = len(unsupported)
    return (revised + f"\n\n_Removed {n} statement{'s' if n > 1 else ''} that couldn't be "
            "matched to a cited source._")
