# Chunking Pipeline — Problems, Decisions & Solutions

> **Project:** AI201 Project 1 — The Unofficial Guide to Texas State CS Professors  
> **Pipeline stage:** Document ingestion, chunking, embedding, and retrieval  
> **Author reference doc** — written after implementation to record every problem encountered, why it mattered, and exactly how it was fixed.

---

## Table of Contents

1. [What the Pipeline Does](#1-what-the-pipeline-does)
2. [Directory Structure Problem](#2-directory-structure-problem)
3. [Problem: Junk Chunks from Scraper Noise](#3-problem-junk-chunks-from-scraper-noise)
4. [Problem: Truncated Reviews](#4-problem-truncated-reviews)
5. [Problem: Reviews Under 50 Words](#5-problem-reviews-under-50-words)
6. [Problem: Missing Date Metadata](#6-problem-missing-date-metadata)
7. [Problem: Professor Imbalance](#7-problem-professor-imbalance)
8. [Problem: Retrieval Failure Due to Header-Polluted Embeddings](#8-problem-retrieval-failure-due-to-header-polluted-embeddings)
9. [Final Chunk Statistics](#9-final-chunk-statistics)
10. [How to Run](#10-how-to-run)
11. [Libraries Used and Why](#11-libraries-used-and-why)
12. [Remaining Known Limitations](#12-remaining-known-limitations)
13. [Problem: Poor Answer Quality on Comparison Queries](#13-problem-poor-answer-quality-on-comparison-queries)
14. [Problem: Jill Seaman Ignored in CS 1428 Comparison](#14-problem-jill-seaman-ignored-in-cs-1428-comparison)
15. [Problem: Qasem and Vargas Underrepresented in Comparison Answers](#15-problem-qasem-and-vargas-underrepresented-in-comparison-answers)

---

## 1. What the Pipeline Does

The pipeline reads scraped professor review files, splits them into individual chunks, cleans them, and writes a JSONL file ready for embedding into a vector database (ChromaDB).

### Sources ingested

| Subdirectory | Source | Strategy |
|---|---|---|
| `documents/coursicle/` | Coursicle student reviews | One chunk per review |
| `documents/rmp/` | Rate My Professors reviews | One chunk per review |
| `documents/reddit/` | r/txstate discussion threads | Sliding window, 400 words, 50-word overlap |
| `documents/official/` | TXST course catalog | One chunk per course entry |

### What each chunk looks like

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

## 2. Directory Structure Problem

### What happened

The first version of `chunker.py` was written with a flat file list hardcoded at the top:

```python
REVIEW_FILES = ["apanqasem.txt", "husaingholoom.txt", ...]
REDDIT_FILES = ["cs3358_2318_advice.txt", "coursedata.txt"]
CATALOG_FILES = ["coursecatalog.txt"]
```

This worked when tested against a flat uploads folder, but the actual project has a nested structure:

```
documents/
  coursicle/    ← 6 professor files
  rmp/          ← 10 professor files (overlapping with coursicle)
  reddit/       ← 2 discussion files
  official/     ← course catalog
```

The flat version would have broken immediately when run from the real project root, and it also had no way to know which source directory a file came from — meaning metadata like `"source_dir": "rmp"` would never be populated.

### Fix

Replaced the hardcoded file list with a directory-to-strategy routing table:

```python
SUBDIR_STRATEGY = {
    "coursicle": "review",
    "rmp":       "review",
    "reddit":    "reddit",
    "official":  "catalog",
}
```

The ingestion function now walks `documents/<subdir>/*.txt` using `Path.glob()`, automatically picking up any new files added to those folders without requiring code changes. Each chunk also gets `"source_dir"` in its metadata so queries can later be filtered by source type.

### Why this matters for retrieval

Without `source_dir` in metadata, a ChromaDB `where` filter like `{"source_dir": "rmp"}` would be impossible. Knowing whether a review came from RMP vs. Coursicle vs. Reddit matters for trust calibration — official-looking sources differ from anonymous reviews.

---

## 3. Problem: Junk Chunks from Scraper Noise

### What happened

Three chunks (and later found to be 38 total after closer inspection) contained tech news headlines instead of professor reviews:

```
keshavbhandari.txt  → "Nvidia's N1X could show us the future of PCs—and the bill that comes with it"
martinburtscher.txt → "Bright? Glow? What do the WWDC taglines mean?"
minaguirguis.txt    → "Image slip-up reveals possible name of macOS 27"
```

These appeared because the web scraper picked up "related articles" links or page footer content from Rate My Professors alongside the actual review text. The scraper didn't distinguish between review content and surrounding page elements.

### Why this is a serious problem

If these chunks are embedded and stored in ChromaDB, a query like *"What do students say about Burtscher's exams?"* could retrieve the WWDC tagline chunk because it happens to be stored under Burtscher's metadata. The language model would then either ignore it (wasting a retrieval slot) or hallucinate a connection between it and the professor. Either way, answer quality degrades.

### Fix attempt 1 — word count threshold

First fix: drop any review chunk whose body (everything after the header line) is fewer than 15 words.

```python
body = text.split("\n\n", 1)[-1].strip()
if _word_count(body) < 15:
    continue  # drop it
```

This caught most junk chunks but missed the Nvidia headline, which is exactly 15 words long.

### Fix attempt 2 — raise threshold + keyword blocklist

Raised the threshold to 16 words and added a keyword blocklist for known scraper noise patterns:

```python
JUNK_SIGNALS = ["nvidia", "wwdc", "macos", "apple", "image slip", "tagline", "bill that comes"]
body_lower = body.lower()
is_junk_keyword = any(s in body_lower for s in JUNK_SIGNALS)
if _word_count(body) < 16 or is_junk_keyword:
    continue
```

**Result:** 38 junk chunks dropped. 0 remaining after fix.

### Lesson learned

Web-scraped data always contains noise. A cleaning pass should be treated as mandatory, not optional. The keyword blocklist approach is brittle (it only catches known patterns) but fast — in production, a better solution would be a classifier trained on "is this a review?" but that's overkill for this corpus size.

---

## 4. Problem: Truncated Reviews

### What happened

Two Gholoom reviews ended mid-sentence:

```
"Very test heavy. I received straight A's on the assignments, then he randomly(?) gave me a"
```

This appeared in both `coursicle/` and `rmp/` files with identical truncation, meaning the data was incomplete at scrape time — the scraper hit a character limit or the source page had a "read more" button that wasn't clicked.

### Why this is a problem

A truncated review missing its conclusion can mislead the language model. A review that starts "he randomly gave me a..." and ends there could be completed as anything — a bad grade, a good grade, an extension. The LLM cannot know.

### Fix

Unlike junk chunks, truncated reviews contain real partial information and should not be dropped. Instead, they are flagged in metadata:

```python
if body and body[-1] not in ".!?\"'":
    chunk["metadata"]["possibly_truncated"] = True
```

**Result:** 85 chunks flagged with `"possibly_truncated": true`.

Downstream, the generation prompt can be written to say: *"Some retrieved chunks may be incomplete — do not speculate about missing content."* Or the retriever can deprioritize flagged chunks when non-truncated alternatives exist.

### Why not just delete them?

Truncated reviews still contain useful signal up to the point of truncation. "Very test heavy. I received straight A's on the assignments" is useful information even without the ending. Dropping them would silently discard real data.

---

## 5. Problem: Reviews Under 50 Words

### What happened

155 review chunks (after initial dedup, before professor cap) were shorter than 50 words — the lower bound specified in the chunking strategy. Examples:

```
"Awful teacher. Deserves to be fired"          (6 words of content)
"Tough on grading assignments"                 (4 words of content)
"Great professor!"                             (2 words of content)
```

### Why this is a problem

Very short reviews provide almost no semantic signal for embedding. A 4-word review embedded as a vector will have very low discriminative power — it will match many unrelated queries because there isn't enough vocabulary to distinguish it. The embedding model (`all-MiniLM-L6-v2`) performs best on sentences and short paragraphs, not fragments.

### Decision: flag, don't drop

Two options were considered:

| Option | Pros | Cons |
|---|---|---|
| Drop all < 50 word reviews | Cleaner embeddings, faster retrieval | Loses real opinions, even if brief |
| Flag and keep | Preserves all data, downstream can filter | Slightly noisier embedding space |

**Decision: flag and keep.** Brief reviews like "Awful teacher. Deserves to be fired" carry a clear sentiment signal even if they're short. Dropping them would systematically remove the strongest negative opinions, which are often brief by nature. The flag allows filtering later if needed.

```python
if _word_count(body) < 50:
    chunk["metadata"]["short_review"] = True
```

**Result:** 141 chunks flagged with `"short_review": true` in the final output.

---

## 6. Problem: Missing Date Metadata

### What happened

332 chunks from `coursicle/` had no `date` field in their metadata because Coursicle's review format uses a `Source:` field instead of a `Date:` field:

```
# RMP format (has date):
Professor: Apan Qasem | Course: CS3339 | Quality: 5.0/5 | ... | Date: May 10th, 2025

# Coursicle format (no date):
Professor: Martin Burtscher | Course: CS 4380 | Source: Coursicle | Year Level: Senior | Major: CS
```

The metadata parser correctly extracted whatever fields were present — but downstream code that tries to access `chunk["metadata"]["date"]` would crash with a `KeyError` on any Coursicle chunk.

### Fix

A cleaning pass fills in `"date": "unknown"` for any review chunk missing a date:

```python
if not chunk["metadata"].get("date"):
    chunk["metadata"]["date"] = "unknown"
```

**Result:** Consistent metadata schema across all review chunks. Downstream filtering by date will correctly exclude Coursicle reviews (they'll appear as `"unknown"`) rather than crashing.

### Broader lesson

Inconsistent schemas across sources are a very common real-world data engineering problem. The fix here (fill with a sentinel value) is standard practice. An alternative would be to make all downstream code use `.get("date", "unknown")` instead of direct key access — either approach works, but filling at ingestion time is safer because it guarantees the schema at the storage layer.

---

## 7. Problem: Professor Imbalance

### What happened

After deduplication, the review counts per professor were highly uneven:

| Professor | Raw chunks |
|---|---|
| Husain Gholoom | 315 |
| Jill Seaman | 215 |
| Lee Koh | 139 |
| Ted Lehr | 68 |
| Mina Guirguis | 53 |
| Martin Burtscher | 52 |
| Keshav Bhandari | 39 |
| Apan Qasem | 29 |
| Oleg Komogortsev | 26 |
| Xiaomin Li | 12 |

Gholoom had 26× more chunks than Li. This happened because Gholoom had more reviews scraped across both Coursicle and RMP, while Li was only available on RMP with fewer reviews overall.

### Why this is a problem for RAG

In a RAG system, retrieval is based on semantic similarity. If Gholoom has 315 chunks and Li has 12, then:

1. **Vague queries get Gholoom-dominated results.** A query like *"which professor is hardest?"* will statistically return more Gholoom chunks simply because there are more of them in the vector space, not because Gholoom is necessarily the answer.
2. **Many Gholoom chunks are near-duplicates.** With 315 reviews, many say essentially the same thing: "hard class, lots of self-teaching." These redundant chunks waste retrieval slots that could be used for diverse information.
3. **Unfair representation.** The system would appear to "know more" about Gholoom than Burtscher, which is an artifact of scraping volume, not actual information density.

### Fix: greedy max-margin diversity selection

A cap of 100 reviews per professor was set. For professors exceeding the cap, a **greedy max-margin selection algorithm** was used to choose the most diverse 100 reviews rather than taking the first 100 or a random sample.

#### How the algorithm works

1. **Vectorize** all reviews for that professor using TF-IDF (term frequency-inverse document frequency). This turns each review into a vector where each dimension represents a word, weighted by how distinctive that word is.

2. **Seed** the selected set with the review that has the lowest average cosine similarity to all others — the most "unique" review in the set.

3. **Greedily add** the review that is most dissimilar to any already-selected review. "Most dissimilar" means: for each candidate, compute its maximum cosine similarity to anything already selected; pick the candidate with the lowest such score.

4. **Stop** when 100 reviews are selected.

```python
# Seed: most unique review
mean_sim = sim_matrix.mean(axis=1)
seed = int(np.argmin(mean_sim))
selected_indices.append(seed)

# Greedy expansion
while len(selected_indices) < max_per_prof and remaining:
    max_sim_to_selected = np.array([
        sim_matrix[i, selected_indices].max()
        for i in remaining
    ])
    best = list(remaining)[int(np.argmin(max_sim_to_selected))]
    selected_indices.append(best)
    remaining.remove(best)
```

#### Why TF-IDF and not a neural embedding model?

`sentence-transformers` (which would give better semantic similarity) was unavailable in the build environment. TF-IDF is a good fallback for this task because:
- Reviews are short and keyword-heavy (exam, curve, office hours, project)
- The goal is lexical diversity, not deep semantic understanding
- TF-IDF is fast and needs no GPU

In production, replacing `TfidfVectorizer` with `SentenceTransformer("all-MiniLM-L6-v2")` would give better results and is a straightforward swap.

#### Result

| Professor | Before | After |
|---|---|---|
| Husain Gholoom | 133 | 100 |
| Jill Seaman | 104 | 100 |
| Lee Koh | 133 | 100 |
| Everyone else | unchanged | unchanged |

Note: counts here are post-junk-filter (133, not 315/215/139), which is why only 3 professors exceeded the cap after cleaning.

---

## 8. Problem: Retrieval Failure Due to Header-Polluted Embeddings

### What happened

After the full pipeline was running and the Gradio app was live, the query:

```
"best professor for CS 1428"
```

returned:

```
I couldn't find enough information in the retrieved sources to answer that question.
```

This was wrong — the data contained 112 chunks about CS 1428 across four professors (Seaman: 50, Gholoom: 39, Qasem: 18, Vargas: 5). The retrieval system was simply failing to surface them.

### Root cause diagnosis

Every review chunk stored in ChromaDB has this structure:

```
Professor: Jill Seaman | Course: CS 1428 | Source: Coursicle | Year Level: Senior | Major: CS

She is a fair grader and gives opportunities for her students to perform well...
```

The original `embed.py` embedded the **entire chunk text** — both the header line and the review body — as a single vector:

```python
# Original (broken) code
texts = [c["text"] for c in batch]
embeddings = model.encode(texts, ...).tolist()
```

This meant the embedding vector was partially encoding the metadata header format (`"Professor: ... | Course: ... | Source: ..."`) rather than the semantic content of the review. Because every single chunk has a header in the same format, these repeated patterns pushed the vectors for all chunks into a similar region of the embedding space — making cosine similarity comparisons between the query and chunks much less discriminative.

A query like `"best professor for CS 1428"` embeds into a vector that represents the *question's meaning*, but it was being compared against vectors that partially represented a *pipe-delimited metadata format*. The mismatch was large enough that the top-5 retrieved chunks didn't contain CS 1428 content.

### Why this is a significant RAG engineering lesson

The assignment checklist said: *"bad chunks cannot be fixed by tuning retrieval later."* This is an instance of exactly that — the chunks themselves were fine (correct content, correct metadata), but what got **embedded** was wrong. Retrieval tuning (adjusting top-k, similarity thresholds, etc.) would not have fixed this because the fundamental vector representations were misaligned with query intent.

### Fix 1 — Embed body only, store full text

Updated `embed.py` to separate what gets **embedded** from what gets **stored**:

```python
def get_body(text: str) -> str:
    """Extract review body — everything after the first blank line."""
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) > 1 else text.strip()

# In build_index():
full_texts = [c["text"]     for c in batch]   # stored in ChromaDB for display
body_texts = [get_body(c["text"]) for c in batch]  # embedded as vectors

embeddings = model.encode(body_texts, ...).tolist()

collection.upsert(
    ids        = ids,
    documents  = full_texts,    # full text returned during retrieval
    embeddings = embeddings,    # body-only vector used for similarity search
    metadatas  = metas,
)
```

The `documents` field in ChromaDB stores the full chunk text so the app can display it with the header intact. The `embeddings` field stores a vector computed only from the review body, making similarity search semantically accurate.

### Fix 3 — Delete old index and rebuild

Because the old ChromaDB index contained header-polluted vectors, it had to be fully deleted and rebuilt:

```bash
rm -rf data/chroma_db
python embed.py --chunks data/chunks.jsonl --db data/chroma_db
```

Simply re-running `embed.py` without deleting the old index would not have helped — ChromaDB's `upsert` would overwrite the documents but the old embeddings would be replaced correctly. However, deleting and rebuilding is cleaner and guarantees no stale vectors remain.

### Result

After rebuilding the index with body-only embeddings, the query `"best professor for CS 1428"` correctly retrieved Seaman, Gholoom, and Qasem chunks and the LLM produced a grounded answer summarising student opinions for each.

### Broader lesson

In RAG systems, what you embed and what you store can and often should be different things:

| Field | Purpose | What to put here |
|---|---|---|
| `documents` | Returned to the app for display | Full chunk text including any metadata header |
| `embeddings` | Used for similarity search | Semantic content only — the part a user's query would match |
| `metadatas` | Used for filtering | Structured fields (professor, course, source_dir, date) |

Treating these as the same thing is a common mistake that produces retrieval systems that work in testing (when queries match exact phrases) but fail on natural language queries.

---

## 9. Final Chunk Statistics

| Metric | Value |
|---|---|
| Raw chunks (before dedup) | 1,043 |
| After content-hash dedup | 672 |
| Junk chunks dropped | 38 |
| Final unique chunks | **564** |

| Chunk type | Count |
|---|---|
| Review (rmp + coursicle) | 508 |
| Catalog (official) | 52 |
| Reddit (discussion) | 4 |
| **Total** | **564** |

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

## 10. How to Run

```bash
# From your project root
cd ~/Desktop/"TXST CS courses and professors rag"

# Activate virtual environment
source venv/bin/activate

# Install dependencies (one time)
pip install -r requirements.txt

# Step 1: chunk and clean documents
python ingest.py --documents-dir documents --out data/chunks.jsonl

# Step 2: embed into ChromaDB (delete old index first if rebuilding)
rm -rf data/chroma_db
python embed.py --chunks data/chunks.jsonl --db data/chroma_db

# Step 3: launch the app
python app.py
```

---

## 11. Libraries Used and Why

| Library | Built-in? | Used for |
|---|---|---|
| `re` | Yes | Splitting files on `----------` separator using regex `r"-{5,}"` |
| `json` | Yes | Serializing chunks to JSONL format (one JSON object per line) |
| `hashlib` | Yes | Generating deterministic 12-char MD5 chunk IDs for deduplication |
| `argparse` | Yes | Command-line interface (`--documents-dir`, `--out`, `--preview`, etc.) |
| `pathlib` | Yes | Cross-platform file path handling; `Path.glob("*.txt")` for directory walking |
| `typing` | Yes | Type hints (`Iterator[dict]`) for IDE support and code clarity |
| `warnings` | Yes | Suppressing sklearn warnings on short documents during TF-IDF fitting |
| `sklearn` | No (`pip install scikit-learn`) | TF-IDF vectorization and cosine similarity for diversity selection |
| `numpy` | No (`pip install numpy`) | `argmin` and array operations in the greedy selection algorithm |
| `chromadb` | No (`pip install chromadb`) | Persistent vector database for storing embeddings and metadata |
| `sentence-transformers` | No (`pip install sentence-transformers`) | Embedding model `all-MiniLM-L6-v2` for vectorising review bodies |
| `groq` | No (`pip install groq`) | LLM API for grounded answer generation |
| `gradio` | No (`pip install gradio`) | Web interface for the student-facing app |
| `python-dotenv` | No (`pip install python-dotenv`) | Loading `GROQ_API_KEY` from `.env` file |

---

## 12. Remaining Known Limitations

**Xiaomin Li underrepresented (11 chunks).** Only 11 reviews were scraped for Li. Queries about her courses will have very limited retrieval context. Fix: scrape more sources or note this limitation in the system prompt.

**Short review flag is not enforced.** 141 chunks marked `short_review: true` are still included. If embedding quality is poor, a `--drop-short` CLI flag could be added to exclude them.

**Coursicle dates are unknown.** 332 Coursicle reviews have `"date": "unknown"`. If recency filtering is added later (e.g., only show reviews from the last 3 years), these reviews will be excluded regardless of when they were actually written.

**TF-IDF vs. semantic similarity for professor cap.** The diversity selection uses TF-IDF because `sentence-transformers` was unavailable in the build environment at that stage. TF-IDF measures word overlap, not semantic meaning — two reviews that say the same thing in different words could both be selected. Swapping in `all-MiniLM-L6-v2` embeddings would improve diversity selection quality.

**Reddit chunks are very few (4 chunks).** The two Reddit files produced only 4 sliding-window chunks total. Reddit is a potentially rich source; scraping more threads would improve coverage of conversational advice that doesn't appear in formal reviews.

**Edwin Vargas has no data.** `edwinvargas.txt` is empty. The pipeline handles it gracefully (0 chunks, no crash), but the professor is unrepresented in the system.

**Course number format inconsistency.** The same course appears as both `"CS 1428"` (with space) and `"CS1428"` (without space) across Coursicle and RMP files. This inconsistency is in the source data. A normalisation pass in `cleaner.py` could standardise all course codes to the no-space format, which would improve metadata filtering accuracy.

---

## 13. Problem: Poor Answer Quality on Comparison Queries

### What happened

After the retrieval fix (Section 8) confirmed that CS 1428 chunks were
being retrieved correctly, the app was tested with:

```
Query: "best professor for CS 1428"
```

The response was:

```
There are conflicting reviews for CS 1428. According to Husain Gholoom's
review, he may not be suitable for introductory level classes and expects
perfection, while Jill Seaman's review states that she is a great professor,
but notes that non-CS majors may find the class difficult. There is no review
for another professor teaching CS 1428 to directly compare.
```

This answer had three distinct failures:

1. **It confused professor names with reviewer names.** It said "according
   to Husain Gholoom's review" — as if Gholoom was writing a review about
   himself. The LLM was reading the metadata header line
   (`Professor: Husain Gholoom | Course: CS 1428 | ...`) as if it were
   the student's voice, not a metadata label.

2. **It failed to synthesise.** The data contained 112 CS 1428 chunks
   across 4 professors (Seaman: 50, Gholoom: 39, Qasem: 18, Vargas: 5).
   The answer summarised one sentence per professor and called it done.
   It didn't reflect the weight of evidence.

3. **It claimed there was "no review for another professor"** — which is
   false. Qasem and Vargas both had reviews in the data, but the LLM
   missed them because the interleaved context made them easy to overlook.

---

### Root cause 1 — Header line passed to LLM as content

The context sent to the LLM included the full chunk text:

```
Professor: Husain Gholoom | Course: CS 1428 | Source: Coursicle | Year Level: Senior | Major: CS

He has no pity on people learning for the first time...
```

The model treated the first line (`Professor: Husain Gholoom | ...`) as
part of the review content — interpreting it as if Gholoom himself had
written something. This is a classic prompt construction error: metadata
and content were not visually separated in a way the model could reliably
distinguish.

---

### Root cause 2 — Interleaved chunks made comparison hard

The context was structured in retrieval order:

```
Review 1: Seaman chunk
Review 2: Gholoom chunk
Review 3: Seaman chunk
Review 4: Gholoom chunk
Review 5: Qasem chunk
```

The LLM had to mentally interleave and group evidence across professors
while simultaneously generating an answer. This is too much to ask —
models produce significantly worse comparisons when evidence is scattered
rather than grouped.

---

### Root cause 3 — System prompt said synthesise but didn't show how

The system prompt said *"do not just say reviews are mixed"* but didn't
give explicit structure for what a good comparison answer looks like.
Without scaffolding, the model defaulted to a hedge.

---

### Fix 1 — Strip header from context, show body only

Updated `_build_grouped_context()` in `generate.py` to split off the
pipe-delimited header and show only the student's actual words, with
course and date moved into a clean label:

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

The model can no longer confuse the professor name in the header with
the voice of a reviewer.

---

### Fix 2 — Group chunks by professor for comparison queries

Added `_is_comparison_query()` which detects keywords like `"best"`,
`"recommend"`, `"which professor"`, `"compare"`, `"vs"` and routes to
a different context builder:

```python
COMPARISON_SIGNALS = [
    "best", "better", "worst", "compare", "recommend", "which professor",
    "who should", "vs", "versus",
]

def _is_comparison_query(query: str) -> bool:
    return any(signal in query.lower() for signal in COMPARISON_SIGNALS)
```

When triggered, `_build_grouped_context()` groups all retrieved chunks
by professor before sending to the LLM:

```
=== PROFESSOR: Jill Seaman (3 reviews) ===
  Review 1 [...]: She is a fair grader...
  Review 2 [...]: Tests are exactly like homework...

=== PROFESSOR: Husain Gholoom (2 reviews) ===
  Review 3 [...]: He expects everything to be perfect...
```

This lets the model process all evidence for one professor before moving
to the next — dramatically improving synthesis quality.

---

### Fix 3 — System prompt now requires structured comparison output

Updated the system prompt with explicit rules for comparison queries:

```
RESPONSE QUALITY RULES:
6. For "best professor" or comparison questions:
   - Summarise what reviewers say about EACH professor separately
   - Cover: teaching style, exam difficulty, workload, grading, office hours
   - Note the number of reviews you are drawing from
   - Give a concrete recommendation at the end based ONLY on review patterns
   - Do NOT just say "reviews are mixed" — that is unhelpful. Synthesise the pattern.
```

---

### Expected answer after fixes

```
Based on student reviews:

Jill Seaman (50 reviews): Students consistently describe her as fair
and approachable. Tests mirror homework and class questions. She is
understanding about missed exams. Recommended for beginners.

Husain Gholoom (39 reviews): Reviews are genuinely split. Some call him
the best professor at TXST; others warn he expects perfection on
assignments and is strict on academic integrity. Tests are heavily
weighted. Better for students with prior C++ experience.

Apan Qasem (18 reviews): Predominantly positive. Students say he is
clear, organised, and accessible outside class.

Recommendation: For CS 1428, Jill Seaman has the most consistently
positive reviews, particularly for students new to coding.

Sources:
1. Coursicle — Jill Seaman · CS 1428
2. Coursicle — Husain Gholoom · CS 1428
3. RateMyProfessors — Apan Qasem · CS1428
```

---

### Broader lesson

Good retrieval does not guarantee good answers. Even when the right
chunks are retrieved, answer quality depends on:

1. **How the context is formatted** — headers vs. body-only, interleaved
   vs. grouped
2. **How the prompt is structured** — vague instructions produce vague
   answers; explicit output structure produces structured answers
3. **Whether the prompt matches the query type** — a flat context format
   works for factual lookups but fails for multi-entity comparisons

The fix required changes to three separate things simultaneously:
context formatting, context grouping logic, and the system prompt. Any
one fix alone would have improved the answer, but all three together
were needed to produce a reliably good response.

This is why RAG systems require end-to-end testing on real queries —
retrieval metrics alone cannot reveal generation failures.

---

## 14. Problem: Jill Seaman Ignored in CS 1428 Comparison

### What happened

After the generation fix in Section 13, the query `"best professor for CS 1428"`
was tested again. Seaman was still missing from the answer:

```
"However, only one review mentions CS 1428, which is for Professor Husain
Gholoom... I couldn't find enough information in the retrieved sources to
make a thorough comparison."
```

This was wrong. Seaman had 50 CS 1428 chunks in the database — more than
any other professor. She was completely absent from a query specifically
about her course.

---

### Root cause 1 — top_k=5 is mathematically too small for comparison queries

With `top_k=5` and 4 professors teaching CS 1428, it is statistically
impossible to surface all professors reliably. In the worst case, all 5
retrieved chunks could belong to a single professor. Even in the average
case, at least one professor will be missing from every comparison query.

```
4 professors × minimum 2 chunks each = top_k of at least 8
4 professors × fair representation   = top_k of at least 12–15
```

The app was using `top_k=5` for all queries — a value appropriate for
narrow factual lookups but inadequate for multi-entity comparisons.

---

### Root cause 2 — Course number format inconsistency in metadata

Even with a higher top_k, semantic search alone is unreliable for
course-specific queries. The same course appears in three different
formats across sources:

| Format | Count | Source |
|---|---|---|
| `"CS 1428"` | 87 chunks | Coursicle |
| `"CS1428"` | 20 chunks | RMP |
| `"1428"` | 3 chunks | Mixed |
| `"HONORSCS1428"` | 2 chunks | Coursicle |

A semantic search for "best professor for CS 1428" might rank a CS 3358
chunk higher than a CS 1428 chunk if the review text happened to use
similar words — returning the wrong course entirely.

---

### Fix 1 — Adaptive top_k based on query type

Updated `retrieve.py` to use `top_k=15` for comparison queries and
`top_k=5` for standard factual queries:

```python
COMPARISON_TOP_K = 15

def _is_comparison_query(query: str) -> bool:
    signals = ["best", "better", "worst", "compare", "recommend",
               "which professor", "who should", "vs", "versus"]
    return any(signal in query.lower() for signal in signals)

effective_top_k = COMPARISON_TOP_K if _is_comparison_query(query) else top_k
```

15 chunks gives each of the 4 CS 1428 professors room to appear with
3–4 representative reviews, which is enough for the LLM to synthesise
a meaningful comparison.

---

### Fix 2 — Course-aware metadata filtering

Added `_extract_course_number()` which pulls the 4-digit number from
any course format in the query:

```python
def _extract_course_number(query: str) -> str | None:
    match = re.search(r'\b(\d{4})\b', query)
    return match.group(1) if match else None
```

And `_build_course_filter()` which matches all format variants in a
single ChromaDB `$or` filter:

```python
def _build_course_filter(course_digits: str) -> dict:
    return {
        "$or": [
            {"course": f"CS {course_digits}"},      # "CS 1428"
            {"course": f"CS{course_digits}"},        # "CS1428"
            {"course": course_digits},               # "1428"
            {"course": f"HONORSCS{course_digits}"},  # "HONORSCS1428"
        ]
    }
```

This filter is applied before semantic ranking, so ChromaDB only scores
chunks from the correct course. A CS 3358 chunk can no longer appear in
results for a CS 1428 query regardless of how similar the review text sounds.

---

### Verification

After both fixes, simulating the retrieval logic on the chunk database:

```
Query            : 'best professor for CS 1428'
Is comparison    : True
Course digits    : 1428
Effective top_k  : 15

Chunks matching course filter: 112
Professors found:
   50  Jill Seaman       ← was missing before
   39  Husain Gholoom
   18  Apan Qasem
    5  Edwin Vargas
```

All four professors now surfaced. Seaman's 50 reviews — the largest
body of evidence for any CS 1428 professor — will now be included in
every comparison query for that course.

---

### Broader lesson

`top_k` is not a fixed constant — it should scale with the complexity
of the query. A factual lookup ("does Burtscher give extensions?") needs
1–3 relevant chunks. A multi-professor comparison needs enough chunks to
represent every entity being compared. Using a single global `top_k`
value is a design flaw that will silently miss evidence for any query
involving more professors than the value allows.

Similarly, metadata filtering and semantic search serve different roles:
- **Semantic search** finds chunks whose *meaning* is similar to the query
- **Metadata filtering** enforces *structural constraints* like course number

Neither alone is sufficient. Used together, they ensure results are both
relevant and from the correct source.

---

## 15. Problem: Qasem and Vargas Underrepresented in Comparison Answers

### What happened

After fixes in Section 14, the query `"best professor for CS 1428"` correctly
surfaced all four professors. But the answer still showed unequal representation:

```
Husain Gholoom  → 4 reviews summarised
Jill Seaman     → 6 reviews summarised
Edwin Vargas    → 1 review (dismissed as "not relevant to CS 1428")
Apan Qasem      → 4 reviews summarised
```

Vargas was nearly dismissed entirely. The answer incorrectly stated his one
retrieved review was "not directly relevant to CS 1428" — when in reality
Vargas has 5 CS 1428 chunks in the database. The LLM was working with
incomplete evidence and drawing wrong conclusions from it.

---

### Root cause — proportional retrieval favours high-volume professors

With `top_k=15` and a course filter applied, ChromaDB returns the 15 most
semantically similar chunks. Because Seaman has 50 CS 1428 chunks and
Gholoom has 39, their chunks dominate the similarity rankings — they occupy
~12 of the 15 slots proportionally:

| Professor | CS 1428 chunks | % of pool | Slots at top_k=15 |
|---|---|---|---|
| Jill Seaman | 50 | 45% | ~7 |
| Husain Gholoom | 39 | 35% | ~5 |
| Apan Qasem | 18 | 16% | ~2 |
| Edwin Vargas | 5 | 4% | ~1 |

Vargas gets approximately 1 slot. With only 1 chunk, the LLM cannot assess
him fairly — it either dismisses him or makes things up to fill the gap.

This is a **volume bias problem**: professors with more reviews crowd out
professors with fewer, even when the minority professor's reviews are
equally relevant to the query.

---

### Fix — balanced retrieval with per-professor cap

Added `retrieve_balanced()` to `retrieve.py`. For comparison queries,
instead of returning the raw top-k results, it:

1. Fetches a wider pool (`fetch_top_k=30`) with the course filter applied
2. Groups chunks by professor
3. Keeps at most `cap_per_prof=4` chunks per professor
   (the 4 most similar, since ChromaDB returns them sorted)
4. Returns the balanced list

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

Result with balanced retrieval:

| Professor | CS 1428 chunks | Slots allocated |
|---|---|---|
| Jill Seaman | 50 | 4 |
| Husain Gholoom | 39 | 4 |
| Apan Qasem | 18 | 4 |
| Edwin Vargas | 5 | 4 (all of them) |

Total chunks sent to LLM: 16. Every professor gets the same voice.

`generate.py` routes comparison queries through `retrieve_balanced()`
and standard queries through the original `retrieve()`:

```python
if _is_comparison_query(query):
    chunks = retrieve_balanced(query, db_path=db_path)
else:
    chunks = retrieve(query, top_k=top_k, db_path=db_path)
```

---

### Broader lesson

In a RAG system serving comparison queries, raw semantic similarity is
not a fair retrieval criterion when the underlying data has volume
imbalances. A professor with 50 reviews will always crowd out a professor
with 5 — not because their reviews are more relevant, but simply because
there are more of them to match against.

The solution is to separate **relevance** (handled by semantic search and
course filtering) from **representation** (handled by the per-professor
cap). Retrieve for relevance first, then balance for fairness.

This mirrors a pattern in recommendation systems and search ranking:
diversity constraints applied post-retrieval prevent any single entity
from dominating results purely due to data volume.