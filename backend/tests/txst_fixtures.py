"""
Offline stand-ins for mycatalog.txstate.edu pages, in the CourseLeaf markup
the real catalog uses (sc_courselist / sc_plangrid / courseblock). The
requirements are illustrative test data, not the official degree plan.
"""

from __future__ import annotations

BASE = "https://mycatalog.txstate.edu"
PROGRAM_URL = BASE + "/undergraduate/science-engineering/computer-science/computer-science-bs/"
# Where the search result points, for the "program page moved" test.
MOVED_URL = BASE + "/undergraduate/science-engineering/computer-science-bs/"


def _row(code: str, title: str, hours: str = "3", cls: str = "even") -> str:
    return (f'<tr class="{cls}"><td class="codecol"><a href="/search/?P={code}">{code}</a></td>'
            f"<td>{title}</td><td class=\"hourscol\">{hours}</td></tr>")


def _or(code: str, title: str) -> str:
    return (f'<tr class="orclass odd"><td class="codecol">or <a href="/search/?P={code}">{code}</a>'
            f"</td><td>{title}</td><td class=\"hourscol\"></td></tr>")


def _area(text: str) -> str:
    return (f'<tr class="even areaheader"><td colspan="2"><span class="courselistcomment areaheader">'
            f"{text}</span></td><td class=\"hourscol\"></td></tr>")


def _comment(text: str, hours: str = "") -> str:
    return (f'<tr class="odd"><td colspan="2"><span class="courselistcomment">{text}</span></td>'
            f'<td class="hourscol">{hours}</td></tr>')


REQUIREMENTS_TABLE = (
    '<table class="sc_courselist"><tbody>'
    + _area("Major Requirements")
    + _row("CS 1428", "Foundations of Computer Science I", "4")
    + _row("CS 2308", "Foundations of Computer Science II")
    + _row("CS 2315", "Computer Ethics")
    + _row("CS 2318", "Assembly Language")
    + _row("CS 3339", "Computer Architecture")
    + _row("CS 3358", "Data Structures and Algorithms")
    + _row("CS 3360", "Computing Systems Fundamentals")
    + _row("CS 3398", "Software Engineering")
    + _area("Advanced Electives")
    + _comment("Select 6 hours from the following:", "6")
    + _row("CS 4346", "Introduction to Artificial Intelligence")
    + _row("CS 4371", "Computer System Security")
    + _row("CS 4332", "Introduction to Database Systems")
    + _area("Supporting Courses")
    + _row("MATH 2471", "Calculus I", "4")
    + _row("MATH 2358", "Discrete Mathematics I")
    + _row("PHIL 1305", "Philosophy and Critical Thinking")
    + _or("PHIL 1320", "Ethics and Society")
    + '<tr class="listsum"><td colspan="2">Total Hours</td><td class="hourscol">120</td></tr>'
    + "</tbody></table>"
)

PLAN_GRID = """
<table class="sc_plangrid"><tbody>
<tr class="plangridyear"><th colspan="4">First Year</th></tr>
<tr class="plangridterm"><th class="hourscol">Fall</th><th class="hourscol">Hours</th>
  <th class="hourscol">Spring</th><th class="hourscol">Hours</th></tr>
<tr class="even"><td class="codecol"><a href="#">CS 1428</a></td><td class="hourscol">4</td>
  <td class="codecol"><a href="#">CS 2308</a></td><td class="hourscol">3</td></tr>
<tr class="odd"><td class="codecol"><a href="#">MATH 2471</a></td><td class="hourscol">4</td>
  <td class="codecol"><a href="#">MATH 2358</a></td><td class="hourscol">3</td></tr>
<tr class="plangridsum"><td>&nbsp;</td><td class="hourscol">8</td><td>&nbsp;</td><td class="hourscol">6</td></tr>
<tr class="plangridyear"><th colspan="4">Second Year</th></tr>
<tr class="plangridterm"><th class="hourscol">Fall</th><th class="hourscol">Hours</th>
  <th class="hourscol">Spring</th><th class="hourscol">Hours</th></tr>
<tr class="even"><td class="codecol"><a href="#">CS 2318</a></td><td class="hourscol">3</td>
  <td class="codecol"><a href="#">CS 3339</a></td><td class="hourscol">3</td></tr>
<tr class="odd"><td class="codecol"><a href="#">CS 3358</a></td><td class="hourscol">3</td>
  <td class="codecol">Core Curriculum Component</td><td class="hourscol">3</td></tr>
</tbody></table>
"""

PROGRAM_PAGE = f"""<!doctype html><html><head>
<title>Computer Science (B.S.) &lt; Texas State University</title>
<script>var tracking = "Prerequisite: none";</script><style>.x{{}}</style></head>
<body><header><p>2025-2026 Undergraduate Catalog</p></header>
<h1 class="page-title">Computer Science (B.S.)</h1>
<div id="requirementstextcontainer"><h2>Requirements</h2>
<p>The degree requires a minimum of 120 semester credit hours.</p>
{REQUIREMENTS_TABLE}</div>
<div id="courseplantextcontainer"><h2>Four-Year Plan</h2>{PLAN_GRID}</div>
</body></html>"""


def course_page(code: str, title: str, hours: int, desc: str, prereq: str = "") -> str:
    subj, num = code.split()
    pre = f" Prerequisite: {prereq}" if prereq else ""
    return (f"<html><head><title>{code} &lt; Search</title></head><body>"
            f'<div class="searchresult"><div class="courseblock">'
            f'<p class="courseblocktitle"><strong>{subj}&nbsp;{num}.  {title}.  {hours} Semester Credit Hours.'
            f"</strong></p><p class=\"courseblockdesc\">{desc}{pre}</p>"
            f'<p class="courseblockextra">Course Attributes: Upper Division</p></div></div></body></html>')


COURSE_PAGES = {
    "CS 2315": course_page("CS 2315", "Computer Ethics", 3,
                           "Ethical codes of the professional societies.",
                           'CS 1428 and [COMM 1310 or COMM 2338] and [ENG 1310 or ENG 1320 or ENG 1321 '
                           'or ENG 3303] and [PHIL 1305 or PHIL 1320] all with grades of "C" or better.'),
    # Differs from the snapshot (which requires CS 2308 and MATH 2358): a conflict.
    "CS 2318": course_page("CS 2318", "Assembly Language", 3, "Machine-level programming.",
                           'CS 2308 and MATH 2358 and MATH 2471 all with grades of "C" or better.'),
    "CS 3339": course_page("CS 3339", "Computer Architecture", 3, "Processor design.",
                           'CS 2308 and [CS 2318 or EE 3320] both with grades of "C" or better.'),
    "CS 3358": course_page("CS 3358", "Data Structures and Algorithms", 3,
                           "Abstract data types, trees, graphs and algorithm analysis.",
                           'CS 2308 and MATH 2358 both with grades of "C" or better.'),
    "MATH 2358": course_page("MATH 2358", "Discrete Mathematics I", 3, "Logic, sets and proofs.",
                             'MATH 1315 or MATH 2471 with a grade of "C" or better.'),
    "MATH 2471": course_page("MATH 2471", "Calculus I", 4, "Limits and derivatives.",
                             'MATH 2417 with a grade of "C" or better.'),
    "PHIL 1305": course_page("PHIL 1305", "Philosophy and Critical Thinking", 3, "Arguments."),
}

SEARCH_PAGE = """<html><head><title>Search</title></head><body>
<div class="searchresult"><h2><a href="/undergraduate/liberal-arts/history/history-ba/">History (B.A.)</a></h2></div>
<div class="searchresult"><h2><a href="https://evil.example.com/undergraduate/computer-science-bs/">Computer Science (B.S.)</a></h2></div>
<div class="searchresult"><h2><a href="/undergraduate/science-engineering/computer-science-bs/">Computer Science (B.S.)</a></h2></div>
</body></html>"""


def pages() -> dict[str, str]:
    from urllib.parse import quote_plus
    out = {PROGRAM_URL: PROGRAM_PAGE, BASE + "/search/?search=Computer+Science": SEARCH_PAGE}
    for code, html in COURSE_PAGES.items():
        out[f"{BASE}/search/?P={quote_plus(code)}"] = html
    return out


class FakeFetcher:
    """Serves fixture pages; 404 for anything else. Records every request."""

    def __init__(self, routes: dict[str, str | tuple[int, dict, str]] | None = None):
        self.routes = pages() if routes is None else routes
        self.requests: list[str] = []

    def get(self, url, timeout_s):
        self.requests.append(url)
        r = self.routes.get(url)
        if r is None:
            return 404, {"content-type": "text/html"}, "<html>Not found</html>"
        if isinstance(r, tuple):
            return r
        return 200, {"content-type": "text/html; charset=utf-8"}, r
