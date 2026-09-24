"""Hybrid index behaviour: filters, relaxation, dedup, balanced comparison."""

from app.rag.index import BM25, SearchFilters, get_index, tokenize


def test_tokenize_light_stemming():
    assert tokenize("The exams were curves") == ["exam", "curve"]


def test_bm25_prefers_exact_term():
    bm = BM25([tokenize("he curves the final"), tokenize("lectures are boring")])
    s = bm.scores(tokenize("curve"))
    assert s[0] > 0 and s[1] == 0


def test_professor_filter_is_strict():
    ix = get_index()
    for mode in ("dense", "bm25", "hybrid"):
        res = ix.search("does he curve", k=8, filters=SearchFilters(professors=["Lee Koh"]), mode=mode)
        assert res and all(r["metadata"]["professor"] == "Lee Koh" for r in res)


def test_course_filter_relaxes_but_professor_does_not():
    ix = get_index()
    # Koh has no CS4388 reviews: course filter relaxes, professor filter stays.
    res = ix.search("lectures", k=5, filters=SearchFilters(professors=["Lee Koh"], courses=["CS4388"]))
    assert res and all(r["metadata"]["professor"] == "Lee Koh" for r in res)


def test_results_are_deduplicated():
    ix = get_index()
    res = ix.search("Qasem computer architecture", k=10,
                    filters=SearchFilters(professors=["Apan Qasem"]))
    bodies = [" ".join(r["text"].lower().split()) for r in res]
    assert len(bodies) == len(set(bodies))


def test_hybrid_scores_are_descending():
    res = get_index().search("hard exams", k=8, filters=SearchFilters(professors=["Keshav Bhandari"]))
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)


def test_balanced_covers_every_named_professor():
    res = get_index().balanced("who is better", ["Jill Seaman", "Husain Gholoom"], ["CS1428"])
    profs = {r["metadata"]["professor"] for r in res}
    assert {"Jill Seaman", "Husain Gholoom"} <= profs


def test_balanced_without_names_picks_course_professors():
    res = get_index().balanced("best professor", None, ["CS3358"])
    profs = {r["metadata"]["professor"] for r in res if r["metadata"]["professor"]}
    assert len(profs) >= 3


def test_catalog_chunk_lookup():
    c = get_index().catalog_chunk("CS3358")
    assert c and c["metadata"]["chunk_type"] == "catalog"
