"""Topic questions in the chat ("How do I learn AI?"): the words students
use map to skills, and skills to the TXST courses that teach them."""

from __future__ import annotations

import pytest

from app.agents import orchestrator
from app.agents.router import route
from app.careers.mapping import topic_courses, topic_summary
from app.careers.paths import find_topics
from app.tracing import start_trace


@pytest.mark.parametrize("text,first", [
    ("How do I learn AI?", "ai"),
    ("Is A.I. hard?", "ai"),
    ("teach me machine learning", "ml"),
    ("best classes for cybersecurity", "security"),
    ("I want to get into web dev", "web"),
    ("where do I learn SQL", "databases"),
])
def test_topic_words(text, first):
    assert find_topics(text)[0] == first


@pytest.mark.parametrize("text", ["what's the main idea", "I'm available Monday", "email me", "guitar"])
def test_no_topic_inside_other_words(text):
    assert find_topics(text) == []


def test_ai_courses_and_their_order():
    codes = topic_courses(find_topics("learn AI"))
    assert codes[:2] == ["CS4346", "CS4347"] and "CS1309" not in codes and "CS5315" not in codes


def test_summary_explains_the_path_and_what_not_to_take():
    text = topic_summary(["ai", "ml", "deep_learning"], ["CS4346", "CS4347"])
    assert "CS4347 Introduction to Machine Learning — needs CS3358, MATH3305" in text
    assert "Path to get there: CS1428 → CS2308 → MATH2358 → CS3358" in text
    assert "Graduate level (5000+): CS5315" in text
    assert "Doesn't count toward a CS degree: CS1309 AI for Everyone" in text
    assert "Careers tab" in text


def test_summary_names_topics_the_catalog_lacks():
    assert "Not taught in the undergraduate CS catalog: ML deployment and MLOps" in topic_summary(["mlops"], [])


def test_router_adds_topic_courses_without_an_llm():
    plan = route("How do I learn AI?", use_llm=False)
    assert plan.intent == "course_info" and plan.topics[0] == "ai"
    assert plan.courses[:2] == ["CS4346", "CS4347"]
    assert "artificial intelligence" in plan.standalone_question


@pytest.mark.parametrize("question", [
    "What is CS3358 about?",            # names a course: topics not needed
    "Can I use AI on homework?",        # a policy question about AI
    "Is Dr. Smith good for AI?",        # instructor questions stay declined
])
def test_topics_do_not_override_other_routes(question):
    plan = route(question, use_llm=False)
    assert plan.topics == []


def test_extractive_answer_leads_with_the_courses():
    with start_trace():
        events = list(orchestrator.run("How do I learn AI?", [], None))
    done = events[-1]
    assert done["mode"] == "extractive"
    body = done["answer"].split("\n", 2)[2]
    assert body.startswith("TXST COURSES FOR ARTIFICIAL INTELLIGENCE")
    assert "CS4346" in body and "CS4318" not in body
