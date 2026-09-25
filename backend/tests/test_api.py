"""HTTP layer: ask, streaming, history, feedback, analytics, knowledge, limits."""

import json


def _sse(resp) -> list[tuple[str, dict]]:
    events = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["llm_available"] is False


def test_ask_persists_turn_with_trace(client):
    r = client.post("/api/chat/ask", json={"question": "What is CS3358 about?"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "course_info" and body["mode"] == "extractive"
    assert body["citations"] and body["citations"][0]["n"] == 1

    detail = client.get(f"/api/history/{body['conversation_id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["intent"] == "course_info"


def test_followup_uses_history(client, fake_llm):
    first = client.post("/api/chat/ask", json={"question": "Tell me about CS3358"}).json()
    seen = {}

    def router(messages):
        seen["prompt"] = messages[1]["content"]
        return {"intent": "prereq", "standalone_question": "What are the prerequisites of CS3358?",
                "courses": ["CS3358"], "completed_courses": []}

    fake_llm(router_fn=router, answer="CS3358 requires CS2308 [1].")
    r = client.post("/api/chat/ask", json={"question": "what do I need first?",
                                           "conversation_id": first["conversation_id"]})
    assert r.status_code == 200
    assert "Tell me about CS3358" in seen["prompt"]


def test_stream_event_sequence(client, fake_llm):
    fake_llm(answer="CS2308 introduces abstract data types [1].")
    r = client.post("/api/chat/ask/stream", json={"question": "What does CS2308 cover?"})
    assert r.status_code == 200
    types = [t for t, _ in _sse(r)]
    assert types[0] == "plan" and types[-1] == "done"
    assert "sources" in types and "token" in types and "verification" in types
    done = dict(_sse(r))["done"]
    assert done["message_id"] and done["trace"]["llm_calls"] >= 1


def test_answer_cache_serves_repeat_question(client, fake_llm):
    fake = fake_llm(answer="Cached answer [1].")
    client.post("/api/chat/ask", json={"question": "What does CS4310 cover?"})
    synth_calls = fake.calls.count("synth")
    r = client.post("/api/chat/ask", json={"question": "what does cs4310 cover"})
    assert r.status_code == 200 and fake.calls.count("synth") == synth_calls


def test_feedback_and_agent_analytics(client):
    body = client.post("/api/chat/ask", json={"question": "What is CS3339 about?"}).json()
    assert client.post("/api/feedback", json={"message_id": body["message_id"], "helpful": True}).status_code == 200
    a = client.get("/api/analytics/agents").json()
    assert a["answers"] >= 1 and "agent.router" in a["spans"]
    assert a["feedback_by_intent"]


def test_invalid_source_filter(client):
    r = client.post("/api/chat/ask", json={"question": "x", "source_filter": "twitter"})
    assert r.status_code == 400


def test_rate_limit(client, monkeypatch):
    from app.services.protection import RateLimiter
    monkeypatch.setattr("app.routers.chat.rate_limiter", RateLimiter(per_minute=1))
    assert client.post("/api/chat/ask", json={"question": "hi"}).status_code == 200
    r = client.post("/api/chat/ask", json={"question": "hi again"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_knowledge_endpoints(client):
    course = client.get("/api/courses/data structures").json()
    assert course["code"] == "CS3358" and "CS3360" in course["unlocks"]
    assert "professors" not in course
    # Per-instructor profiles and comparisons were removed.
    assert client.get("/api/professors").status_code == 404
    assert client.get("/api/compare", params={"professors": ["a", "b"]}).status_code == 404
    plan = client.post("/api/plan", json={"completed": ["CS 2308"]}).json()
    assert "CS1428" in plan["completed"]
