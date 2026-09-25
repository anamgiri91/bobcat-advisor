"""
sections.py
===========
Splits a page into sections at its headings, keeping the heading path
("Academic Policies > Withdrawal > Deadlines") so every chunk says exactly
where it came from.

Policy text reads section by section, so a chunk boundary belongs at a
heading, not every N words: a fixed window can cut "students may not drop
after..." away from the heading that says which students.

HTML: h1-h4 open sections. Navigation, headers, footers, sidebars, scripts
and forms are dropped; when the page has a main-content container
(<main>, #content, #textcontainer, ...) only its text is kept. List items
become "- " lines and table rows "cell | cell" lines, so calendars and
requirement tables stay readable.

PDF (syllabi): there are no heading tags, so short lines that look like
headings (ALL CAPS, "Title:", or a known syllabus heading) open sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "header", "footer",
              "aside", "form", "button", "select", "iframe"}
_SKIP_MARKERS = ("nav", "breadcrumb", "footer", "header", "menu", "sidebar", "skip", "cookie",
                 "search", "social")
_MAIN_IDS = {"main", "content", "main-content", "maincontent", "textcontainer", "page-content"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
_BLOCK = {"p", "div", "li", "tr", "table", "section", "article", "ul", "ol", "dt", "dd",
          "blockquote", "h5", "h6", "pre"}


@dataclass
class Section:
    path: list[str]                  # heading path, outermost first
    text: str
    level: int = 0

    @property
    def heading(self) -> str:
        return self.path[-1] if self.path else ""

    @property
    def path_str(self) -> str:
        return " > ".join(self.path)


@dataclass
class _Out:
    in_main: bool
    kind: str                        # "heading" | "text"
    text: str
    level: int = 0


@dataclass
class _Frame:
    tag: str
    skip: bool
    main: bool
    cells: list[str] = field(default_factory=list)


class _SectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Frame] = []
        self.out: list[_Out] = []
        self.title = ""
        self._in_title = False
        self._heading: tuple[int, list[str]] | None = None
        self._cell: list[str] | None = None
        self._buf: list[str] = []
        self.saw_main = False

    # -- state helpers ------------------------------------------------------
    @property
    def skipping(self) -> bool:
        return any(f.skip for f in self.stack)

    @property
    def in_main(self) -> bool:
        return any(f.main for f in self.stack)

    def _flush(self) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.out.append(_Out(self.in_main, "text", text))

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
            return
        if tag in _VOID:
            if tag == "br" and not self.skipping:
                self._buf.append("\n")
            return
        a = dict(attrs)
        marker = f"{a.get('id') or ''} {a.get('class') or ''} {a.get('role') or ''}".lower()
        in_main = self.in_main
        # A <header> inside the main content usually holds the page title.
        skip = (tag in _SKIP_TAGS and not (tag == "header" and in_main)) or (
            tag in ("div", "section", "ul") and
                                     any(m in marker.split() or f"{m}-" in marker or f"-{m}" in marker
                                         for m in _SKIP_MARKERS))
        main = tag == "main" or (a.get("id") or "").lower() in _MAIN_IDS or a.get("role") == "main"
        if main:
            self.saw_main = True
        self.stack.append(_Frame(tag, skip, main))
        if self.skipping:
            return
        if tag in _HEADINGS:
            self._flush()
            self._heading = (_HEADINGS[tag], [])
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "li":
            self._flush()
            self._buf.append("- ")
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if tag in _VOID:
            return
        # Pop to the matching open tag (tolerates unclosed <p>, <li>, ...).
        idx = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i].tag == tag), None)
        if idx is None:
            return
        was_skipping = self.skipping
        frame = self.stack[idx]
        if not was_skipping:
            if tag in _HEADINGS and self._heading is not None:
                level, parts = self._heading
                text = re.sub(r"\s+", " ", "".join(parts)).strip()
                if text:
                    self.out.append(_Out(self.in_main, "heading", text, level))
                self._heading = None
            elif tag in ("td", "th") and self._cell is not None:
                row = next((f for f in reversed(self.stack[:idx]) if f.tag == "tr"), None)
                cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
                if row is not None and cell:
                    row.cells.append(cell)
                self._cell = None
            elif tag == "tr":
                if frame.cells:
                    self._flush()
                    self.out.append(_Out(self.in_main, "text", " | ".join(frame.cells)))
            elif tag in _BLOCK or tag == "li":
                self._flush()
        del self.stack[idx:]

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skipping:
            return
        if self._heading is not None:
            self._heading[1].append(data)
        elif self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_sections(html: str, page_title: str = "") -> tuple[str, list[Section]]:
    """(page title, sections). Text before the first heading is filed under the title."""
    p = _SectionParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    title = re.sub(r"\s+", " ", p.title).strip() or page_title
    items = [o for o in p.out if o.in_main] if p.saw_main else p.out

    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    lines: list[str] = []

    def close_section():
        text = "\n".join(lines).strip()
        if text:
            sections.append(Section([h for _, h in path] or [title], text,
                                    path[-1][0] if path else 0))
        lines.clear()

    for o in items:
        if o.kind == "heading":
            close_section()
            while path and path[-1][0] >= o.level:
                path.pop()
            path.append((o.level, o.text))
        else:
            lines.append(o.text)
    close_section()
    return title, sections


# ---------------------------------------------------------------------------
# PDF text
# ---------------------------------------------------------------------------

_SYLLABUS_HEADINGS = re.compile(
    r"^(course (description|objectives|outcomes|schedule|policies|materials|information)|"
    r"learning (outcomes|objectives)|student learning outcomes|required (text|textbooks?|materials)|"
    r"textbooks?|materials|prerequisites?|grading( policy| scale)?|assessment|evaluation|"
    r"assignments?|projects?|exams?|examinations|final exam|attendance|late (work|policy)|"
    r"(tentative )?(weekly )?schedule|calendar|topics|instructor( information)?|contact information|"
    r"office hours|teaching assistant|academic (integrity|honesty)|honor code|accommodations?|"
    r"disability services|use of (ai|generative ai)|artificial intelligence)\s*:?$",
    re.IGNORECASE,
)


def _is_pdf_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 70 or len(s.split()) > 8 or s.endswith((".", ",", ";")):
        return False
    if _SYLLABUS_HEADINGS.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def text_sections(text: str, title: str) -> list[Section]:
    sections: list[Section] = []
    heading, lines = title, []
    for raw in text.splitlines():
        line = raw.strip()
        if _is_pdf_heading(line):
            if lines:
                sections.append(Section([title, heading] if heading != title else [title],
                                        "\n".join(lines)))
            heading, lines = line.rstrip(":").strip().title() if line.isupper() else line.rstrip(":"), []
        elif line:
            lines.append(line)
    if lines:
        sections.append(Section([title, heading] if heading != title else [title], "\n".join(lines)))
    return sections
