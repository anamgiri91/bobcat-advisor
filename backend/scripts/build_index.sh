#!/usr/bin/env bash
# Rebuilds the knowledge layer from documents/. Safe to re-run: embedding is
# incremental (only new/changed chunks are embedded, removed ones deleted).
#   bash scripts/build_index.sh            # chunks + vectors
set -euo pipefail
cd "$(dirname "$0")/.."

python -m app.rag.ingest --documents-dir documents --out data/chunks.jsonl
python -m app.rag.embed --chunks data/chunks.jsonl --db data/chroma_db
