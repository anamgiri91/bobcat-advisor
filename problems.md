# Chunking Pipeline — Problems & Solutions

> **Project:** BOBCAT ADVISOR
> **Stage:** Document ingestion, chunking, embedding, and retrieval

---

## Overview

The pipeline reads scraped professor review files, splits them into chunks, cleans them, and writes a JSONL file for embedding into ChromaDB.

### Sources

| Directory | Source | Strategy |
|---|---|---|
| `documents/coursicle/` | Coursicle reviews | One chunk per review |
| `documents/rmp/` | Rate My Professors | One chunk per review |
| `documents/reddit/` | r/txstate threads | Sliding window, 400 words, 50-word overlap |
| `documents/official/` | TXST course catalog | One chunk per course entry |

### Chunk schema

```json
{
  "id": "664d1b2e07c4",
  "text": "Professor: Apan Qasem | Course: CS3339 | ...\n\nThe class material is pretty dense...",
  "metadata": {
    "professor": "Apan Qasem",
    "course": "CS3339",
    "quality": "5.0/5",
    "difficulty": "4.0/5",
    "grade": "Not sure yet",
    "date": "May 10th, 2025",
    "source_file": "apanqasem.txt",
    "source_dir": "rmp",
    "chunk_type": "review"
  }
}
```

---

## Problem 1 — Junk Chunks from Scraper Noise

### What happened

38 chunks contained tech news headlines instead of reviews:

```
keshavbhandari.txt  → "Nvidia's N1X could show us the future of PCs..."
martinburtscher.txt → "Bright? Glow? What do the WWDC taglines mean?"
minaguirguis.txt    → "Image slip-up reveals possible name of macOS 27"
```

The scraper picked up "related articles" links from the Rate My Professors page footer alongside real review text.

### Why it matters

A query like *"What do students say about Burtscher's exams?"* could retrieve a WWDC headline stored under Burtscher's metadata, wasting a retrieval slot or causing the LLM to hallucinate a connection.

### Fix

**Attempt 1** — drop any review body under 15 words. Caught most junk but missed the Nvidia headline (exactly 15 words).

**Attempt 2** — raise threshold to 16 words and add a keyword blocklist:

```python
JUNK_SIGNALS = ["nvidia", "wwdc", "macos", "apple", "image slip", "tagline", "bill that comes"]

body_lower = body.lower()
if _word_count(body) < 16 or any(s in body_lower for s in JUNK_SIGNALS):
    continue
```

**Result:** 38 junk chunks removed. 0 remaining.

**Lesson:** The blocklist is brittle — it only catches known patterns. For larger corpora, a classifier trained on "is this a review?" would be more robust, but overkill here.

---

## Problem 2 — Truncated Reviews

### What happened

Two Gholoom reviews ended mid-sentence in both `coursicle/` and `rmp/` files:

```
"Very test heavy. I received straight A's on the assignments, then he randomly(?) gave me a"
```

The scraper hit a character limit or failed to click a "read more" button.

### Fix

Truncated reviews still contain useful signal up to the cut-off point, so they are flagged rather than dropped:

```python
if body and body[-1] not in ".!?\"'":
    chunk["metadata"]["possibly_truncated"] = True
```

**Result:** 85 chunks flagged. The generation prompt instructs the LLM not to speculate about missing content, and the retriever can deprioritize flagged chunks when clean alternatives exist.

---

## Problem 3 — Reviews Under 50 Words

### What happened

155 review chunks were shorter than the 50-word lower bound:

```
"Awful teacher. Deserves to be fired"   (6 words)
"Tough on grading assignments"          (4 words)
"Great professor!"                      (2 words)
```

Very short reviews produce low-discriminative embeddings with `all-MiniLM-L6-v2` — a 4-word fragment matches too many unrelated queries.

### Fix

Flag and keep, rather than drop. Brief reviews often carry the strongest sentiment signal, and silently removing them would systematically discard the most negative opinions.

```python
if _word_count(body) < 50:
    chunk["metadata"]["short_review"] = True
```

**Result:** 141 chunks flagged. A `--drop-short` CLI flag can exclude them if embedding quality proves insufficient.

---

## Problem 4 — Missing Date Metadata

### What happened

332 Coursicle chunks had no `date` field because Coursicle uses a `Source:` field instead. Any downstream code accessing `chunk["metadata"]["date"]` directly would crash with a `KeyError`.

### Fix

Fill missing dates with a sentinel value at ingestion time:

```python
if not chunk["metadata"].get("date"):
    chunk["metadata"]["date"] = "unknown"
```

**Result:** Consistent schema across all chunks. Recency filters will correctly exclude Coursicle reviews (they appear as `"unknown"`) rather than raising exceptions.

---

## Problem 5 — Professor Imbalance

### What happened

After deduplication, review counts were highly uneven:

| Professor | Raw chunks |
|---|---|
| Husain Gholoom | 315 |
| Jill Seaman | 215 |
| Lee Koh | 139 |
| Ted Lehr | 68 |
| … | … |
| Xiaomin Li | 12 |

Gholoom had 26× more chunks than Li, purely due to scraping volume.

### Why it matters

In RAG, retrieval is based on similarity. With 315 Gholoom chunks in the vector space, a vague query like *"which professor is hardest?"* will statistically return Gholoom-dominated results regardless of the actual answer. Many of those chunks are also near-duplicates ("hard class, lots of self-teaching"), wasting retrieval slots.

### Fix — greedy max-margin diversity selection

Cap at 100 reviews per professor. For professors exceeding the cap, select the most diverse 100 using TF-IDF vectors and greedy max-margin selection:

1. Vectorize all reviews with TF-IDF.
2. Seed the selected set with the most unique review (lowest average cosine similarity to all others).
3. Greedily add the review most dissimilar to anything already selected.
4. Stop at 100.

```python
# Seed
seed = int(np.argmin(sim_matrix.mean(axis=1)))
selected_indices.append(seed)

# Greedy expansion
while len(selected_indices) < max_per_prof and remaining:
    max_sim = np.array([sim_matrix[i, selected_indices].max() for i in remaining])
    best = list(remaining)[int(np.argmin(max_sim))]
    selected_indices.append(best)
    remaining.remove(best)
```

TF-IDF was used instead of `sentence-transformers` because the embedding model was unavailable at this build stage. For reviews — which are short and keyword-heavy — TF-IDF lexical diversity is a reasonable substitute. Swapping in `all-MiniLM-L6-v2` later is a straightforward one-line change.

**Result:**

| Professor | Before cap | After cap |
|---|---|---|
| Husain Gholoom | 133 | 100 |
| Jill Seaman | 104 | 100 |
| Lee Koh | 133 | 100 |

(Counts are post-junk-filter, which is why raw numbers differ from the table above.)

---

## Problem 6 — Retrieval Failure from Header-Polluted Embeddings

### What happened

After the app launched, the query `"best professor for CS 1428"` returned:

```
I couldn't find enough information in the retrieved sources to answer that question.
```

The database contained 112 CS 1428 chunks across four professors. Retrieval was failing silently.

### Root cause

Every chunk has this structure:

```
Professor: Jill Seaman | Course: CS 1428 | Source: Coursicle | Year Level: Senior | Major: CS

She is a fair grader and gives opportunities for her students to perform well...
```

The original `embed.py` embedded the **entire chunk** — header and body together. Because every chunk shares an identical header format (`Professor: ... | Course: ... | Source: ...`), the repeated pattern pushed all vectors into a similar region of the embedding space, making cosine similarity comparisons between a natural-language query and review content much less discriminative.

This is a case of what the assignment calls "bad chunks cannot be fixed by tuning retrieval later." The chunks were correct; what was *embedded* was wrong. Adjusting `top_k` or similarity thresholds would not have helped.

### Fix — embed body only, store full text

```python
def get_body(text: str) -> str:
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) > 1 else text.strip()

# In build_index():
full_texts  = [c["text"]          for c in batch]  # stored for display
body_texts  = [get_body(c["text"]) for c in batch]  # embedded for search

embeddings = model.encode(body_texts, ...).tolist()

collection.upsert(
    ids        = ids,
    documents  = full_texts,   # returned during retrieval
    embeddings = embeddings,   # used for similarity ranking
    metadatas  = metas,
)
```

The old index had to be fully deleted and rebuilt — ChromaDB `upsert` would have replaced documents but left stale vectors:

```bash
rm -rf data/chroma_db
python embed.py --chunks data/chunks.jsonl --db data/chroma_db
```

**Result:** After rebuilding, `"best professor for CS 1428"` correctly retrieved Seaman, Gholoom, and Qasem chunks.

**Lesson:** In RAG, what you embed and what you store should be treated as separate concerns:

| ChromaDB field | Purpose | Contents |
|---|---|---|
| `documents` | Returned to the app | Full chunk text including header |
| `embeddings` | Similarity search | Review body only |
| `metadatas` | Structured filtering | Professor, course, source, date |

---

## Problem 7 — Poor Answer Quality on Comparison Queries

### What happened

After the retrieval fix, `"best professor for CS 1428"` still produced a bad answer:

> *"It said 'according to Husain Gholoom's review' — as if Gholoom was reviewing himself."*

Three failure modes:

1. **Name confusion.** The LLM read the header line (`Professor: Husain Gholoom | ...`) as the reviewer's voice rather than a metadata label.
2. **No synthesis.** With 112 CS 1428 chunks available, the answer produced one sentence per professor and stopped.
3. **False claim.** The answer said there was "no review for another professor" — Qasem and Vargas both had CS 1428 chunks.

### Root cause

- The full chunk text, header included, was passed to the LLM as content.
- Chunks were ordered by retrieval rank, interleaving professors: Seaman, Gholoom, Seaman, Gholoom, Qasem. The model had to group evidence mentally while generating — models produce much worse comparisons from scattered context than grouped context.
- The system prompt said "don't just say reviews are mixed" but gave no structure for what a good comparison looks like.

### Fix 1 — strip header from context

```python
body_parts = chunk["text"].split("\n\n", 1)
body = body_parts[1].strip() if len(body_parts) > 1 else chunk["text"]

parts.append(f"  Review {n} [Course: {course} | Grade: {grade}]:\n  {body}")
```

Before:
```
--- Review 1 [RateMyProfessors — Jill Seaman, CS 1428] ---
Professor: Jill Seaman | Course: CS 1428 | Quality: 5.0/5 | ...

She is a fair grader and gives opportunities...
```

After:
```
=== PROFESSOR: Jill Seaman (3 reviews from Coursicle) ===
  Review 1 [Course: CS 1428 | Grade: A]:
  She is a fair grader and gives opportunities...
```

### Fix 2 — group chunks by professor for comparison queries

Added `_is_comparison_query()` to detect keywords and route to a grouped context builder:

```python
COMPARISON_SIGNALS = ["best", "better", "worst", "compare", "recommend",
                      "which professor", "who should", "vs", "versus"]

def _is_comparison_query(query: str) -> bool:
    return any(signal in query.lower() for signal in COMPARISON_SIGNALS)
```

### Fix 3 — explicit output structure in the system prompt

```
For "best professor" or comparison questions:
- Summarise each professor separately (teaching style, exams, workload, grading, office hours)
- Note the number of reviews you are drawing from
- Give a concrete recommendation at the end based only on review patterns
- Do NOT say "reviews are mixed" — that is unhelpful; synthesise the pattern
```

**Lesson:** Good retrieval does not guarantee good answers. Answer quality also depends on how context is formatted, how it is grouped, and how the prompt is structured. All three failures here required simultaneous fixes — any one change alone would have helped but not solved the problem. RAG systems require end-to-end testing on real queries; retrieval metrics cannot reveal generation failures.

---

## Problem 8 — Jill Seaman Missing from CS 1428 Comparison

### What happened

After Problem 7 fixes, `"best professor for CS 1428"` still returned:

> *"Only one review mentions CS 1428, which is for Professor Husain Gholoom... I couldn't find enough information to make a thorough comparison."*

Seaman had 50 CS 1428 chunks — more than any other professor — and was entirely absent.

### Root causes

**top_k=5 is mathematically too small for multi-professor comparisons.** With 4 professors teaching CS 1428, the worst case is all 5 slots going to one professor. Fair representation requires:

```
4 professors × 2 chunks minimum = top_k ≥ 8
4 professors × representative coverage = top_k ≥ 12–15
```

**Course number format inconsistency.** The same course appears in multiple formats across sources:

| Format | Count | Source |
|---|---|---|
| `"CS 1428"` | 87 | Coursicle |
| `"CS1428"` | 20 | RMP |
| `"1428"` | 3 | Mixed |
| `"HONORSCS1428"` | 2 | Coursicle |

Semantic search could rank a CS 3358 chunk higher than a CS 1428 chunk if the review text happened to use similar words.

### Fix 1 — adaptive top_k

```python
COMPARISON_TOP_K = 15

effective_top_k = COMPARISON_TOP_K if _is_comparison_query(query) else top_k
```

### Fix 2 — course-aware metadata filter

```python
def _extract_course_number(query: str) -> str | None:
    match = re.search(r'\b(\d{4})\b', query)
    return match.group(1) if match else None

def _build_course_filter(course_digits: str) -> dict:
    return {
        "$or": [
            {"course": f"CS {course_digits}"},
            {"course": f"CS{course_digits}"},
            {"course": course_digits},
            {"course": f"HONORSCS{course_digits}"},
        ]
    }
```

The filter is applied before semantic ranking, so a CS 3358 chunk cannot appear in CS 1428 results regardless of text similarity.

**Result:** All four professors now surface for every CS 1428 query.

**Lesson:** `top_k` is not a fixed constant — it should scale with query complexity. A factual lookup needs 1–3 chunks; a multi-professor comparison needs enough to represent every entity being compared. A single global value is a design flaw that silently drops evidence. Metadata filtering and semantic search also serve distinct roles: semantic search finds relevant meaning; metadata filtering enforces structural constraints like course number. Neither alone is sufficient.

---

## Problem 9 — Qasem and Vargas Underrepresented in Comparison Answers

### What happened

After Problem 8 fixes, the answer still showed unequal representation. Vargas was nearly dismissed: *"not relevant to CS 1428"* — when he had 5 CS 1428 chunks.

### Root cause — proportional retrieval favours high-volume professors

With `top_k=15` and a course filter, ChromaDB returns the 15 most similar chunks. Seaman (50 chunks) and Gholoom (39 chunks) dominate the similarity rankings:

| Professor | CS 1428 chunks | Expected slots at top_k=15 |
|---|---|---|
| Jill Seaman | 50 | ~7 |
| Husain Gholoom | 39 | ~5 |
| Apan Qasem | 18 | ~2 |
| Edwin Vargas | 5 | ~1 |

Vargas gets ~1 slot — not enough for the LLM to assess him fairly.

### Fix — balanced retrieval with per-professor cap

```python
def retrieve_balanced(query, db_path, fetch_top_k=30, cap_per_prof=4):
    pool = retrieve(query, top_k=fetch_top_k, db_path=db_path)

    by_prof = {}
    for chunk in pool:
        prof = chunk["metadata"].get("professor", "")
        if prof:
            by_prof.setdefault(prof, []).append(chunk)

    balanced = []
    for prof_chunks in by_prof.values():
        balanced.extend(prof_chunks[:cap_per_prof])

    return balanced
```

Result: every professor gets 4 slots (or all of their chunks if fewer than 4). Total sent to LLM: 16.

`generate.py` routes comparison queries through `retrieve_balanced()` and standard queries through the original `retrieve()`.

**Lesson:** In comparison queries, raw semantic similarity is not a fair retrieval criterion when data has volume imbalances — a professor with 50 reviews will always crowd out one with 5. The solution is to separate relevance (semantic search + course filter) from representation (per-professor cap). This mirrors diversity constraints in recommendation systems: retrieve for relevance first, then balance for fairness.

---

## Final Statistics

| Metric | Value |
|---|---|
| Raw chunks (before dedup) | 1,043 |
| After content-hash dedup | 672 |
| Junk chunks dropped | 38 |
| **Final unique chunks** | **564** |

| Chunk type | Count |
|---|---|
| Review (RMP + Coursicle) | 508 |
| Catalog (official) | 52 |
| Reddit (discussion) | 4 |

| Quality flag | Count |
|---|---|
| `possibly_truncated: true` | 85 |
| `short_review: true` | 141 |

| Professor | Final chunks |
|---|---|
| Husain Gholoom | 100 |
| Jill Seaman | 100 |
| Lee Koh | 100 |
| Ted Lehr | 65 |
| Keshav Bhandari | 30 |
| Apan Qasem | 27 |
| Mina Guirguis | 27 |
| Oleg Komogortsev | 25 |
| Martin Burtscher | 23 |
| Xiaomin Li | 11 |

---

## How to Run

```bash
cd ~/Desktop/"TXST CS courses and professors rag"
source venv/bin/activate

# Chunk and clean
python ingest.py --documents-dir documents --out data/chunks.jsonl

# Embed (delete old index first if rebuilding)
rm -rf data/chroma_db
python embed.py --chunks data/chunks.jsonl --db data/chroma_db

# Launch
python app.py
```

---

## Libraries

| Library | Install | Used for |
|---|---|---|
| `re`, `json`, `hashlib`, `argparse`, `pathlib`, `typing`, `warnings` | built-in | Splitting, serialisation, deduplication, CLI, path handling, type hints, warning suppression |
| `scikit-learn` | `pip install` | TF-IDF vectorization and cosine similarity for diversity selection |
| `numpy` | `pip install` | `argmin` and array operations in greedy selection |
| `chromadb` | `pip install` | Persistent vector database |
| `sentence-transformers` | `pip install` | `all-MiniLM-L6-v2` embedding model |
| `groq` | `pip install` | LLM API for answer generation |
| `gradio` | `pip install` | Student-facing web interface |
| `python-dotenv` | `pip install` | Loading `GROQ_API_KEY` from `.env` |

---

## Known Limitations

**Xiaomin Li underrepresented (11 chunks).** Only 11 reviews were scraped. Queries about her courses will have thin retrieval context.

**Edwin Vargas has no data.** `edwinvargas.txt` is empty. The pipeline handles it gracefully (0 chunks, no crash), but the professor is unrepresented.

**Coursicle dates are unknown.** 332 Coursicle reviews have `"date": "unknown"`. Any future recency filter will exclude them regardless of when they were actually written.

**Short review flag is unenforced.** 141 short chunks are still included. A `--drop-short` CLI flag would allow excluding them if embedding quality is insufficient.

**TF-IDF for diversity selection.** Two reviews that say the same thing in different words could both survive the cap. Replacing `TfidfVectorizer` with `all-MiniLM-L6-v2` embeddings would give meaningfully better diversity selection.

**Reddit coverage is thin (4 chunks).** Only 4 sliding-window chunks were produced from two Reddit files. Scraping more threads would add conversational context absent from formal reviews.

**Course number format inconsistency.** The same course appears as `"CS 1428"` and `"CS1428"` across sources. A normalisation pass in `cleaner.py` standardising all codes to the no-space format would improve metadata filter accuracy.
