"""
crawl.py
========
A small, polite crawler for the knowledge-base sources, built on the
advising browser (advising/web.py), so it inherits the same guarantees:
HTTPS only, TXST hosts only, every redirect re-checked, size/time limits.

On top of that it:
  - obeys robots.txt (per host, fetched once)
  - stays on the seed hosts of the source being crawled
  - only follows links whose URL or anchor text matches the source's
    `follow` keywords, breadth-first to `max_depth`, up to `max_pages`
  - treats seeds as hubs: they're crawled through, never indexed, so a
    source's seeds should be index/landing pages
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlparse
from urllib.robotparser import RobotFileParser

from ..advising.web import Browser, FetchError, Page, is_allowed
from .sources import SourceSpec

USER_AGENT = "BobcatAdvisor"
_SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|svg|webp|ico|zip|docx?|xlsx?|pptx?|mp4|mp3|css|js)(\?|$)",
                       re.IGNORECASE)


@dataclass
class CrawlResult:
    source: str
    pages: list[tuple[Page, int]] = field(default_factory=list)   # (page, depth)
    errors: list[str] = field(default_factory=list)
    skipped_robots: int = 0
    visited: int = 0


class Crawler:
    def __init__(self, browser: Browser | None = None):
        self.browser = browser or Browser(max_pages=10_000)
        self._robots: dict[str, RobotFileParser | None] = {}

    def allowed_by_robots(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        if host not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                page = self.browser.fetch(f"https://{host}/robots.txt")
                parser.parse(page.raw.splitlines() if isinstance(page.raw, str) else [])
            except FetchError:
                parser = None  # no robots.txt (or unreachable): nothing disallowed
            self._robots[host] = parser
        parser = self._robots[host]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    @staticmethod
    def matches(spec: SourceSpec, url: str, text: str = "") -> bool:
        hay = f"{url} {text}".lower()
        return any(k.lower() in hay for k in spec.follow)

    def crawl(self, spec: SourceSpec) -> CrawlResult:
        result = CrawlResult(source=spec.id)
        hosts = {urlparse(s).hostname for s in spec.seeds}
        queue: deque[tuple[str, int]] = deque((s, 0) for s in spec.seeds)
        seen: set[str] = set()
        while queue and len(result.pages) < spec.max_pages:
            url, depth = queue.popleft()
            url = urldefrag(url)[0]
            if url in seen:
                continue
            seen.add(url)
            if not self.allowed_by_robots(url):
                result.skipped_robots += 1
                continue
            try:
                page = self.browser.fetch(url)
            except FetchError as e:
                result.errors.append(f"{url}: {e}")
                continue
            result.visited += 1
            if depth > 0:
                result.pages.append((page, depth))
            if depth >= spec.max_depth:
                continue
            for text, href in page.parsed.links:
                href = urldefrag(href)[0]
                if (href in seen or not is_allowed(href) or urlparse(href).hostname not in hosts
                        or _SKIP_EXT.search(href)):
                    continue
                if self.matches(spec, href, text):
                    queue.append((href, depth + 1))
        return result
