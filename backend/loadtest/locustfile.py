"""
Load test for the Bobcat Advisor API.

  locust -f loadtest/locustfile.py --host http://localhost:8000 \
         --headless -u 50 -r 10 -t 60s --csv results/run
  LOCUST_NO_WAIT=1 locust ... -u 20 -t 30s       # saturation: requests back to back

The traffic mix mirrors how the UI is used: mostly catalog lookups and
chat questions, fewer advising runs (the heaviest endpoint), and some
timetable and what-if requests. Half the chat questions repeat (the answer
cache serves them); half are unique.

Without an LLM key, chat answers are extractive, so these numbers measure
the app's own overhead: routing, retrieval, planning and database writes,
not the provider's latency. See docs/LOAD_TEST.md.
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, constant, task

COURSES = ["CS1428", "CS2308", "CS2318", "CS3339", "CS3358", "CS3360", "CS3398", "CS4310",
           "CS4328", "CS4332", "CS4346", "CS4371"]
QUESTIONS = [
    "What is CS3358 about?", "What are the prerequisites for CS3360?",
    "Which course covers compilers?", "Should I take CS3358 or CS3360 first?",
    "I've taken CS1428 and CS2308. What can I take next?", "What does CS4328 unlock?",
]
PROFILE = {"year": "sophomore", "semester": "Fall", "term_year": 2026, "target_credits": 15,
           "completed": ["CS1428", "CS2308", "MATH2471"], "interests": ["AI"]}


class Student(HttpUser):
    # LOCUST_NO_WAIT=1: no think time, to find the throughput ceiling.
    wait_time = constant(0) if os.environ.get("LOCUST_NO_WAIT") else between(1, 3)

    @task(1)
    def live(self):
        self.client.get("/api/health/live")

    @task(2)
    def list_courses(self):
        self.client.get("/api/courses")

    @task(3)
    def course_detail(self):
        self.client.get(f"/api/courses/{random.choice(COURSES)}", name="/api/courses/{code}")

    @task(2)
    def plan(self):
        self.client.post("/api/plan", json={"completed": random.sample(COURSES[:4], 2)})

    @task(4)
    def chat(self):
        q = random.choice(QUESTIONS)
        if random.random() < 0.5:                      # unique: bypasses the answer cache
            q = f"{q} ({uuid.uuid4().hex[:6]})"
        self.client.post("/api/chat/ask", json={"question": q}, name="/api/chat/ask")

    @task(2)
    def timetable(self):
        self.client.post("/api/timetable", json={
            "term": "Fall 2026", "courses": random.sample(["CS3358", "CS2318", "MATH2358", "PHIL1305"], 3),
            "preferred_days": random.choice(["", "MWF", "TR"])})

    @task(1)
    def advise(self):
        self.client.post("/api/advise", json=PROFILE)

    @task(1)
    def whatif(self):
        self.client.post("/api/advise/whatif", json={
            "profile": PROFILE, "scenarios": [{"type": "change_load", "target_credits": 12},
                                              {"type": "fail_course", "course": "MATH2358"}]})
