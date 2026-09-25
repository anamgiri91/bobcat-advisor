"""
index.py
========
Hybrid retrieval: dense (MiniLM embeddings) + sparse (BM25), fused with
Reciprocal Rank Fusion, optionally reranked by a cross-encoder.

Design decisions
----------------
Exact search in memory, Chroma as the store of record.
  The corpus is the course catalog (~50 chunks). At this size a brute-force
  cosine over numpy is faster than an ANN query and exact, and it lets us
  apply arbitrary Python filters (e.g. "any of this chunk's courses") that
  Chroma's `where` can't express. Chroma still persists the embeddings, so
  nothing is re-embedded at startup. Past ~50k chunks, move the dense leg
  back to collection.query() — the fusion code doesn't change.

Why hybrid?
  MiniLM is weak on exact tokens: course numbers and rare terms like
  "automata" or "Zybooks". BM25 is strong exactly there and weak on
  paraphrase ("programming for the web" vs "internet software
  development"). RRF combines ranks, not scores, so the two legs don't need
  calibrating against each other.

Filter relaxation.
  When a course filter matches fewer than `min_results` chunks it is
  dropped, and the span records that it happened.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import TypedDict

import numpy as np

from ..config import settings
from ..knowledge.corpus import chunk_courses
from ..tracing import span

COLLECTION_NAME = "txstate_cs_catalog"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RRF_K = 60
CANDIDATES_PER_LEG = 50
RERANK_POOL = 30


class RetrievedChunk(TypedDict):
    id: str
    text: str
    metadata: dict
    score: float          # higher is better (fused / reranked)
    scores: dict          # per-leg diagnostics: dense, bm25, rrf, rerank


@dataclass
class SearchFilters:
    courses: list[str] | None = None
    sources: list[str] | None = None        # source_dir values
    chunk_types: list[str] | None = None    # catalog

    def describe(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

_STOPWORDS = set("""a an and are as at be but by for from has have he her his i if in is it
its me my of on or our she so that the their them they this to was we were what when which
who will with you your do does did about how than then there these those can could would
should very really just also into out up more most some any all not no""".split())


def tokenize(text: str) -> list[str]:
    # Letters and digits split apart, so "CS3358" matches the catalog's "CS 3358".
    tokens = re.findall(r"[a-z]+|[0-9]+", text.lower())
    out = []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 2:
            continue
        # Light stemming: plurals only. Enough for exam/exams, curve/curves.
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_tf = [Counter(d) for d in docs]
        self.doc_len = np.array([len(d) for d in docs], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        s = np.zeros(len(self.doc_tf), dtype=np.float32)
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / max(self.avgdl, 1e-6))
        for t in set(query_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            tf = np.array([d.get(t, 0) for d in self.doc_tf], dtype=np.float32)
            s += idf * tf * (self.k1 + 1) / (tf + norm)
        return s


# ---------------------------------------------------------------------------
# Models (lazy singletons)
# ---------------------------------------------------------------------------

_embedder = None
_reranker = None
_model_lock = threading.Lock()


def embed_query(text: str) -> np.ndarray:
    global _embedder
    with _model_lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(model_name=EMBED_MODEL)
    vec = np.asarray(list(_embedder.embed([text]))[0], dtype=np.float32)
    return vec / (np.linalg.norm(vec) + 1e-12)


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    global _reranker
    with _model_lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            _reranker = TextCrossEncoder(model_name=RERANK_MODEL)
    return [float(s) for s in _reranker.rerank(query, texts)]


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class HybridIndex:
    def __init__(self, db_path: str):
        import chromadb
        collection = chromadb.PersistentClient(path=db_path).get_collection(COLLECTION_NAME)
        data = collection.get(include=["embeddings", "documents", "metadatas"])
        self.ids: list[str] = data["ids"]
        self.texts: list[str] = data["documents"]
        self.metas: list[dict] = data["metadatas"]
        emb = np.asarray(data["embeddings"], dtype=np.float32)
        self.emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12)
        self.bm25 = BM25([tokenize(t) for t in self.texts])
        self.courses = [set(chunk_courses(m)) for m in self.metas]
        self.by_id = {cid: i for i, cid in enumerate(self.ids)}

    def __len__(self) -> int:
        return len(self.ids)

    # -- filtering ----------------------------------------------------------

    def _mask(self, f: SearchFilters | None) -> np.ndarray:
        mask = np.ones(len(self.ids), dtype=bool)
        if f is None:
            return mask
        for i, m in enumerate(self.metas):
            if f.courses and not (self.courses[i] & set(f.courses)):
                mask[i] = False
            elif f.sources and m.get("source_dir") not in f.sources:
                mask[i] = False
            elif f.chunk_types and m.get("chunk_type") not in f.chunk_types:
                mask[i] = False
        return mask

    def _chunk(self, i: int, score: float, scores: dict) -> RetrievedChunk:
        return RetrievedChunk(id=self.ids[i], text=self.texts[i], metadata=self.metas[i],
                              score=score, scores=scores)

    # -- search ---------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = 8,
        filters: SearchFilters | None = None,
        mode: str = "hybrid",
        rerank: bool | None = None,
        min_results: int = 1,
    ) -> list[RetrievedChunk]:
        """
        mode: "hybrid" | "dense" | "bm25" — the eval harness compares all three.
        """
        rerank = settings.RERANKER_ENABLED if rerank is None else rerank

        with span("retrieval.search", mode=mode, k=k, rerank=rerank,
                  filters=filters.describe() if filters else {}) as s:
            mask = self._mask(filters)
            if filters and filters.courses and mask.sum() < min_results:
                mask = self._mask(SearchFilters(None, filters.sources, filters.chunk_types))
                s.attributes["relaxed_course_filter"] = True
            candidates = np.flatnonzero(mask)
            s.attributes["candidates"] = int(len(candidates))
            if len(candidates) == 0 or not query.strip():
                return []

            fused: dict[int, float] = {}
            diag: dict[int, dict] = {}

            if mode in ("hybrid", "dense"):
                qv = embed_query(query)
                dense = self.emb[candidates] @ qv
                order = candidates[np.argsort(-dense)][:CANDIDATES_PER_LEG]
                dense_by_idx = dict(zip(candidates.tolist(), dense.tolist(), strict=False))
                for rank, i in enumerate(order.tolist()):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
                    diag.setdefault(i, {})["dense"] = round(dense_by_idx[i], 4)

            if mode in ("hybrid", "bm25"):
                bm = self.bm25.scores(tokenize(query))[candidates]
                order = candidates[np.argsort(-bm)][:CANDIDATES_PER_LEG]
                bm_by_idx = dict(zip(candidates.tolist(), bm.tolist(), strict=False))
                for rank, i in enumerate(order.tolist()):
                    if bm_by_idx[i] <= 0:
                        break
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
                    diag.setdefault(i, {})["bm25"] = round(bm_by_idx[i], 3)

            unique = sorted(fused, key=fused.get, reverse=True)

            if rerank and unique:
                pool = unique[:RERANK_POOL]
                rs = rerank_scores(query, [self.texts[i] for i in pool])
                for i, r in zip(pool, rs, strict=False):
                    diag[i]["rerank"] = round(r, 3)
                unique = [i for _, i in sorted(zip(rs, pool, strict=False), reverse=True)] + unique[RERANK_POOL:]

            results = []
            for i in unique[:k]:
                d = diag.get(i, {})
                d["rrf"] = round(fused[i], 5)
                score = d.get("rerank", fused[i])
                results.append(self._chunk(i, score, d))
            s.attributes["returned"] = len(results)
            return results

    def catalog_chunk(self, course: str) -> RetrievedChunk | None:
        for i, m in enumerate(self.metas):
            if m.get("chunk_type") == "catalog" and course in self.courses[i]:
                return self._chunk(i, 1.0, {"pinned": True})
        return None


_indexes: dict[str, HybridIndex] = {}
_index_lock = threading.Lock()


def get_index(db_path: str | None = None) -> HybridIndex:
    db_path = db_path or settings.CHROMA_DB_PATH
    with _index_lock:
        if db_path not in _indexes:
            _indexes[db_path] = HybridIndex(db_path)
        return _indexes[db_path]
