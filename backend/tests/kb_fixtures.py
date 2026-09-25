"""
Offline stand-ins for the knowledge-base sources. Structure mirrors real
TXST pages (CourseLeaf catalog, registrar calendar tables, HTML and PDF
syllabi); the wording is TEST DATA ONLY, not actual TXST policy.
"""

from __future__ import annotations

CAT = "https://mycatalog.txstate.edu"
REG = "https://www.registrar.txstate.edu"
SYL = "https://hb2504.txst.edu"

_CHROME = """<header class="site-header"><nav><a href="/">Home</a> <a href="/menu">Menu</a></nav></header>
<div id="breadcrumb">Home / Catalog</div>"""
_FOOTER = """<footer><p>601 University Drive, San Marcos · webmaster@example.edu</p></footer>"""

CATALOG_HUB = f"""<html><head><title>Undergraduate Catalog &lt; Texas State</title></head><body>
{_CHROME}<main id="content"><h1>2026-2027 Undergraduate Catalog</h1>
<p><a href="/undergraduate/academic-policies/">Academic Policies</a></p>
<p><a href="/undergraduate/about/">About the university</a></p>
<p><a href="/undergraduate/private/policies/">Internal policies</a></p>
<p><a href="/undergraduate/academic-policies/map.jpg">Campus map policies</a></p>
<p><a href="https://evil.example.com/policies/">Mirror of policies</a></p>
</main>{_FOOTER}</body></html>"""

CATALOG_POLICIES = f"""<html><head><title>Academic Policies &lt; Texas State University</title></head><body>
{_CHROME}
<div id="content">
<p>2026-2027 Undergraduate Catalog. Test fixture — not actual TXST policy.</p>
<h1>Academic Policies</h1>
<h2>Repeating a Course</h2>
<p>A student may repeat a course in which a grade of D or F was earned. When a course is repeated,
the most recent grade replaces the earlier grade in the grade point average, although both
attempts remain on the transcript. Test data: a course may be repeated at most two times.</p>
<h2>Withdrawal</h2>
<p>Students who withdraw from a course after the census date receive a grade of W. The W grade
does not affect the grade point average but counts toward attempted hours for financial aid.</p>
<h3>Deadlines</h3>
<p>The last day to drop a course with a W is published each term in the academic calendar.</p>
<h2>Course Load</h2>
<table><tr><th>Status</th><th>Semester hours</th></tr>
<tr><td>Full-time</td><td>12 or more</td></tr>
<tr><td>Overload</td><td>more than 18, with dean approval</td></tr></table>
</div>
<aside class="sidebar"><p>Related links: parking, dining, athletics</p></aside>
{_FOOTER}</body></html>"""

ROBOTS = "User-agent: *\nDisallow: /undergraduate/private/\n"

REGISTRAR_HUB = f"""<html><head><title>Office of the University Registrar</title></head><body>
{_CHROME}<main><h1>Registrar</h1>
<p><a href="/calendars/academic-calendar.html">Academic calendar</a></p>
<p><a href="/transcripts.html">Order transcripts</a></p></main>{_FOOTER}</body></html>"""

REGISTRAR_CALENDAR = f"""<html><head><title>Academic Calendar | Registrar</title></head><body>
{_CHROME}<main>
<h1>Academic Calendar 2026-2027</h1>
<p>Test fixture dates — not the actual TXST calendar.</p>
<h2>Fall 2026</h2>
<table>
<tr><td>First class day</td><td>Monday, Aug. 24</td></tr>
<tr><td>Last day to drop a class with a W</td><td>Wednesday, Oct. 28</td></tr>
<tr><td>Registration for Spring 2027 opens</td><td>Nov. 2</td></tr>
</table>
<h2>Spring 2027</h2>
<table>
<tr><td>First class day</td><td>Tuesday, Jan. 19</td></tr>
<tr><td>Last day to drop a class with a W</td><td>Friday, March 26</td></tr>
</table>
</main>{_FOOTER}</body></html>"""

SYLLABI_HUB = """<html><head><title>Course Syllabi (HB 2504)</title></head><body><main>
<h1>Public course syllabi</h1>
<a href="/syllabi/cs3358-fall2026.html">CS 3358 Data Structures syllabus</a>
<a href="/syllabi/cs4371.pdf">CS 4371 syllabus (PDF)</a>
<a href="/syllabi/math2471.html">MATH 2471 syllabus</a>
</main></body></html>"""

SYLLABUS_HTML = """<html><head><title>CS 3358 Data Structures and Algorithms — Syllabus</title></head>
<body><main>
<h1>CS 3358 Data Structures and Algorithms — Fall 2026</h1>
<h2>Instructor Information</h2>
<p>Dr. Jane Placeholder, Comal 210, jane.placeholder@example.edu, (512) 555-0100</p>
<p>Office hours: Tuesday and Thursday 2:00-3:30 pm</p>
<h2>Course Description</h2>
<p>Classic data structures and algorithm analysis. Test fixture — not an actual TXST syllabus.</p>
<p>Instructor: Dr. Jane Placeholder</p>
<h2>Required Textbook</h2>
<p>Data Structures Test Edition, 4th edition (test fixture title).</p>
<h2>Grading</h2>
<ul><li>Programming assignments 40%</li><li>Midterm exam 25%</li><li>Final project 35%</li></ul>
<h2>Weekly Schedule</h2>
<table><tr><td>Week 1</td><td>Algorithm analysis, big-O</td></tr>
<tr><td>Week 2</td><td>Linked lists; questions to Prof. Jane Placeholder</td></tr></table>
</main></body></html>"""

SYLLABUS_MATH = """<html><head><title>MATH 2471 Calculus I — Syllabus</title></head><body><main>
<h1>MATH 2471 Calculus I</h1><h2>Course Description</h2><p>Limits and derivatives, test text
long enough to be a section on its own for the purposes of this fixture page.</p></main></body></html>"""

SYLLABUS_PDF_LINES = [
    "CS 4371 COMPUTER SYSTEM SECURITY",
    "INSTRUCTOR",
    "Prof. Sam Example, sam.example@example.edu",
    "COURSE DESCRIPTION",
    "Secure systems and practical security. Test fixture - not an actual TXST syllabus.",
    "Students analyse vulnerabilities and write secure code across the semester.",
    "Use of AI:",
    "Generative AI tools may be used for brainstorming only; submitted code must be your own.",
]


def make_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF with one line of text per entry."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


def routes() -> dict:
    html = {"content-type": "text/html; charset=utf-8"}
    return {
        f"{CAT}/undergraduate/": (200, html, CATALOG_HUB),
        f"{CAT}/undergraduate/academic-policies/": (200, html, CATALOG_POLICIES),
        f"{CAT}/undergraduate/private/policies/": (200, html, CATALOG_POLICIES),
        f"{CAT}/robots.txt": (200, {"content-type": "text/plain"}, ROBOTS),
        f"{REG}/": (200, html, REGISTRAR_HUB),
        f"{REG}/calendars/academic-calendar.html": (200, html, REGISTRAR_CALENDAR),
        f"{SYL}/": (200, html, SYLLABI_HUB),
        f"{SYL}/syllabi/cs3358-fall2026.html": (200, html, SYLLABUS_HTML),
        f"{SYL}/syllabi/math2471.html": (200, html, SYLLABUS_MATH),
        f"{SYL}/syllabi/cs4371.pdf": (200, {"content-type": "application/pdf"},
                                      make_pdf(SYLLABUS_PDF_LINES)),
    }
