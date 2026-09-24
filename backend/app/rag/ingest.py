"""
ingest.py
=========
Orchestrates the full ingestion pipeline and exposes the CLI.

This file does not implement chunking or cleaning itself — it imports
those responsibilities from chunker.py and cleaner.py respectively.

Pipeline steps:
  1. Walk documents/<subdir>/*.txt
  2. Route each subdirectory to the correct chunker  (chunker.py)
  3. Deduplicate chunks — two stages (see below)
  4. Run all cleaning passes                         (cleaner.py)
  5. Save to JSONL and/or upsert into ChromaDB

DEDUPLICATION
-------------
Stage A — full-text MD5 hash:
  Catches exact duplicates where the entire chunk text is byte-for-byte
  identical. This is the only dedup stage now.

  Stage B (cross-source body fingerprint) was removed because it was
  collapsing reviews from coursicle/ and rmp/ that shared the same text
  but carried different metadata (grade, date, year level, major). Those
  differences are useful context for the LLM, so we keep both copies.

Usage
-----
  python ingest.py --documents-dir documents
  python ingest.py --documents-dir documents --preview 5
  python ingest.py --documents-dir documents --out output/chunks.jsonl
  python ingest.py --documents-dir documents --out chunks.jsonl --chroma
"""

import argparse
import json
from pathlib import Path

from .chunker import CHUNKER_MAP, SUBDIR_STRATEGY
from .cleaner import clean_chunks

# ---------------------------------------------------------------------------
# Ingestion orchestrator
# ---------------------------------------------------------------------------

def ingest_all(documents_dir: Path) -> list[dict]:
    """
    Walk the documents directory, chunk every file, deduplicate, and clean.

    Parameters
    ----------
    documents_dir : Path
        Path to the documents/ folder.

    Returns
    -------
    list[dict]
        Cleaned, deduplicated chunks ready for embedding.
    """
    if not documents_dir.exists():
        raise FileNotFoundError(f"documents/ directory not found: {documents_dir}")

    raw_chunks: list[dict] = []

    # --- Step 1 & 2: walk subdirectories and chunk each file ---------------
    for subdir_name, strategy in SUBDIR_STRATEGY.items():
        subdir = documents_dir / subdir_name
        if not subdir.exists():
            print(f"[WARN] Subdirectory not found, skipping: {subdir}")
            continue

        txt_files = sorted(subdir.glob("*.txt"))
        if not txt_files:
            print(f"[WARN] No .txt files found in: {subdir}")
            continue

        chunker_fn = CHUNKER_MAP[strategy]
        print(f"\n=== {subdir_name}/ ({strategy}) ===")
        for path in txt_files:
            file_chunks = list(chunker_fn(path, subdir_name))
            print(f"  {path.name:<30} {len(file_chunks):>4} chunks")
            raw_chunks.extend(file_chunks)

    # --- Step 3: two-stage deduplication ------------------------------------

    # Stage A: full-text MD5 hash — catches byte-identical duplicates
    seen_ids: set[str] = set()
    after_stage_a: list[dict] = []
    for chunk in raw_chunks:
        if chunk["id"] not in seen_ids:
            seen_ids.add(chunk["id"])
            after_stage_a.append(chunk)

    # Stage B removed: cross-source body dedup was collapsing reviews from
    # coursicle/ and rmp/ that shared the same text but had different metadata
    # (grade, date, year level, major). Removing it keeps all unique-ID chunks
    # so every professor has full coverage across both sources.
    unique = after_stage_a

    stage_a_dropped = len(raw_chunks) - len(after_stage_a)

    print(f"\n{'─'*50}")
    print(f"Raw chunks  : {len(raw_chunks)}")
    print(f"After dedup : {len(unique)}"
          f"  (−{stage_a_dropped} exact duplicates)")

    # --- Step 4: clean ------------------------------------------------------
    print("\nRunning cleaning passes...")
    cleaned, report = clean_chunks(unique)

    _print_report(report)
    print(f"Final chunk count   : {len(cleaned)}")
    return cleaned


def _print_report(report: dict) -> None:
    """Print the cleaning report returned by cleaner.clean_chunks()."""
    print(f"\n{'─'*50}")
    print("CLEANING REPORT")
    print(f"{'─'*50}")
    print(f"  Junk chunks dropped     : {report.get('junk_dropped', 0)}")
    for ex in report.get("junk_examples", []):
        print(f"    ↳ {ex!r}")
    print(f"  Truncated (flagged)     : {report.get('truncated_flagged', 0)}")
    print(f"  Short < 50w (flagged)   : {report.get('short_flagged', 0)}")
    print(f"  Missing dates filled    : {report.get('date_filled', 0)}")
    print(f"  Course codes normalised : {report.get('courses_normalised', 0)}")
    print(f"{'─'*50}")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_jsonl(chunks: list[dict], out_path: Path) -> None:
    """Write chunks to a JSONL file (one JSON object per line)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"\nSaved → {out_path}  ({len(chunks)} chunks)")


def ingest_to_chroma(chunks: list[dict],
                     collection_name: str = "txstate_cs_reviews",
                     persist_dir: str = "./chroma_db") -> None:
    """
    Embed and upsert all chunks into a local ChromaDB collection.
    `upsert` is idempotent: running twice won't create duplicates.

    Requires:  pip install chromadb
    """
    try:
        import chromadb
    except ImportError:
        print("chromadb not installed — run: pip install chromadb")
        return

    client     = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(name=collection_name)

    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        collection.upsert(
            ids       = [c["id"]       for c in batch],
            documents = [c["text"]     for c in batch],
            metadatas = [c["metadata"] for c in batch],
        )
        print(f"  Upserted {min(i + batch_size, len(chunks))}/{len(chunks)}")

    print(f"Collection '{collection_name}' → {collection.count()} total documents")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Chunk, clean, and ingest the TXST professor review corpus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingest.py --documents-dir documents
  python ingest.py --documents-dir documents --preview 5
  python ingest.py --documents-dir documents --out output/chunks.jsonl
  python ingest.py --documents-dir documents --out chunks.jsonl --chroma
        """,
    )
    parser.add_argument(
        "--documents-dir", default="documents",
        help="Path to the documents/ folder (default: ./documents)",
    )
    parser.add_argument(
        "--out", default="chunks.jsonl",
        help="Output JSONL path (default: chunks.jsonl)",
    )
    parser.add_argument(
        "--chroma", action="store_true",
        help="Also upsert chunks into a local ChromaDB instance",
    )
    parser.add_argument(
        "--chroma-dir", default="./chroma_db",
        help="ChromaDB persistence directory (default: ./chroma_db)",
    )
    parser.add_argument(
        "--preview", type=int, default=0, metavar="N",
        help="Print N sample chunks to stdout and exit without saving",
    )
    args = parser.parse_args()

    all_chunks = ingest_all(Path(args.documents_dir))

    if args.preview:
        for chunk in all_chunks[: args.preview]:
            print(json.dumps(chunk, indent=2, ensure_ascii=False))
        raise SystemExit(0)

    save_jsonl(all_chunks, Path(args.out))

    if args.chroma:
        ingest_to_chroma(all_chunks, persist_dir=args.chroma_dir)
