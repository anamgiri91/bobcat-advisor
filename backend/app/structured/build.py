"""
build.py
========
Fetches or imports the class schedule into data/schedule_sections.jsonl and
writes the offering history to data/course_offerings.json.

  python -m app.structured.build                          # fetch configured terms
  python -m app.structured.build --terms "Fall 2026,Spring 2027"
  python -m app.structured.build --import-csv fall.csv --term "Fall 2026"
  python -m app.structured.build --import-html page.html --term "Fall 2026"

Fetching uses the sandboxed advising browser (HTTPS, TXST hosts only). A
term's stored sections are replaced only when the new fetch returned some,
so a failed fetch never wipes good data. With no URL configured (see
sources.json) fetching is a no-op and says so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from ..advising.web import Browser, FetchError, parse_html
from . import schedule
from .offerings import compute_offerings
from .schedule import Section, save_sections, sections_from_csv, sections_from_page

SEASONS = ("Spring", "Summer", "Fall")


def load_config() -> dict:
    return json.loads(Path(__file__).with_name("sources.json").read_text())["schedule"]


def term_window(today: dt.date, past: int, future: int) -> list[str]:
    """Terms from `past` before the current one to `future` after it."""
    idx = 0 if today.month <= 5 else 1 if today.month <= 7 else 2
    out = []
    for offset in range(-past, future + 1):
        n = idx + offset
        year, season = today.year + n // 3, SEASONS[n % 3]
        out.append(f"{season} {year}")
    return out


def fetch_term(term: str, cfg: dict, browser: Browser) -> tuple[list[Section], list[str]]:
    season, year = term.split()
    code = cfg["term_codes"][season].format(year=year)
    sections, errors = [], []
    for template in cfg["url_templates"]:
        for subject in cfg["subjects"]:
            url = template.format(term_code=code, subject=subject, year=year, season=season)
            try:
                page = browser.fetch(url)
            except FetchError as e:
                errors.append(f"{url}: {e}")
                continue
            got = [s for s in sections_from_page(page.parsed, term) if s.course.startswith(subject)]
            sections.extend(got)
    # One row per section even if two templates returned it.
    unique = {(s.course, s.section): s for s in sections}
    return list(unique.values()), errors


def write_offerings() -> int:
    """Written next to the sections store (data/course_offerings.json)."""
    offerings = compute_offerings()
    path = schedule.store_path().with_name("course_offerings.json")
    path.write_text(json.dumps({c: o.to_dict() for c, o in sorted(offerings.items())}, indent=1) + "\n")
    return len(offerings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terms", help="comma-separated terms, e.g. 'Fall 2026,Spring 2027'")
    ap.add_argument("--import-csv", type=Path)
    ap.add_argument("--import-html", type=Path)
    ap.add_argument("--term", help="term for --import-csv / --import-html")
    args = ap.parse_args(argv)

    if args.import_csv or args.import_html:
        if not args.term:
            ap.error("--term is required with --import-csv / --import-html")
        if args.import_csv:
            sections = sections_from_csv(args.import_csv.read_text(encoding="utf-8-sig"), args.term)
        else:
            sections = sections_from_page(parse_html(args.import_html.read_text(encoding="utf-8")), args.term)
        if not sections:
            print("No schedule rows recognised (need course, days and time columns).")
            return 1
        save_sections(sections)
        print(f"Imported {len(sections)} sections for {args.term}; offerings for {write_offerings()} courses.")
        return 0

    cfg = load_config()
    if not cfg["url_templates"]:
        print("No schedule URL configured (app/structured/sources.json); nothing fetched. "
              "Use --import-csv to load an export.")
        return 0
    terms = ([t.strip() for t in args.terms.split(",")] if args.terms
             else term_window(dt.date.today(), cfg["past_terms"], cfg["future_terms"]))
    browser = Browser(max_pages=len(terms) * len(cfg["url_templates"]) * len(cfg["subjects"]) + 5)
    for term in terms:
        sections, errors = fetch_term(term, cfg, browser)
        if sections:
            save_sections(sections, {term})
        print(f"{term:12} {len(sections):>4} sections" + (f"  ({len(errors)} errors)" if errors else ""))
        for e in errors[:3]:
            print(f"    ! {e}")
    print(f"Offering history for {write_offerings()} courses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
