"""Career paths: data integrity, resolving a goal, course mapping, the
resource scout and link checker, the roadmap verifier, the event stream
and the API."""

from __future__ import annotations

import json
import threading

import pytest

from app.advising import web
from app.careers import mapping, pipeline, scout
from app.careers.paths import allowed_domains, careers, resolve, resources, skills
from app.tracing import start_trace
from tests.txst_fixtures import FakeFetcher


def _page(title: str, description: str = "", body: str = "") -> str:
    return (f"<html><head><title>{title}</title><meta name='description' content='{description}'>"
            f"</head><body><p>{body}</p></body></html>")


@pytest.fixture
def no_search():
    scout.set_searcher(None)
    yield
    scout.set_searcher(None)


# -- data ---------------------------------------------------------------------------

def test_data_references_are_consistent():
    for c in careers().values():
        assert set(c.core) | set(c.helpful) <= set(skills()), c.id
        assert not set(c.core) & set(c.helpful), c.id
    for r in resources():
        assert set(r.skills) <= set(skills()) and set(r.careers) <= set(careers()), r.title
        assert r.kind in {"course", "certification", "tutorial", "book"}
        assert r.cost in {"free", "free to audit", "free tier", "paid"}
        assert web.is_allowed(r.url, allowed_domains()), r.url
        assert "(" not in r.provider                 # rendered as "Title (Provider)"
    assert len({r.url for r in resources()}) == len(resources())


def test_career_link_checker_cannot_reach_other_sites():
    domains = allowed_domains()
    assert not web.is_allowed("https://evil.example/x", domains)
    assert not web.is_allowed("http://www.coursera.org/x", domains)      # HTTPS only
    assert not web.is_allowed("https://coursera.org.evil.example/", domains)


# -- resolving a goal -------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("ml_engineer", "ml_engineer"),
    ("Machine learning engineer", "ml_engineer"),
    ("I want to be an ML engineer", "ml_engineer"),
    ("machine learning", "ml_engineer"),
    ("I'd like to do cybersecurity", "security_engineer"),
    ("SRE", "cloud_devops"),
    ("full-stack developer", "web_developer"),
    ("LLM engineer", "ai_engineer"),
    ("data analyst", "data_scientist"),
])
def test_rules_resolve_common_goals(text, expected):
    res = resolve(text)
    assert res.career and res.career.id == expected and res.method in ("exact", "alias")


def test_unknown_goal_lists_the_options():
    res = resolve("underwater basket weaving")
    assert res.career is None and "Pick one of" in res.note and "Game developer" in res.note


def test_llm_picks_from_the_list_only(fake_llm):
    fake_llm(answer='{"id": "data_engineer"}')
    res = resolve("I like building pipelines that move numbers around")
    assert res.career.id == "data_engineer" and res.method == "llm"
    fake_llm(answer='{"id": "astronaut"}')                               # not a defined career
    assert resolve("I like building pipelines that move numbers around").career is None


# -- course mapping -------------------------------------------------------------------------

def test_non_major_courses_are_never_recommended():
    for skill_id in skills():
        assert "CS1309" not in {m.code for m in mapping.matches_for(skill_id)}   # "will not satisfy CS major"
        assert "CS1308" not in {m.code for m in mapping.matches_for(skill_id)}


def test_ml_coverage_and_evidence():
    cov = {s.id: s for s in mapping.coverage(careers()["ml_engineer"])}
    assert cov["ml"].coverage == "covered" and "CS4347" in cov["ml"].to_dict()["courses"]
    assert cov["deep_learning"].coverage == "partial"                   # one mention, in CS4337
    assert cov["deep_learning"].matches[0].evidence.startswith("Topics include")
    assert cov["mlops"].coverage == "gap"
    assert cov["math_ml"].coverage == "gap" and "Mathematics department" in cov["math_ml"].note


def test_gateway_prerequisite_comes_first():
    career = careers()["ai_engineer"]
    courses = mapping.recommend_courses(career, mapping.coverage(career), {"CS1428", "CS2308"}, set())
    first = courses[0]
    assert first["code"] == "CS3358" and first["status"] == "eligible"
    assert "CS4347" in first["unlocks"] and first["skills"] == []
    ml = next(c for c in courses if c["code"] == "CS4347")
    assert ml["status"] == "later" and ml["missing"] == ["CS3358"]
    assert [c["status"] for c in courses[-2:]] == ["done", "done"]        # progress shown last


def test_in_progress_and_implied_prerequisites():
    career = careers()["ml_engineer"]
    courses = mapping.recommend_courses(career, mapping.coverage(career), {"CS3358"}, {"CS4347"})
    status = {c["code"]: c["status"] for c in courses}
    assert status["CS4347"] == "in_progress"
    assert status["CS2308"] == "done"                     # implied by CS3358
    assert status["CS4337"] == "eligible"


def test_experience_skips_what_is_done():
    assert "CS4100" not in {e["code"] for e in mapping.experience({"CS4100"})}
    assert {e["code"] for e in mapping.experience(set())} == {"CS4100", "CS4298", "CS3190"}


# -- scout ------------------------------------------------------------------------------------

def test_seed_candidates_without_a_search_key(no_search):
    out = scout.gather([("mlops", "gap"), ("dsa", "deeper")], "ml_engineer", "ML engineer",
                       include_certifications=True, free_only=False)
    assert {c.for_skill for c in out if c.purpose != "certification"} == {"mlops", "dsa"}
    assert all(c.source == "seed" for c in out)
    certs = [c for c in out if c.purpose == "certification"]
    assert certs and all("ml_engineer" in c.careers for c in certs)
    assert len({c.url for c in out}) == len(out)                        # de-duplicated


def test_free_only_and_no_certifications(no_search):
    out = scout.gather([("cloud", "gap")], "cloud_devops", "Cloud engineer",
                       include_certifications=False, free_only=True)
    assert out and all(c.cost != "paid" and c.purpose != "certification" for c in out)


def test_search_results_are_limited_to_known_providers(no_search):
    queries = []

    def fake_search(q):
        queries.append(q)
        return [scout.SearchHit("https://evil.example/mlops", "MLOps!!", ""),
                scout.SearchHit("https://www.coursera.org/learn/mlops-new", "New MLOps course", ""),
                scout.SearchHit("https://madewithml.com/", "Made With ML", "")]
    scout.set_searcher(fake_search)
    seen = []
    out = scout.gather([("mlops", "gap")], "ml_engineer", "Machine learning engineer",
                       include_certifications=False, free_only=False, on_search=seen.append)
    urls = [c.url for c in out]
    assert "https://evil.example/mlops" not in urls
    new = next(c for c in out if c.url.endswith("mlops-new"))
    assert new.source == "search" and new.cost == "check site"
    assert next(c for c in out if c.url == "https://madewithml.com/").source == "seed"   # known page
    assert seen == [{"query": queries[0], "results": 3, "kept": 2}]
    assert "machine learning engineers" in queries[0]


def test_search_failure_falls_back_to_seeds(no_search):
    def broken(q):
        raise RuntimeError("quota exceeded")
    scout.set_searcher(broken)
    with start_trace() as trace:
        out = scout.gather([("mlops", "gap")], "ml_engineer", "ML engineer",
                           include_certifications=False, free_only=False)
    assert out and all(c.source == "seed" for c in out)
    assert "quota" in next(s for s in trace.spans if s.name == "career.search").attributes["error"]


def test_configured_provider(monkeypatch, no_search):
    from app.config import settings
    monkeypatch.setattr(settings, "SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(settings, "BRAVE_API_KEY", None)
    assert scout.searcher() is None                                    # no key, no search
    monkeypatch.setattr(settings, "BRAVE_API_KEY", "k")
    assert scout.searcher() is scout._brave


def _cand(url, source="seed", skill="mlops", kind="course"):
    return scout.Candidate("T", "P", url, kind, "free", [skill], source, (), "gap", skill)


def test_link_checker_outcomes():
    base = "https://madewithml.com"
    web.set_fetcher(FakeFetcher({
        f"{base}/ok": _page("Made With ML", "Learn MLOps and how to deploy models to production"),
        f"{base}/offtopic": _page("Cookie recipes", "Chocolate chip"),
        f"{base}/refused": (403, {"content-type": "text/html"}, "nope"),
        f"{base}/search-ok": _page("Serving models", "", "MLOps serving at scale"),
    }))
    cands = [_cand(f"{base}/ok"), _cand(f"{base}/gone"), _cand(f"{base}/offtopic"),
             _cand(f"{base}/refused"), _cand(f"{base}/refused", source="search"),
             _cand(f"{base}/search-ok", source="search"), _cand("https://evil.example/x")]
    with start_trace():
        kept, dropped = scout.check(cands, web.Browser(max_pages=20, allowed_domains=allowed_domains()))
    status = {(c.url, c.source): c.check for c in cands}
    ok = status[(f"{base}/ok", "seed")]
    assert ok["status"] == "verified" and ok["page_title"] == "Made With ML" and ok["matched"] == "mlops"
    assert status[(f"{base}/gone", "seed")]["status"] == "dropped"                       # 404
    assert status[(f"{base}/offtopic", "seed")]["reason"] == "page isn't about this skill"
    assert status[(f"{base}/refused", "seed")] == {"status": "unchecked", "detail": "HTTP 403",
                                                    "reason": "the site refused an automated check"}
    assert status[(f"{base}/refused", "search")]["status"] == "dropped"                  # no trust
    found = next(c for c in cands if c.url.endswith("search-ok"))
    assert found.check["status"] == "verified" and found.title == "Serving models"
    assert status[("https://evil.example/x", "seed")]["status"] == "unchecked"          # blocked, never fetched
    assert len(kept) + len(dropped) == len(cands)


def test_unreachable_seed_is_kept_but_labelled():
    kept, dropped = scout.check([_cand("https://madewithml.com/")], web.Browser(allowed_domains=allowed_domains()))
    assert not dropped and kept[0].check["reason"] == "couldn't reach the site to check it"


def test_select_prefers_verified_and_on_topic():
    a = _cand("https://a.org/1", skill="mlops")
    a.skills = ["llm", "mlops"]
    a.check = {"status": "verified"}
    b = _cand("https://a.org/2")
    b.check = {"status": "verified"}
    c = _cand("https://a.org/3")
    c.check = {"status": "unchecked"}
    d = _cand("https://a.org/4")
    d.check = {"status": "verified"}
    groups = scout.select([c, a, b, d])
    assert [r["url"] for r in groups["gap"]] == ["https://a.org/2", "https://a.org/4"]   # 2 per skill


# -- browser changes --------------------------------------------------------------------------

def test_meta_description_is_parsed():
    page = web.parse_html(_page("T", "About MLOps"), "https://x.org/")
    assert page.description == "About MLOps" and "About MLOps" not in page.text


def test_page_budget_holds_across_threads():
    web.set_fetcher(FakeFetcher({f"https://madewithml.com/{i}": _page("x") for i in range(8)}))
    browser = web.Browser(max_pages=3, allowed_domains=allowed_domains())
    results = []

    def go(i):
        try:
            browser.fetch(f"https://madewithml.com/{i}")
            results.append(True)
        except web.FetchError:
            results.append(False)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 3 and len(browser.visits) == 8


# -- roadmap verifier ---------------------------------------------------------------------------

def test_verifier_removes_unsupported_codes_and_urls():
    memo = ("**Next term:** Take CS3358. Then CS9999 is great.\n\n"
            "**Outside class:** Visit https://evil.example now. Try Made With ML.\n\n"
            "**Extra:** MATH 3377 is required.")
    text, report = pipeline.verify_memo(memo, {"CS3358"})
    assert "CS3358" in text and "CS9999" not in text and "evil" not in text
    assert "Made With ML" in text and "MATH 3377" not in text and "Extra" not in text
    assert len(report["removed"]) == 3 and report["course_mentions_checked"] == 3


# -- pipeline and API -----------------------------------------------------------------------------

def _events(raw):
    with start_trace():
        return list(pipeline.run(raw))


def test_event_stream_order_and_template_memo(no_search):
    events = _events({"career": "machine learning engineer", "completed": ["CS1428", "CS2308"]})
    agents = [e["agent"] for e in events if e["type"] == "agent" and e["status"] == "done"]
    assert agents == pipeline.AGENTS
    types = [e["type"] for e in events]
    for t in ("career", "courses", "skills", "resources", "memo", "verification"):
        assert t in types
    done = events[-1]
    assert done["type"] == "done" and done["mode"] == "template"
    assert done["memo"].startswith("**Next term:** CS3358")
    assert "ML deployment and MLOps: Made With ML" in done["memo"]
    assert done["verification"]["removed"] == []
    assert done["coverage"] == {"covered": 9, "partial": 2, "gap": 3}
    # Offline: every seed is "couldn't reach", nothing verified, nothing dropped.
    assert all(r["check"]["status"] == "unchecked" for g in done["resources"].values() for r in g)


def test_llm_memo_is_verified(fake_llm, no_search):
    fake_llm(answer="**Next term:** Take CS3358 now. Also take CS4999 for fun.")
    done = _events({"career": "ai_engineer", "completed": ["CS1428"]})[-1]
    assert done["mode"] == "llm" and "CS3358" in done["memo"] and "CS4999" not in done["memo"]
    assert done["verification"]["removed"] == ["Also take CS4999 for fun."]


def test_llm_failure_keeps_the_template(fake_llm, no_search):
    fake_llm(fail_with=RuntimeError("provider down"))
    events = _events({"career": "security_engineer"})
    mentor = next(e for e in events if e.get("agent") == "mentor" and e["status"] == "done")
    assert "template" in mentor["error"] and events[-1]["mode"] == "template"


def test_unknown_career_is_a_readable_error(no_search):
    with pytest.raises(pipeline.CareerError, match="Pick one of"):
        _events({"career": "zzzz qqqq"})


def test_api_paths_and_json(client, no_search):
    paths = client.get("/api/career/paths").json()
    assert {"id": "ml_engineer", "title": "Machine learning engineer",
            "summary": careers()["ml_engineer"].summary} in paths
    r = client.post("/api/career", json={"career": "game dev", "completed": ["CS1428"]})
    assert r.status_code == 200 and r.json()["career"]["id"] == "game_developer"
    assert client.post("/api/career", json={"career": "zzzz qqqq"}).status_code == 422
    assert client.post("/api/career", json={"career": "x"}).status_code == 422          # too short


def test_api_stream(client, no_search):
    with client.stream("POST", "/api/career/stream", json={"career": "data engineer"}) as s:
        text = "".join(s.iter_text())
    blocks = [b for b in text.split("\n\n") if b]
    assert blocks[-1].startswith("event: done")
    done = json.loads(blocks[-1].split("data: ", 1)[1])
    assert done["career"]["id"] == "data_engineer" and done["trace"]["spans"]
    with client.stream("POST", "/api/career/stream", json={"career": "zzzz qqqq"}) as s:
        text = "".join(s.iter_text())
    last = [b for b in text.split("\n\n") if b][-1]
    assert last.startswith("event: error") and "Pick one of" in last and "CareerError" not in last
