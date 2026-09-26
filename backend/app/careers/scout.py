"""
scout.py
========
Finds outside courses and certifications for a career, and checks every
link before it's recommended.

Resource scout (agentic search)
    For each skill the TXST catalog doesn't cover, plus each core skill
    worth going deeper on, it builds a search query and asks the web search
    provider (Brave Search API, when SEARCH_PROVIDER=brave and a key is
    set). Results are kept only on hosts from the seed list: the scout finds
    current pages on providers it knows, it doesn't recommend random sites.
    The curated seeds for each skill are always candidates too, so the
    feature works with no search key.

Link checker
    Opens every candidate with the sandboxed browser (HTTPS, allowlisted
    hosts only, redirects re-checked, page budget) and reads its title and
    description:
      verified   the page loaded and talks about the skill
      unchecked  a curated seed whose site refused an automated check
                 (403/429) or couldn't be reached: kept, labelled as such
      dropped    a dead link (404/410), an off-topic page, or a search
                 result that couldn't be opened
    Pages are fetched concurrently; each fetch is a traced span.

Page text is untrusted and is never sent to an LLM here: relevance is a
keyword check against the skill's terms.
"""

from __future__ import annotations

import contextvars
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import httpx

from ..advising.web import Browser, FetchError
from ..config import settings
from ..tracing import span
from .paths import Resource, Skill, allowed_domains, resources, skills

MAX_SEARCH_QUERIES = 6
PER_SKILL = 3                      # candidates checked per skill
KEEP_PER_SKILL = 2                 # resources shown per skill


# ---------------------------------------------------------------------------
# Search provider
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str


Searcher = Callable[[str], list[SearchHit]]


def _brave(query: str) -> list[SearchHit]:
    resp = httpx.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": 10, "safesearch": "strict"},
                     headers={"Accept": "application/json",
                              "X-Subscription-Token": settings.BRAVE_API_KEY or ""},
                     timeout=settings.WEB_TIMEOUT_S)
    resp.raise_for_status()
    return [SearchHit(r.get("url", ""), r.get("title", ""), r.get("description", ""))
            for r in (resp.json().get("web") or {}).get("results", [])]


_searcher: Searcher | None = None


def set_searcher(fn: Searcher | None) -> None:
    """Tests and offline runs swap the search provider."""
    global _searcher
    _searcher = fn


def searcher() -> Searcher | None:
    if _searcher is not None:
        return _searcher
    if settings.SEARCH_PROVIDER == "brave" and settings.BRAVE_API_KEY:
        return _brave
    return None


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    title: str
    provider: str
    url: str
    kind: str
    cost: str
    skills: list[str]
    source: str                    # seed | search
    careers: tuple[str, ...] = ()
    purpose: str = ""              # gap | deeper | certification
    for_skill: str = ""            # the skill it was found for
    check: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"title": self.title, "provider": self.provider, "url": self.url, "kind": self.kind,
                "cost": self.cost, "skills": [skills()[s].name for s in self.skills],
                "source": self.source, "purpose": self.purpose,
                "for_skill": skills()[self.for_skill].name if self.for_skill else "",
                "check": self.check}


def _normal(url: str) -> str:
    u = urlparse(url)
    return urlunparse((u.scheme, (u.hostname or "").lower(), u.path.rstrip("/") or "/", "", "", ""))


def _from_seed(r: Resource, purpose: str, for_skill: str = "") -> Candidate:
    return Candidate(r.title, r.provider, r.url, r.kind, r.cost, list(r.skills), "seed",
                     r.careers, purpose, for_skill)


def _query(skill: Skill, career_title: str) -> str:
    return f"{skill.name} online course for {career_title.lower()}s"


def _kind_of(hit: SearchHit) -> str:
    text = f"{hit.title} {hit.url}".lower()
    return "certification" if re.search(r"certif", text) else "course"


def gather(targets: list[tuple[str, str]], career_id: str, career_title: str, *,
           include_certifications: bool, free_only: bool,
           on_search: Callable[[dict], None] | None = None) -> list[Candidate]:
    """
    targets: (skill_id, purpose) pairs, most important first, where purpose
    is "gap" (the catalog doesn't teach it) or "deeper" (it does; go further).
    """
    seen: set[str] = set()
    out: list[Candidate] = []

    def add(c: Candidate) -> None:
        key = _normal(c.url)
        if key in seen or (free_only and c.cost == "paid"):
            return
        seen.add(key)
        out.append(c)

    search = searcher()
    for i, (skill_id, purpose) in enumerate(targets):
        skill = skills()[skill_id]
        picked = 0
        if search and i < MAX_SEARCH_QUERIES:
            query = _query(skill, career_title)
            with span("career.search", query=query) as s:
                try:
                    hits = search(query)
                except Exception as e:           # provider down: seeds still work
                    hits = []
                    s.attributes["error"] = f"{type(e).__name__}: {e}"[:200]
                kept = [h for h in hits if urlparse(h.url).hostname
                        and any(urlparse(h.url).hostname.lower() == d
                                or urlparse(h.url).hostname.lower().endswith("." + d)
                                for d in allowed_domains())]
                s.attributes.update(results=len(hits), kept=len(kept))
            if on_search:
                on_search({"query": query, "results": len(hits), "kept": len(kept)})
            known = {_normal(r.url): r for r in resources()}
            for h in kept[:PER_SKILL]:
                seed = known.get(_normal(h.url))
                if seed:
                    add(_from_seed(seed, purpose, skill_id))
                else:
                    host = (urlparse(h.url).hostname or "").removeprefix("www.")
                    add(Candidate(h.title[:120] or host, host, h.url, _kind_of(h), "check site",
                                  [skill_id], "search", (), purpose, skill_id))
                picked += 1
        for r in resources():
            if picked >= PER_SKILL:
                break
            if skill_id in r.skills and r.kind != "certification":
                before = len(out)
                add(_from_seed(r, purpose, skill_id))
                picked += len(out) > before

    if include_certifications:
        for r in resources():
            if career_id in r.careers:
                add(_from_seed(r, "certification"))
    return out


# ---------------------------------------------------------------------------
# Link checking
# ---------------------------------------------------------------------------

_DEAD = re.compile(r"HTTP (404|410)\b")
_REFUSED = re.compile(r"HTTP (401|403|429|451)\b")


def _unchecked_reason(error: str) -> str:
    if _REFUSED.search(error):
        return "the site refused an automated check"
    if "budget" in error or "browsing is disabled" in error:
        return "not checked this time"
    return "couldn't reach the site to check it"


def _relevant(candidate: Candidate, title: str, description: str, text: str) -> tuple[bool, str]:
    hay = f" {title} {description} {text[:4000]} ".lower()
    for skill_id in candidate.skills:
        for term in skills()[skill_id].terms:
            if term.lower() in hay:
                return True, term.strip()
    if candidate.kind == "certification" and "certif" in hay:
        return True, "certification"
    return False, ""


def check(candidates: list[Candidate], browser: Browser, workers: int = 6) -> tuple[list[Candidate], list[dict]]:
    """Open every candidate; returns (kept, dropped)."""
    today = time.strftime("%Y-%m-%d")

    def one(c: Candidate) -> None:
        try:
            page = browser.fetch(c.url)
        except FetchError as e:
            reason = str(e)
            # A curated link is only dropped when it's provably dead; a site that
            # refuses bots or a network hiccup isn't evidence. Search results
            # have no such trust and must open to be shown.
            if c.source == "seed" and not _DEAD.search(reason):
                c.check = {"status": "unchecked", "reason": _unchecked_reason(reason),
                           "detail": reason[:120]}
            else:
                c.check = {"status": "dropped", "reason": f"link failed: {reason[:120]}"}
            return
        ok, term = _relevant(c, page.title, page.parsed.description, page.text)
        if not ok:
            c.check = {"status": "dropped", "reason": "page isn't about this skill"}
            return
        c.check = {"status": "verified", "checked_on": today, "page_title": page.title[:140],
                   "matched": term, "final_url": page.url}
        if c.source == "search" and page.title:
            c.title = page.title[:120]
        if c.source == "search" and page.parsed.description:
            c.check["summary"] = page.parsed.description[:240]

    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(lambda c: ctx.copy().run(one, c), candidates))
    kept = [c for c in candidates if c.check.get("status") != "dropped"]
    dropped = [{"url": c.url, "title": c.title, "reason": c.check.get("reason", "")}
               for c in candidates if c.check.get("status") == "dropped"]
    return kept, dropped


def select(kept: list[Candidate]) -> dict[str, list[dict]]:
    """Best per skill (verified first, then free), grouped for the UI."""
    rank = {"verified": 0, "unchecked": 1}
    cost_rank = {"free": 0, "free to audit": 1, "free tier": 2, "check site": 3, "paid": 4}
    groups: dict[str, list[dict]] = {"gap": [], "deeper": [], "certification": []}
    per_skill: dict[tuple[str, str], int] = {}
    # Verified first; then resources mainly about the skill (Made With ML for
    # MLOps before a general course that touches it), curated, cheaper.
    for c in sorted(kept, key=lambda c: (rank.get(c.check.get("status"), 2),
                                         bool(c.for_skill) and c.skills[0] != c.for_skill,
                                         c.source != "seed", cost_rank.get(c.cost, 3))):
        if c.purpose == "certification":
            groups["certification"].append(c.to_dict())
            continue
        key = (c.purpose, c.for_skill)
        if per_skill.get(key, 0) >= KEEP_PER_SKILL:
            continue
        per_skill[key] = per_skill.get(key, 0) + 1
        groups[c.purpose].append(c.to_dict())
    return groups
