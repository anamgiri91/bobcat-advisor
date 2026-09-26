"""
web.py
======
The browser tool the research agent uses to read the live TXST catalog.

It is deliberately narrow:

  - HTTPS only, and only hosts under WEB_ALLOWED_DOMAINS (txstate.edu /
    txst.edu by default). Redirects are followed by hand so every hop is
    re-checked: an allowlisted page can't bounce the fetcher to an internal
    address or another site (SSRF).
  - A per-request page budget (WEB_MAX_PAGES), a timeout and a size cap, so a
    slow or huge page can't stall the advising pipeline.
  - A process-wide TTL cache: the catalog changes once a year, and every
    student asking about the same major would otherwise refetch it.
  - Everything fetched is untrusted text. The parser keeps structure (tables,
    course blocks, links) and drops scripts and styles; downstream agents
    treat page text as quoted data, never as instructions.

The TXST catalog runs on CourseLeaf, whose pages share a stable structure:
requirement tables are `table.sc_courselist` (rows with `td.codecol`,
`td.hourscol`, `tr.orclass` alternatives, `tr.areaheader` headings and
`span.courselistcomment` rules like "Select 6 hours from the following"),
suggested four-year plans are `table.sc_plangrid`, and course descriptions
are `div.courseblock`. parse_html() extracts exactly those.

Tests (and offline runs) swap the network with set_fetcher(), so parsing and
the whole pipeline are testable without a connection.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from ..tracing import span


class FetchError(RuntimeError):
    """The page could not be fetched: blocked, over budget, HTTP error or network failure."""


# ---------------------------------------------------------------------------
# URL policy
# ---------------------------------------------------------------------------

def is_allowed(url: str, domains: list[str] | tuple[str, ...] | None = None) -> bool:
    """HTTPS, default port, no credentials, host inside the allowlist
    (WEB_ALLOWED_DOMAINS unless a caller passes its own)."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        return False
    if u.port not in (None, 443):
        return False
    host = u.hostname.lower().rstrip(".")
    allowed = settings.WEB_ALLOWED_DOMAINS if domains is None else domains
    return any(host == d or host.endswith("." + d) for d in allowed)


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base"}
_SKIP = {"script", "style", "noscript", "svg", "template"}
_BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
          "br", "ul", "ol", "article", "header", "footer", "dt", "dd"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


@dataclass
class Cell:
    tag: str
    classes: set[str]
    text: str


@dataclass
class Row:
    classes: set[str]
    cells: list[Cell] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _clean(" ".join(c.text for c in self.cells))


@dataclass
class Table:
    classes: set[str]
    rows: list[Row] = field(default_factory=list)


@dataclass
class ParsedPage:
    title: str
    text: str
    links: list[tuple[str, str]]          # (anchor text, absolute href)
    tables: list[Table]
    blocks: list[str]                     # text of each div.courseblock
    description: str = ""                 # <meta name=description> / og:description


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.tables: list[Table] = []
        self.blocks: list[str] = []
        self._skip = 0
        self._in_title = False
        self._table: Table | None = None
        self._table_depth = 0
        self._row: Row | None = None
        self._cell: Cell | None = None
        self._cell_parts: list[str] = []
        self._link_href: str | None = None
        self._link_parts: list[str] = []
        self._block_depth = 0          # div nesting inside the current courseblock
        self._block_parts: list[str] = []
        self.description = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag in _SKIP:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if self._skip:
            return
        if tag == "meta" and not self.description:
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description"):
                self.description = _clean(a.get("content") or "")
            return
        if tag in _BLOCK:
            self._emit("\n")
        if tag == "table":
            if self._table is None:
                self._table = Table(classes=classes)
                self._table_depth = 1
            else:
                self._table_depth += 1
        elif tag == "tr" and self._table is not None and self._table_depth == 1:
            self._row = Row(classes=classes)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = Cell(tag=tag, classes=classes, text="")
            self._cell_parts = []
        elif tag == "span" and self._cell is not None and "courselistcomment" in classes:
            self._cell.classes.add("courselistcomment")
        elif tag == "a" and a.get("href"):
            self._link_href = urljoin(self.base_url, a["href"])
            self._link_parts = []
        elif tag == "div":
            if self._block_depth:
                self._block_depth += 1
            elif "courseblock" in classes:
                self._block_depth = 1
                self._block_parts = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if self._skip:
            return
        if tag in _BLOCK:
            self._emit("\n")
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell.text = _clean("".join(self._cell_parts))
            self._row.cells.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row.cells:
                self._table.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(self._table)
                self._table = None
        elif tag == "a" and self._link_href is not None:
            self.links.append((_clean("".join(self._link_parts)), self._link_href))
            self._link_href = None
        elif tag == "div" and self._block_depth:
            self._block_depth -= 1
            if self._block_depth == 0:
                self.blocks.append(_clean("".join(self._block_parts)))

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self._skip:
            self._emit(" ")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip:
            return
        self._emit(data)

    def _emit(self, s: str) -> None:
        self.text_parts.append(s)
        if self._cell is not None:
            self._cell_parts.append(s if s != "\n" else " ")
        if self._link_href is not None:
            self._link_parts.append(s)
        if self._block_depth:
            self._block_parts.append(s if s != "\n" else " ")


def pdf_text(data: bytes | str, max_pages: int = 40) -> str:
    """Plain text of a PDF (syllabi are often PDFs). Empty string if unreadable."""
    if isinstance(data, str):
        return data
    try:
        from io import BytesIO

        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages]).strip()
    except Exception:
        return ""


def parse_html(html: str, base_url: str = "") -> ParsedPage:
    p = _Parser(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed markup: keep whatever was parsed
        pass
    lines = [_clean(line) for line in "".join(p.text_parts).split("\n")]
    text = "\n".join(line for line in lines if line)
    return ParsedPage(title=_clean("".join(p.title_parts)), text=text, links=p.links,
                      tables=p.tables, blocks=[b for b in p.blocks if b], description=p.description)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

@dataclass
class Page:
    url: str
    status: int
    parsed: ParsedPage
    fetched_at: float
    raw: str = ""                     # HTML source, or extracted PDF text
    content_type: str = "text/html"

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type

    @property
    def text(self) -> str:
        return self.parsed.text

    @property
    def title(self) -> str:
        return self.parsed.title


class Fetcher(Protocol):
    def get(self, url: str, timeout_s: float) -> tuple[int, dict[str, str], str | bytes]:
        """(status, lower-cased headers, body: str, or bytes for PDFs). Must NOT follow redirects."""
        ...


class HttpxFetcher:
    def __init__(self) -> None:
        self._client = httpx.Client(
            follow_redirects=False,
            headers={"User-Agent": "BobcatAdvisor/1.0 (student course-planning assistant)",
                     "Accept": "text/html,application/xhtml+xml"},
        )

    def get(self, url, timeout_s):
        try:
            with self._client.stream("GET", url, timeout=timeout_s) as resp:
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > settings.WEB_MAX_BYTES:
                        raise FetchError(f"page larger than {settings.WEB_MAX_BYTES} bytes")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if "pdf" in headers.get("content-type", ""):
                    return resp.status_code, headers, body     # bytes; see pdf_text()
                return resp.status_code, headers, body.decode(resp.encoding or "utf-8", "replace")
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__}: {e}") from e


_fetcher: Fetcher | None = None
_fetcher_lock = threading.Lock()


def set_fetcher(fetcher: Fetcher | None) -> None:
    """Swap the network layer (tests, offline evals). None restores httpx."""
    global _fetcher
    with _fetcher_lock:
        _fetcher = fetcher
    clear_cache()


def _get_fetcher() -> Fetcher:
    global _fetcher
    with _fetcher_lock:
        if _fetcher is None:
            _fetcher = HttpxFetcher()
        return _fetcher


class _TTLCache:
    def __init__(self, size: int = 256):
        self.size = size
        self._data: OrderedDict[str, Page] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> Page | None:
        with self._lock:
            page = self._data.get(url)
            if page is None:
                return None
            if time.time() - page.fetched_at > settings.WEB_CACHE_TTL_S:
                del self._data[url]
                return None
            self._data.move_to_end(url)
            return page

    def put(self, page: Page) -> None:
        with self._lock:
            self._data[page.url] = page
            self._data.move_to_end(page.url)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _TTLCache()


def clear_cache() -> None:
    _cache.clear()


@dataclass
class Visit:
    url: str
    ok: bool
    status: int | None
    ms: float
    cached: bool
    error: str | None = None
    title: str = ""

    def to_dict(self) -> dict:
        return {"url": self.url, "ok": self.ok, "status": self.status, "ms": round(self.ms),
                "cached": self.cached, "error": self.error, "title": self.title}


class Browser:
    """
    One advising request's view of the web: enforces the allowlist and page
    budget, records every visit (shown live in the UI and kept in the
    trace), and reports visits to `on_visit` as they happen.
    """

    def __init__(self, max_pages: int | None = None,
                 on_visit: Callable[[Visit], None] | None = None,
                 allowed_domains: list[str] | tuple[str, ...] | None = None):
        self.max_pages = max_pages if max_pages is not None else settings.WEB_MAX_PAGES
        self.on_visit = on_visit
        self.allowed_domains = allowed_domains          # None = WEB_ALLOWED_DOMAINS
        self.visits: list[Visit] = []
        self.pages: dict[str, Page] = {}
        self._network_fetches = 0
        self._lock = threading.Lock()                   # fetch() may run on several threads

    def allows(self, url: str) -> bool:
        return is_allowed(url, self.allowed_domains)

    @property
    def enabled(self) -> bool:
        return settings.WEB_BROWSING_ENABLED

    def _record(self, visit: Visit) -> None:
        with self._lock:
            self.visits.append(visit)
        if self.on_visit:
            self.on_visit(visit)

    def fetch(self, url: str) -> Page:
        if not self.enabled:
            raise FetchError("web browsing is disabled (WEB_BROWSING_ENABLED=false)")
        t0 = time.perf_counter()
        if not self.allows(url):
            self._record(Visit(url, False, None, 0, False, "blocked: not an allowed address"))
            raise FetchError(f"blocked URL: {url}")
        cached = _cache.get(url)
        if cached is not None:
            self.pages[url] = cached
            self._record(Visit(url, True, cached.status, (time.perf_counter() - t0) * 1000, True,
                               title=cached.title))
            return cached
        with self._lock:
            over_budget = self._network_fetches >= self.max_pages
            if not over_budget:
                self._network_fetches += 1
        if over_budget:
            self._record(Visit(url, False, None, 0, False, "page budget used"))
            raise FetchError(f"page budget ({self.max_pages}) used")

        with span("web.fetch", url=url) as s:
            current = url
            try:
                for _ in range(4):
                    status, headers, body = _get_fetcher().get(current, settings.WEB_TIMEOUT_S)
                    if status in (301, 302, 303, 307, 308) and headers.get("location"):
                        nxt = urljoin(current, headers["location"])
                        if not self.allows(nxt):
                            raise FetchError(f"redirect to a non-allowed address blocked: {nxt}")
                        current = nxt
                        continue
                    break
                else:
                    raise FetchError("too many redirects")
                if status >= 400:
                    raise FetchError(f"HTTP {status}")
                ctype = headers.get("content-type", "text/html")
                if "html" not in ctype and "text" not in ctype and "pdf" not in ctype:
                    raise FetchError(f"not an HTML or PDF page ({ctype})")
                if "pdf" in ctype:
                    body = pdf_text(body)
            except FetchError as e:
                s.attributes["error"] = str(e)
                self._record(Visit(url, False, None, (time.perf_counter() - t0) * 1000, False,
                                   str(e)))
                raise
            if "pdf" in ctype:
                parsed = ParsedPage(title=current.rsplit("/", 1)[-1], text=body, links=[],
                                    tables=[], blocks=[])
            else:
                parsed = parse_html(body, current)
            page = Page(url=current, status=status, parsed=parsed, fetched_at=time.time(),
                        raw=body, content_type=ctype)
            s.attributes.update(status=status, chars=len(page.text))

        _cache.put(page)
        if current != url:
            _cache.put(Page(url, page.status, page.parsed, page.fetched_at, page.raw,
                            page.content_type))
        with self._lock:
            self.pages[url] = page
        self._record(Visit(url, True, status, (time.perf_counter() - t0) * 1000, False,
                           title=page.title))
        return page
