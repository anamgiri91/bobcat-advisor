#!/usr/bin/env bash
# Builds the ChromaDB vector index from documents/.
# Run once before starting the API (locally or from inside the backend
# container: docker compose exec backend bash scripts/build_index.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

python -m app.rag.ingest --documents-dir documents --out data/chunks.jsonl
python -m app.rag.embed --chunks data/chunks.jsonl --db data/chroma_db
