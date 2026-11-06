"""Degree audit / transcript import: the parser, PDF reading, privacy and the API."""

from __future__ import annotations

import json

import pytest

from app.advising.transcript import MAX_BYTES, TranscriptError, parse_text, pdf_text, read
from tests.transcript_fixtures import BANNER, DEGREE_WORKS


def _pdf(text: str, password: str | None = None) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    for line in text.splitlines():
        pdf.cell(0, 4, line, new_x="LMARGIN", new_y="NEXT")
    if password:
        pdf.set_encryption(owner_password="owner", user_password=password)
    return bytes(pdf.output())


# -- Banner-style transcript ------------------------------------------------------

@pytest.fixture(scope="module")
def banner():
    return parse_text(BANNER)


def test_courses_by_status(banner):
    assert banner.completed == ["HIST1301", "ENGL1301", "MATH2471", "CS1428", "PHIL1305", "COMM1310",
                                "CS2308", "MATH2472", "MATH2358", "PHYS2325", "PHYS2125"]
    assert banner.in_progress == ["CS3358", "CS2318", "MATH3305"]
    assert banner.not_counted == ["ENG1320 (F)"]


def test_credit_sources(banner):
    source = {c.code: c.source for c in banner.courses}
    assert source["HIST1301"] == "transfer" and source["MATH2471"] == "test"
    assert source["CS1428"] == "institution"
    assert source["CS3358"] == "institution"           # a "Total Transfer:" row isn't a heading


def test_repeats_count_once_with_the_best_grade(banner):
    cs2308 = next(c for c in banner.courses if c.code == "CS2308")
    assert (cs2308.grade, cs2308.term, cs2308.hours) == ("B", "Fall 2025", 3.0)
    assert any("CS 2308 was taken 2 times; counted your B grade from Fall 2025" in w for w in banner.warnings)
    assert any(w.startswith("MATH 2358 was taken 2 times") for w in banner.warnings)   # W, then C


def test_hours_gpa_and_program(banner):
    # transfer 9 + AP 4 + TXST 24 (CS2308 once; the F and the W earn nothing)
    assert banner.hours_counted == banner.hours_earned == 37
    assert banner.gpa == 3.447                          # the Overall row, not a term GPA
    assert (banner.classification, banner.classification_source) == ("sophomore", "document")
    assert (banner.major, banner.degree, banner.catalog_year) == ("Computer Science", "BS", "2024-2025")


def test_generic_transfer_credit_is_hours_not_a_course(banner):
    assert banner.elective_credit == [{"code": "HIST1XXX", "hours": 3.0, "source": "transfer"}]
    assert "HIST1XXX" not in banner.completed
    assert any("HIST 1XXX" in q for q in banner.questions)


def test_no_personal_details_come_back(banner):
    out = json.dumps(banner.to_dict())
    for secret in ("Jane", "Doe", "A01234567", "FEB-2005", "Austin Community"):
        assert secret not in out


# -- Degree Works-style audit ------------------------------------------------------

def test_degree_works_audit():
    r = parse_text(DEGREE_WORKS)
    assert r.completed == ["CS1428", "CS2308", "CS2315"]
    assert r.in_progress == ["CS2318"]                  # listed twice, counted once
    assert r.not_counted == ["CS3339 (W)"]
    assert "CS3358" not in {c.code for c in r.courses}  # "Still needed: 1 Class in CS 3358"
    assert next(c for c in r.courses if c.code == "CS2315").source == "transfer"
    assert (r.hours_stated, r.hours_earned, r.hours_counted) == (64, 64, 10)
    assert any("says 64 hours earned" in w for w in r.warnings)
    assert (r.gpa, r.classification, r.degree, r.catalog_year) == (3.12, "junior", "BA", "2023-2024")
    assert "Smith" not in json.dumps(r.to_dict()) and "Pat" not in json.dumps(r.to_dict())


# -- grade rules and edge cases --------------------------------------------------------

def test_a_d_is_hours_but_not_passed():
    r = parse_text("Term: Fall 2025\nMATH 2471 Calculus I D 4.000\nCS 1428 Foundations I A 4.000\n")
    assert r.completed == ["CS1428"] and r.not_counted == ["MATH2471 (D)"]
    assert r.hours_earned == 8
    assert any("D in MATH 2471" in w and "C or better" in w for w in r.warnings)
    assert r.questions == ["My transcript shows a D in MATH 2471. Do I need to retake it before the "
                           "courses that require it?"]


def test_retaking_after_a_low_grade():
    r = parse_text("Fall 2024\nCS 2308 Foundations II F 3.000\nCourses in progress\nCS 2308 Foundations II 3.000\n")
    assert r.in_progress == ["CS2308"] and r.completed == []
    assert "You're retaking CS 2308; it's planned as passed." in r.warnings


def test_title_roman_numerals_are_not_grades():
    r = parse_text("COURSES IN PROGRESS\nMATH 2472 Calculus II 4.000\nPHYS 2325 General Physics I 3.000\n")
    assert r.in_progress == ["MATH2472", "PHYS2325"]
    r = parse_text("MATH 2471 CALCULUS I A 4.000\n")
    assert r.completed == ["MATH2471"] and r.courses[0].grade == "A"


@pytest.mark.parametrize("line", [
    "Fall 2024 Dean's List",
    "MAY 2025 graduation application",
    "CS 3358 or CS 3360 A 3",                       # two different courses: a requirement
    "Still needed: 1 Class in CS 3358",
    "CS 4346 minimum grade of C 3",
    "CS 1428 Foundations of Computer Science I 4",  # no grade outside an in-progress section
    "CS 1428 Foundations A 12.000",                 # 12 hours: not a credit-hours column
])
def test_lines_that_are_not_course_records(line):
    with pytest.raises(TranscriptError, match="No course records"):
        parse_text(line)


def test_classification_from_hours_when_not_stated():
    r = parse_text("\n".join(f"CS {1000 + i} Course A 3.000" for i in range(21)))   # 63 hours
    assert (r.classification, r.classification_source) == ("junior", "hours")


def test_text_too_long():
    with pytest.raises(TranscriptError, match="more text than a degree audit"):
        parse_text("x" * 200_001)


# -- PDFs ------------------------------------------------------------------------------

def test_pdf_round_trip():
    r = read(_pdf(BANNER), "application/pdf")
    assert r.source == "pdf" and r.pages == 1
    assert r.completed == parse_text(BANNER).completed and r.gpa == 3.447


def test_pdf_detected_by_content_not_header():
    assert read(_pdf("CS 1428 Foundations A 4.000"), "application/octet-stream").completed == ["CS1428"]


@pytest.mark.parametrize("data,message", [
    (b"not a pdf at all", "isn't a PDF"),
    (b"%PDF-1.7\nbroken", "couldn't be read"),
    (b"%PDF" + b"0" * MAX_BYTES, "larger than 5 MB"),
])
def test_unreadable_pdfs(data, message):
    with pytest.raises(TranscriptError, match=message):
        pdf_text(data)


def test_scanned_pdf_has_no_text():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()                                     # a blank page: nothing to extract
    with pytest.raises(TranscriptError, match="probably a scan"):
        pdf_text(bytes(pdf.output()))


def test_password_protected_pdf():
    with pytest.raises(TranscriptError, match="password-protected"):
        pdf_text(_pdf("CS 1428 A 4.000", password="secret"))


def test_other_content_types():
    with pytest.raises(TranscriptError, match="Upload a PDF"):
        read(b"<html/>", "text/html")
    with pytest.raises(TranscriptError, match="plain text"):
        read(b"\xff\xfe\x00", "text/plain")


# -- API ----------------------------------------------------------------------------------

def test_api_imports_a_pdf_without_keeping_it(client):
    r = client.post("/api/advise/import", content=_pdf(BANNER), headers={"Content-Type": "application/pdf"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["hours_earned"] == 37 and body["source"] == "pdf" and "CS3358" in body["in_progress"]
    assert "Jane" not in r.text and "A01234567" not in r.text


def test_api_imports_pasted_text(client):
    r = client.post("/api/advise/import", content=DEGREE_WORKS.encode(),
                    headers={"Content-Type": "text/plain; charset=utf-8"})
    assert r.status_code == 200 and r.json()["classification"] == "junior"


def test_api_errors_are_readable(client):
    r = client.post("/api/advise/import", content=b"hello", headers={"Content-Type": "text/plain"})
    assert r.status_code == 422 and "No course records" in r.json()["detail"]
    assert client.post("/api/advise/import", content=b"  ",
                       headers={"Content-Type": "text/plain"}).status_code == 422
    big = client.post("/api/advise/import", content=b"%PDF" + b"0" * MAX_BYTES,
                      headers={"Content-Type": "application/pdf"})
    assert big.status_code == 413


def test_trace_holds_counts_not_content(client):
    from app import tracing
    seen = []
    tracing.register_exporter(seen.append)
    try:
        client.post("/api/advise/import", content=BANNER.encode(), headers={"Content-Type": "text/plain"})
    finally:
        tracing.unregister_exporter(seen.append)
    assert seen and seen[-1].attributes == {"kind": "import", "outcome": "ok", "source": "text",
                                            "courses": 16}
    dumped = json.dumps([t.to_dict() for t in seen], default=str)
    assert "1428" not in dumped and "Jane" not in dumped
