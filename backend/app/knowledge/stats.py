"""
stats.py
========
Deterministic, countable facts about professors and courses, computed from
chunk metadata and per-review aspect tags.

An LLM reading 8 retrieved reviews will happily say "most students say the
exams are hard" when it has seen 3. This module answers the counting
questions exactly: "7 of 12 CS2308 reviews of Seaman mention a curve",
"average RMP difficulty 3.8/5 over 41 ratings". The StatsAgent returns
these as evidence the synthesizer must cite, not paraphrase.

Aspect tags come from data/aspects.jsonl when present (LLM-extracted, see
aspects.py), falling back to a transparent keyword lexicon otherwise. Every
stats payload says which method produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from ..config import settings
from .corpus import chunk_courses, load_chunks

# ---------------------------------------------------------------------------
# Aspect schema (shared with aspects.py, the LLM extractor)
# ---------------------------------------------------------------------------

# aspect -> allowed values. "null" (not mentioned) is always allowed.
ASPECT_VALUES: dict[str, list[str]] = {
    "difficulty": ["easy", "moderate", "hard"],
    "workload": ["light", "moderate", "heavy"],
    "grading": ["lenient", "fair", "harsh"],
    "exams": ["easy", "fair", "hard"],
    "lectures": ["clear", "mixed", "unclear"],
    "helpfulness": ["helpful", "mixed", "unhelpful"],
    "curve": ["yes", "no"],
    "attendance_required": ["yes", "no"],
    "recommend": ["yes", "mixed", "no"],
}

# Keyword fallback: detects *mentions* of a topic with a fixed polarity only
# where the phrase itself carries it. Deliberately conservative: a missed
# mention is better than a wrong count.
_LEXICON: dict[str, list[tuple[str, str]]] = {
    "curve": [
        (r"\b(curves?|curved|curving)\b(?!\s+ball)", "yes"),
        (r"\b(no|doesn'?t|does not|never|won'?t)\s+(\w+\s+)?curve", "no"),
    ],
    "attendance_required": [
        (r"\battendance (is )?(mandatory|required|matters|counts)\b", "yes"),
        (r"\b(takes|checks) attendance\b", "yes"),
        (r"\battendance (is )?(not|isn'?t) (mandatory|required)\b", "no"),
    ],
    "workload": [
        (r"\b(heavy|lots of|a lot of|tons of) (homework|work|assignments|projects)\b", "heavy"),
        (r"\b(time[- ]consuming|workload is (heavy|high|insane))\b", "heavy"),
        (r"\b(light|little|not much) (homework|work|workload)\b", "light"),
    ],
    "exams": [
        (r"\b(exams?|tests?|quizzes|midterms?|finals?) (are|were|is|was) (very |really |extremely |super )?(hard|difficult|tough|brutal)\b", "hard"),
        (r"\b(hard|difficult|tough) (exams?|tests?)\b", "hard"),
        (r"\b(exams?|tests?) (are|were|is|was) (pretty |really |very )?(easy|fair|straightforward)\b", "easy"),
    ],
    "helpfulness": [
        (r"\b(very |extremely |super |really )?helpful\b", "helpful"),
        (r"\boffice hours\b.{0,40}\b(great|helpful|useful)\b", "helpful"),
        (r"\b(unhelpful|not helpful|won'?t help)\b", "unhelpful"),
    ],
    "recommend": [
        (r"\b(highly recommend|would (definitely )?take (him|her|them) again|take (him|her|them)!)\b", "yes"),
        (r"\b(avoid|do not take|don'?t take|would not recommend|wouldn'?t recommend)\b", "no"),
    ],
}


def lexicon_aspects(text: str) -> dict[str, str]:
    """Keyword-based aspect tags. 'no'/'unhelpful' patterns win over 'yes'."""
    t = text.lower()
    tags: dict[str, str] = {}
    for aspect, patterns in _LEXICON.items():
        for pattern, value in patterns:
            if re.search(pattern, t):
                # Later patterns in each list are negations: let them override.
                tags[aspect] = value
    return tags


@lru_cache(maxsize=1)
def load_llm_aspects() -> dict[str, dict]:
    """chunk_id -> aspect dict from aspects.py, or {} if never generated."""
    path = Path(settings.DATA_DIR) / "aspects.jsonl"
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                out[row["chunk_id"]] = row["aspects"]
    return out


def aspect_method() -> str:
    return "llm" if load_llm_aspects() else "lexicon"


# ---------------------------------------------------------------------------
# Review records
# ---------------------------------------------------------------------------

_NON_GRADES = {"", "n/a", "not sure yet", "rather not say", "audit/no grade",
               "drop/withdrawal", "incomplete", "unknown"}


def _rating(value: str) -> float | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*5", value or "")
    return float(m.group(1)) if m else None


def _body_key(text: str) -> str:
    return hashlib.md5(re.sub(r"\W+", " ", text.lower()).strip().encode()).hexdigest()


@lru_cache(maxsize=1)
def reviews() -> list[dict]:
    """
    One record per *unique* review. The same review text is often posted to
    both RMP and Coursicle; both chunks are kept for retrieval (they carry
    different metadata), but counting them twice would inflate every stat.
    Duplicates are merged, keeping the union of their metadata.
    """
    llm = load_llm_aspects()
    merged: dict[tuple[str, str], dict] = {}
    for c in load_chunks():
        meta = c["metadata"]
        if meta.get("chunk_type") != "review":
            continue
        prof = meta.get("professor", "")
        if not prof or prof.lower() == "unknown":
            continue
        key = (prof, _body_key(c["text"]))
        rec = merged.get(key)
        if rec is None:
            rec = merged[key] = {
                "chunk_ids": [], "professor": prof, "text": c["text"],
                "courses": set(), "sources": set(), "quality": None,
                "difficulty": None, "grade": None, "date": None, "aspects": {},
            }
        rec["chunk_ids"].append(c["id"])
        rec["courses"].update(chunk_courses(meta))
        rec["sources"].add(meta.get("source_dir", ""))
        rec["quality"] = rec["quality"] or _rating(meta.get("quality", ""))
        rec["difficulty"] = rec["difficulty"] or _rating(meta.get("difficulty", ""))
        grade = (meta.get("grade") or "").strip()
        if grade.lower() not in _NON_GRADES:
            rec["grade"] = grade
        if meta.get("date") and meta["date"] != "unknown":
            rec["date"] = meta["date"]
        rec["aspects"] = llm.get(c["id"]) or rec["aspects"] or lexicon_aspects(c["text"])
    return list(merged.values())


def _select(professor: str | None, course: str | None) -> list[dict]:
    return [
        r for r in reviews()
        if (professor is None or r["professor"] == professor)
        and (course is None or course in r["courses"])
    ]


def _grade_bucket(grade: str) -> str:
    g = grade.upper().strip()
    return g[0] if g and g[0] in "ABCDF" else "other"


def compute_stats(professor: str | None = None, course: str | None = None) -> dict:
    """
    Aggregate stats for a professor, a course, or a professor+course pair.
    Every number carries its denominator, so the synthesizer can't turn
    "3 of 4" into "most students".
    """
    rows = _select(professor, course)
    qualities = [r["quality"] for r in rows if r["quality"] is not None]
    difficulties = [r["difficulty"] for r in rows if r["difficulty"] is not None]
    grades = Counter(_grade_bucket(r["grade"]) for r in rows if r["grade"])

    aspects: dict[str, dict] = {}
    for aspect in ASPECT_VALUES:
        counts = Counter(r["aspects"].get(aspect) for r in rows)
        counts.pop(None, None)
        counts.pop("null", None)
        if counts:
            aspects[aspect] = {"mentioned_in": sum(counts.values()), "values": dict(counts)}

    courses = Counter(c for r in rows for c in r["courses"])
    by_prof = Counter(r["professor"] for r in rows) if professor is None else None

    return {
        "professor": professor,
        "course": course,
        "review_count": len(rows),
        "sources": dict(Counter(s for r in rows for s in r["sources"])),
        "avg_quality": round(sum(qualities) / len(qualities), 2) if qualities else None,
        "quality_n": len(qualities),
        "avg_difficulty": round(sum(difficulties) / len(difficulties), 2) if difficulties else None,
        "difficulty_n": len(difficulties),
        "grades": dict(grades),
        "grades_n": sum(grades.values()),
        "aspects": aspects,
        "aspect_method": aspect_method(),
        "courses": dict(courses.most_common()),
        "professors": dict(by_prof.most_common()) if by_prof else None,
    }


def professors_for_course(course: str) -> list[dict]:
    """Every professor with reviews for a course, with their per-course stats."""
    counts = Counter(r["professor"] for r in _select(None, course))
    return [compute_stats(p, course) for p, _ in counts.most_common()]


def stats_to_text(s: dict) -> str:
    """Render a stats dict as compact evidence text for the LLM."""
    who = " / ".join(x for x in (s["professor"], s["course"]) if x) or "all"
    lines = [f"STATS for {who}: {s['review_count']} unique reviews"]
    if s["avg_quality"] is not None:
        lines.append(f"- RMP quality: {s['avg_quality']}/5 (n={s['quality_n']})")
    if s["avg_difficulty"] is not None:
        lines.append(f"- RMP difficulty: {s['avg_difficulty']}/5 (n={s['difficulty_n']})")
    if s["grades_n"]:
        g = ", ".join(f"{k}: {v}" for k, v in sorted(s["grades"].items()))
        lines.append(f"- Self-reported grades (n={s['grades_n']}): {g}")
    for aspect, info in s["aspects"].items():
        vals = ", ".join(f"{v} {k}" for k, v in info["values"].items())
        lines.append(f"- {aspect}: mentioned in {info['mentioned_in']} of "
                     f"{s['review_count']} reviews ({vals})")
    if s.get("professors"):
        lines.append("- Reviews by professor: " + ", ".join(
            f"{p} ({n})" for p, n in s["professors"].items()))
    lines.append(f"(aspect tags: {s['aspect_method']})")
    return "\n".join(lines)


def aggregate_review_ids() -> dict[str, list[str]]:
    """professor -> chunk ids (used by the aspects extractor for batching)."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in reviews():
        out[r["professor"]].append(r["chunk_ids"][0])
    return dict(out)
