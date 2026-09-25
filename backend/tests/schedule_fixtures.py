"""
Schedule test data: a Banner-style HTML table and CSV exports for past
terms. Section numbers, times and seats are TEST DATA, not TXST's schedule;
instructor names are placeholders that must never be stored.
"""

from __future__ import annotations

FALL_2026_HTML = """<html><head><title>Class Schedule - Fall 2026</title></head><body>
<h1>Class Schedule: Fall 2026 (test fixture)</h1>
<table class="datadisplaytable">
<tr><th>CRN</th><th>Subj</th><th>Crse</th><th>Sec</th><th>Title</th><th>Days</th><th>Time</th>
    <th>Cap</th><th>Act</th><th>Rem</th><th>WL Rem</th><th>Instructor</th><th>Campus</th>
    <th>Instructional Method</th></tr>
<tr><td>10001</td><td>CS</td><td>3358</td><td>001</td><td>Data Structures</td><td>MW</td>
    <td>10:00 am-11:20 am</td><td>40</td><td>35</td><td>5</td><td>0</td><td>Pat Placeholder</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td>10002</td><td>CS</td><td>3358</td><td>002</td><td>Data Structures</td><td>TR</td>
    <td>9:30 am-10:50 am</td><td>40</td><td>40</td><td>0</td><td>3</td><td>Alex Example</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td>10003</td><td>CS</td><td>3358</td><td>251</td><td>Data Structures</td><td>TBA</td>
    <td>TBA</td><td>30</td><td>12</td><td>18</td><td>0</td><td>Sam Sample</td>
    <td>Online</td><td>Online</td></tr>
<tr><td>10010</td><td>CS</td><td>2318</td><td>001</td><td>Assembly Language</td><td>MW</td>
    <td>10:00 am-11:20 am</td><td>40</td><td>20</td><td>20</td><td>0</td><td>Pat Placeholder</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td>10011</td><td>CS</td><td>2318</td><td>002</td><td>Assembly Language</td><td>TR</td>
    <td>2:00 pm-3:20 pm</td><td>40</td><td>30</td><td>10</td><td>0</td><td>Alex Example</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td>10020</td><td>MATH</td><td>2358</td><td>001</td><td>Discrete Mathematics I</td><td>MWF</td>
    <td>9:00 am-9:50 am</td><td>45</td><td>40</td><td>5</td><td>0</td><td>Robin Test</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td>10021</td><td>MATH</td><td>2358</td><td>002</td><td>Discrete Mathematics I</td><td>TR</td>
    <td>11:00 am-12:20 pm</td><td>45</td><td>30</td><td>15</td><td>0</td><td>Robin Test</td>
    <td>San Marcos</td><td>Face to Face</td></tr>
<tr><td></td><td></td><td></td><td></td><td>Lab</td><td>F</td>
    <td>2:00 pm-3:50 pm</td><td></td><td></td><td></td><td></td><td>Robin Test</td>
    <td></td><td></td></tr>
<tr><td>10030</td><td>PHIL</td><td>1305</td><td>001</td><td>Philosophy and Critical Thinking</td>
    <td>MWF</td><td>11:00 am-11:50 am</td><td>60</td><td>50</td><td>10</td><td>0</td>
    <td>Casey Demo</td><td>San Marcos</td><td>Face to Face</td></tr>
</table></body></html>"""

# Past terms, as CSV exports: CS3360 only ever in Fall, CS3358 in both.
_HEADER = "CRN,Course,Section,Title,Days,Time,Capacity,Enrolled,Instructor,Modality\n"


def csv_term(rows: list[str]) -> str:
    return _HEADER + "\n".join(rows) + "\n"


PAST_TERMS = {
    "Fall 2024": csv_term(["1,CS 3358,001,Data Structures,MW,1000-1120,40,40,Pat Placeholder,In person",
                           "2,CS 3360,001,Computing Systems,TR,1100-1220,40,38,Alex Example,In person"]),
    "Spring 2025": csv_term(["3,CS 3358,001,Data Structures,TR,0930-1050,40,39,Pat Placeholder,In person"]),
    "Fall 2025": csv_term(["4,CS 3358,001,Data Structures,MW,1000-1120,40,40,Pat Placeholder,In person",
                           "5,CS 3360,001,Computing Systems,TR,1100-1220,40,40,Alex Example,In person"]),
    "Spring 2026": csv_term(["6,CS 3358,001,Data Structures,TR,0930-1050,40,40,Pat Placeholder,In person"]),
}
