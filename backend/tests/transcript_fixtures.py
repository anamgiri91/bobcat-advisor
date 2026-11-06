"""
Illustrative documents for the degree-audit importer. Layouts follow the
common shapes of a Banner unofficial transcript and a Degree Works audit;
the student, ID and every grade are made up.
"""

BANNER = """\
Texas State University Unofficial Academic Transcript
Name: Jane Q Doe   Student ID: A01234567
Birth Date: 01-FEB-2005
Current Program
Bachelor of Science
Program: BS Computer Science
Major: Computer Science   Minor: Mathematics
Catalog Term: Fall 2024
Classification: Sophomore

TRANSFER CREDIT ACCEPTED BY INSTITUTION:
Fall 2023 Austin Community College
HIST 1301 UG US History I A 3.000
ENGL 1301 UG Composition I B 3.000
HIST 1XXX UG History Elective A 3.000

TEST CREDIT
AP Calculus AB
MATH 2471 UG Calculus I CR 4.000

INSTITUTION CREDIT:
Term: Fall 2024
CS 1428 UG Foundations of Computer Science I B 4.000 12.00
PHIL 1305 UG Philosophy and Critical Thinking A 3.000 12.00
COMM 1310 UG Fundamentals of Human Communication A 3.000 12.00
Term Totals (Undergraduate) GPA 3.40
Term: Spring 2025
CS 2308 UG Foundations of Computer Science II D 3.000 3.00
MATH 2472 UG Calculus II B 4.000 12.00
MATH 2358 UG Discrete Mathematics I W 3.000 0.00
Term: Fall 2025
CS 2308 UG Foundations of Computer Science II B 3.000 9.00 R
MATH 2358 UG Discrete Mathematics I C 3.000 6.00
PHYS 2325 UG General Physics I A 3.000 12.00
PHYS 2125 UG General Physics I Laboratory A 1.000 4.00
ENG 1320 UG College Writing II F 3.000 0.00

TRANSCRIPT TOTALS (UNDERGRADUATE)
Total Institution: 35.000 32.000 32.000 29.000 98.00 3.379
Total Transfer: 13.000 13.000 13.000 9.000 33.00 3.667
Overall: 48.000 45.000 45.000 38.000 131.00 3.447

COURSES IN PROGRESS
Term: Spring 2026
CS 3358 UG Data Structures and Algorithms 3.000
CS 2318 UG Assembly Language 3.000
MATH 3305 UG Introduction to Probability I 3.000
"""

DEGREE_WORKS = """\
Degree Works - Student View
Student Jane Q Doe  ID A01234567
Classification Junior  Advisor Smith, Pat
Degree Bachelor of Arts  Catalog Year 2023-2024
Major Computer Science
Overall GPA 3.12
Degree in Bachelor of Arts  Credits required: 120  Credits applied: 64
Major in Computer Science  Credits applied: 22
Foundations of Computer Science I  CS 1428  Foundations of Comp Sci I  A  4  Fall 2023
Foundations of Computer Science II  CS 2308  Foundations of Comp Sci II  B  3  Spring 2024
Data Structures  Still needed: 1 Class in CS 3358
Advanced electives  Still needed: 6 Credits in CS 4@ with a grade of C or better
Assembly Language  CS 2318  Assembly Language  IP  (3)  Fall 2025
Computer Ethics  CS 2315  Computer Ethics  TB  3  Fall 2022  Transfer: Lone Star College
In-progress
CS 2318  Assembly Language  IP  (3)  Fall 2025
Insufficient
CS 3339  Computer Architecture  W  3  Spring 2025
"""
