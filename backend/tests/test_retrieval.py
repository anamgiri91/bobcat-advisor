"""Hybrid index behaviour over the catalog: tokenisation, filters, relaxation."""

from app.rag.index import BM25, SearchFilters, get_index, tokenize


def test_tokenize_light_stemming():
    assert tokenize("The exams were curves") == ["exam", "curve"]


def test_tokenize_splits_course_codes():
    assert tokenize("CS3358") == tokenize("CS 3358") == ["cs", "3358"]


def test_bm25_prefers_exact_term():
    bm = BM25([tokenize("lexical analysis and parsing"), tokenize("computer networks")])
    s = bm.scores(tokenize("parsing"))
    assert s[0] > 0 and s[1] == 0


def test_index_holds_only_the_catalog():
    ix = get_index()
    assert len(ix) >= 40
    assert {m["chunk_type"] for m in ix.metas} == {"catalog"}
    assert all("professor" not in m for m in ix.metas)


def test_topic_search_finds_the_course():
    res = get_index().search("lexical analysis and parsing", k=3, mode="bm25")
    assert res[0]["metadata"]["course"] == "CS4318"


def test_course_filter():
    res = get_index().search("algorithms", k=5, mode="bm25", filters=SearchFilters(courses=["CS3358"]))
    assert res and all(r["metadata"]["course"] == "CS3358" for r in res)


def test_unknown_course_filter_relaxes():
    res = get_index().search("networks", k=3, mode="bm25", filters=SearchFilters(courses=["CS9999"]))
    assert res


def test_hybrid_scores_are_descending():
    res = get_index().search("machine learning and neural networks", k=8)
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)


def test_catalog_chunk_lookup():
    c = get_index().catalog_chunk("CS3358")
    assert c and c["metadata"]["chunk_type"] == "catalog"
