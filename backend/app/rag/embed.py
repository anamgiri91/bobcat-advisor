"""
embed.py
========
Reads chunks.jsonl, embeds every chunk using all-MiniLM-L6-v2,
and stores the vectors in a persistent ChromaDB collection.

Run once before starting the app:
    python embed.py --chunks data/chunks.jsonl --db data/chroma_db

Re-running is safe: ChromaDB upserts by chunk ID, so duplicates
are overwritten rather than inserted twice.

Dependencies:
    pip install chromadb sentence-transformers
"""

import json
import argparse
from pathlib import Path

from sentence_transformers import SentenceTransformer
import chromadb


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

    # Exclude short_review chunks from embedding (body < 50 words)
    chunks = [c for c in all_chunks if not c["metadata"].get("short_review")]
    skipped = len(all_chunks) - len(chunks)
    print(f"  Skipping {skipped} short_review chunks (body < 50 words)")
    print(f"  Embedding {len(chunks)} chunks")

    print(f"\nLoading embedding model: {EMBED_MODEL} ...")
    model = SentenceTransformer(EMBED_MODEL)

    print(f"\nConnecting to ChromaDB at {db_path} ...")
    db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    print(f"\nEmbedding and upserting in batches of {BATCH_SIZE} ...")
    for i in range(0, len(chunks), BATCH_SIZE):
        batch      = chunks[i : i + BATCH_SIZE]
        full_texts = [c["text"]     for c in batch]
        ids        = [c["id"]       for c in batch]
        metas      = [c["metadata"] for c in batch]
        embeddings = model.encode(full_texts, show_progress_bar=False).tolist()

        collection.upsert(
            ids=ids,
            documents=full_texts,
            embeddings=embeddings,
            metadatas=metas,
        )

        done = min(i + BATCH_SIZE, len(chunks))
        print(f"  [{done:>4}/{len(chunks)}] upserted")

    print(f"\nDone. Collection '{COLLECTION_NAME}' has {collection.count()} documents.")
    print(f"Note: {skipped} short_review chunks were excluded from the index.")
    print("If rebuilding: delete data/chroma_db/ before re-running.")


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