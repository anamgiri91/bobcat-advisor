"""
build.py
========
Crawls the knowledge-base sources and writes:

  data/kb_chunks.jsonl    one chunk per page section, with metadata
  data/kb_dates.jsonl     dated facts (deadlines, registration windows)
  data/kb_manifest.json   per-source fetch date, counts and errors

then `bash scripts/build_index.sh` merges the chunks into the search index
(app/rag/ingest.py picks up kb_chunks.jsonl) and embeds only what changed.

Every chunk carries: kind (chunk_type), source id, URL, page title, heading
path, catalog year (catalog pages), fetch date, and course codes (syllabi).
Its text starts with "Title — Heading > Subheading" so the heading words are
searchable and the citation label is exact.

Usage (from backend/):
  python -m app.kb.build                       # all sources
  python -m app.kb.build --sources registrar   # one source; others kept as-is
  python -m app.kb.build --check-freshness     # exit 1 if a source is overdue
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

from ..advising.profile import normalise_codes
from ..advising.research import catalog_year_of
from ..advising.web import Browser, Page
from ..config import settings
from ..tracing import span
from .crawl import Crawler
from .dates import DateFact, extract_dates
from .scrub import is_people_section, scrub_contacts, scrub_people
from .sections import Section, html_sections, text_sections
from .sources import SourceSpec, load_sources

MIN_WORDS = 25        # smaller sections are merged into the previous one (else the next)
MAX_WORDS = 350       # larger sections are split at paragraph boundaries


def _paths() -> tuple[Path, Path, Path]:
    d = Path(settings.DATA_DIR)
    return d / "kb_chunks.jsonl", d / "kb_dates.jsonl", d / "kb_manifest.json"


def _words(text: str) -> int:
    return len(text.split())


def _merge_and_split(sections: list[Section]) -> list[Section]:
    """Fold a tiny section into its parent heading's section (e.g. a short
    "Deadlines" under "Withdrawal"); page-intro text before the first heading
    goes into the next section. Siblings are never merged: each keeps its own
    heading. Long sections are split at paragraph boundaries."""
    merged: list[Section] = []
    carry: list[str] = []
    for s in sections:
        prev = merged[-1] if merged else None
        is_child = prev is not None and len(prev.path) < len(s.path) and s.path[:len(prev.path)] == prev.path
        if _words(s.text) < MIN_WORDS and is_child:
            merged[-1] = Section(prev.path, f"{prev.text}\n{s.heading}: {s.text}", prev.level)
            continue
        if _words(s.text) < MIN_WORDS and len(s.path) <= 1 and s is not sections[-1]:
            carry.append(s.text)            # intro text under the page title only
            continue
        merged.append(Section(s.path, "\n".join(carry + [s.text]), s.level))
        carry = []
    if carry:
        merged.append(Section(sections[-1].path, "\n".join(carry)))
    out: list[Section] = []
    for s in merged:
        if _words(s.text) <= MAX_WORDS:
            out.append(s)
            continue
        part, n = [], 1
        for para in re.split(r"\n+", s.text):
            if part and _words("\n".join(part + [para])) > MAX_WORDS:
                out.append(Section(s.path + [f"part {n}"], "\n".join(part), s.level))
                part, n = [], n + 1
            part.append(para)
        if part:
            out.append(Section(s.path + ([f"part {n}"] if n > 1 else []), "\n".join(part), s.level))
    return out


def page_chunks(page: Page, spec: SourceSpec, fetched: str) -> tuple[list[dict], list[DateFact], dict]:
    """Chunks and dated facts for one page, plus counters for the report."""
    stats = {"sections": 0, "people_sections_dropped": 0, "people_details_removed": 0}
    if page.is_pdf:
        text = page.raw if isinstance(page.raw, str) else page.text
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first.title() if first.isupper() else first
        title = re.sub(r"\b([A-Z][a-z]{1,3}) (\d{4})", lambda m: f"{m.group(1).upper()} {m.group(2)}", title)
        title = title if 0 < len(title) <= 100 else page.title
        sections = text_sections(text, title)
    else:
        title, sections = html_sections(page.raw or "", page.title)
    title = re.split(r"\s+[<|]\s+", title)[0].strip() or page.url

    courses: list[str] = []
    if spec.kind == "syllabus":
        courses = normalise_codes(f"{title} {page.url} {' '.join(s.text for s in sections[:2])}")
        if spec.only_courses_with_prefix:
            courses = [c for c in courses if c.startswith(spec.only_courses_with_prefix)]
            if not courses:
                return [], [], stats   # another department's syllabus
        courses = courses[:1]      # the syllabus's own course, not ones it mentions

    kept: list[Section] = []
    for s in sections:
        stats["sections"] += 1
        if spec.scrub_people and is_people_section(s.heading):
            stats["people_sections_dropped"] += 1
            continue
        if spec.scrub_people:
            text, removed = scrub_people(s.text)
            stats["people_details_removed"] += removed
        else:
            text = scrub_contacts(s.text)
        if text.strip():
            kept.append(Section(s.path, text, s.level))

    catalog_year = catalog_year_of(page) if spec.catalog_year else None
    # Dates come from each original section, so every date keeps its own
    # heading (and therefore its own term).
    facts: list[DateFact] = []
    for s in kept:
        facts.extend(extract_dates(s.text, f"{title} > {s.path_str}", page.url, fetched))

    chunks = []
    for s in _merge_and_split(kept):
        # Drop the page title from the header when the top heading repeats it.
        same = bool(s.path) and s.path[0].lower().split()[:3] == title.lower().split()[:3]
        path = s.path_str
        header = path if same else (f"{title} — {path}" if path else title)
        text = f"{header}\n\n{s.text}"
        chunks.append({
            "id": hashlib.md5(f"{page.url}\n{text}".encode()).hexdigest(),
            "text": text,
            "metadata": {
                "chunk_type": spec.kind,
                "source_dir": "web",
                "source": spec.id,
                "url": page.url,
                "title": title[:200],
                "heading": (s.heading or title)[:200],
                "section_path": path[:300],
                "catalog_year": catalog_year or "",
                "fetched_at": fetched,
                "course": courses[0] if courses else "",
                "courses": "|".join(courses),
            },
        })
    return chunks, facts, stats


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def build(source_ids: list[str] | None = None, crawler: Crawler | None = None,
          today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    chunks_path, dates_path, manifest_path = _paths()
    specs = [s for s in load_sources() if not source_ids or s.id in source_ids]
    rebuilt = {s.id for s in specs}

    # Rebuilding some sources keeps the others' chunks and dates.
    chunks = [c for c in _load_jsonl(chunks_path) if c["metadata"]["source"] not in rebuilt]
    dates = [d for d in _load_jsonl(dates_path)
             if not any(d.get("source") == sid for sid in rebuilt)]
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    crawler = crawler or Crawler(Browser(max_pages=10_000))
    for spec in specs:
        with span("kb.source", source=spec.id) as sp:
            result = crawler.crawl(spec)
            n_chunks = n_dates = 0
            totals = {"sections": 0, "people_sections_dropped": 0, "people_details_removed": 0}
            seen_ids: set[str] = set()
            for page, _depth in result.pages:
                page_c, page_d, st = page_chunks(page, spec, today.isoformat())
                for k in totals:
                    totals[k] += st[k]
                for c in page_c:
                    if c["id"] not in seen_ids:
                        seen_ids.add(c["id"])
                        chunks.append(c)
                        n_chunks += 1
                for f in page_d:
                    dates.append({**f.to_dict(), "source": spec.id})
                    n_dates += 1
            manifest[spec.id] = {
                "kind": spec.kind, "fetched_at": today.isoformat(),
                "refresh_days": spec.refresh_days, "pages_visited": result.visited,
                "pages_indexed": len(result.pages), "chunks": n_chunks, "dates": n_dates,
                "skipped_by_robots": result.skipped_robots, **totals,
                "errors": result.errors[:20],
            }
            sp.attributes.update(pages=len(result.pages), chunks=n_chunks, dates=n_dates)

    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with chunks_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with dates_path.open("w", encoding="utf-8") as f:
        for d in sorted(dates, key=lambda d: d["date"]):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def overdue(manifest: dict, today: dt.date | None = None) -> list[str]:
    """Sources never fetched, or fetched longer ago than their refresh interval."""
    today = today or dt.date.today()
    out = []
    for spec in load_sources():
        m = manifest.get(spec.id)
        if not m or not m.get("chunks"):
            out.append(f"{spec.id}: never fetched (or no pages found)")
            continue
        age = (today - dt.date.fromisoformat(m["fetched_at"])).days
        if age > spec.refresh_days:
            out.append(f"{spec.id}: fetched {age} days ago (refresh every {spec.refresh_days})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", help="comma-separated source ids (default: all)")
    ap.add_argument("--check-freshness", action="store_true",
                    help="report overdue sources and exit 1 if any; fetches nothing")
    args = ap.parse_args(argv)

    if args.check_freshness:
        manifest_path = _paths()[2]
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        late = overdue(manifest)
        print("\n".join(late) or "All knowledge-base sources are fresh.")
        return 1 if late else 0

    ids = [s.strip() for s in args.sources.split(",")] if args.sources else None
    manifest = build(ids)
    for sid, m in sorted(manifest.items()):
        if ids and sid not in ids:
            continue
        print(f"{sid:18} pages {m['pages_indexed']:>3}/{m['pages_visited']:<3} chunks {m['chunks']:>4} "
              f"dates {m['dates']:>3} people-removed {m['people_details_removed']:>3} "
              f"errors {len(m['errors'])}")
        for e in m["errors"][:3]:
            print(f"    ! {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
