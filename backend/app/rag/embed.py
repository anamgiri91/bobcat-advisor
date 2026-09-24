"""
embed.py
========
Reads chunks.jsonl, embeds every chunk using all-MiniLM-L6-v2,
and stores the vectors in a persistent ChromaDB collection.

Run once before starting the app:
    python embed.py --chunks data/chunks.jsonl --db data/chroma_db

Re-running is safe and incremental: chunk IDs are content hashes, so only
new chunks are embedded and chunks removed from chunks.jsonl are deleted.

Embedding backend: fastembed (ONNX Runtime), not sentence-transformers
(PyTorch). This must match retrieve.py's backend — ingesting with one
library and querying with another can produce subtly different vectors
for the same model name, which would quietly hurt retrieval quality.
If you already have an existing ChromaDB built with sentence-transformers,
delete data/chroma_db/ and re-run this script so all vectors come from
the same fastembed backend.

Dependencies:
    pip install chromadb fastembed
"""

import argparse
import json
from pathlib import Path

import chromadb
from fastembed import TextEmbedding

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COLLECTION_NAME = "txstate_cs_reviews"
EMBED_MODEL     = "all-MiniLM-L6-v2"
BATCH_SIZE      = 64


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_index(chunks_path: Path, db_path: Path) -> None:
    print(f"Loading chunks from {chunks_path} ...")
    all_chunks = [json.loads(line) for line in chunks_path.open(encoding="utf-8")]
    print(f"  {len(all_chunks)} chunks loaded")

    # Short reviews (< 50 words) are indexed too. Whether retrieval uses them
    # is a query-time policy (settings.INCLUDE_SHORT_REVIEWS), decided by the
    # eval harness rather than baked into the index.
    chunks = all_chunks
    print(f"  Indexing {len(chunks)} chunks "
          f"({sum(1 for c in chunks if c['metadata'].get('short_review'))} short reviews)")

    print(f"\nLoading embedding model: {EMBED_MODEL} ...")
    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

    print(f"\nConnecting to ChromaDB at {db_path} ...")
    db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Incremental sync: chunk IDs are content hashes, so an unchanged chunk
    # keeps its ID. Only embed IDs the index doesn't have yet, and delete IDs
    # whose source text no longer exists. A full rebuild is never needed.
    wanted_ids = {c["id"] for c in chunks}
    existing = collection.get(include=["metadatas"])
    existing_meta = dict(zip(existing["ids"], existing["metadatas"], strict=False))
    stale_ids = sorted(set(existing_meta) - wanted_ids)
    new_chunks = [c for c in chunks if c["id"] not in existing_meta]

    # Same text but re-cleaned metadata (e.g. course normalisation changed):
    # update metadata in place, no re-embedding needed.
    meta_changed = [
        c for c in chunks
        if c["id"] in existing_meta and existing_meta[c["id"]] != c["metadata"]
    ]

    if stale_ids:
        collection.delete(ids=stale_ids)
    for i in range(0, len(meta_changed), BATCH_SIZE):
        batch = meta_changed[i : i + BATCH_SIZE]
        collection.update(ids=[c["id"] for c in batch],
                          metadatas=[c["metadata"] for c in batch])
    print(f"\n  {len(stale_ids)} stale deleted, {len(new_chunks)} new, "
          f"{len(meta_changed)} metadata updated, "
          f"{len(chunks) - len(new_chunks) - len(meta_changed)} unchanged")

    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch      = new_chunks[i : i + BATCH_SIZE]
        full_texts = [c["text"]     for c in batch]
        ids        = [c["id"]       for c in batch]
        metas      = [c["metadata"] for c in batch]

        # fastembed's .embed() returns a generator of numpy arrays, one per
        # input text, in the same order as full_texts. Convert each to a
        # plain list[float] since that's what ChromaDB's upsert expects.
        embeddings = [vec.tolist() for vec in model.embed(full_texts)]

        collection.upsert(
            ids=ids,
            documents=full_texts,
            embeddings=embeddings,
            metadatas=metas,
        )

        done = min(i + BATCH_SIZE, len(new_chunks))
        print(f"  [{done:>4}/{len(new_chunks)}] upserted")

    print(f"\nDone. Collection '{COLLECTION_NAME}' has {collection.count()} documents.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embed chunks into ChromaDB")
    parser.add_argument("--chunks", default="data/chunks.jsonl",
                        help="Path to chunks.jsonl (default: data/chunks.jsonl)")
    parser.add_argument("--db", default="data/chroma_db",
                        help="ChromaDB persistence directory (default: data/chroma_db)")
    args = parser.parse_args()

    build_index(Path(args.chunks), Path(args.db))
