## Domain

This RAG system covers student experiences with Computer Science professors and courses at Texas State University (TXST). To be precise, it answers questions about teaching style, exam difficulty, workload, grading policies, and which professor to choose for a given course.  
This is really valuable because official university resources like course catalogs, faculty pages, and registration systems only describe what a course covers, not what it is actually like to take it. For instance, I spend couple of hours everytime reviewing courses and professors during my registration. This RAG pipeline helps students like me by combining that scattered information and helps answer a direct question with get a grounded, source-cited answer in seconds.


## Document Sources

| # | Source | Type | URL or file path |
|---|--------|------|-----------------|
| 1 | Rate My Professors — Lee Koh |  Professors — Lee KohProfessor reviews |https://www.ratemyprofessors.com/professor/56546|
| 2 | Rate My Professors — Husain Gholoom | Professor reviews | https://www.ratemyprofessors.com/professor/1755852 |
| 3 | Rate My Professors — Martin Burtscher | Professor reviews | https://www.ratemyprofessors.com/professor/1508396 |
| 4 | Rate My Professors — Mina Guirguis | Professor reviews | https://www.ratemyprofessors.com/professor/1118377 |
| 5 | Rate My Professors — Oleg Komogortsev | Professor reviews | https://www.ratemyprofessors.com/professor/1132554 |
| 6 | Rate My Professors — Jill Seaman | Professor reviews | https://www.ratemyprofessors.com/professor/1828652 |
| 7 | Rate My Professors — Ted Lehr | Professor reviews | https://www.ratemyprofessors.com/professor/1909698 |
| 8 | Rate My Professors — Keshav Bhandari | Professor reviews | https://www.ratemyprofessors.com/professor/2676876 |
| 9 | Rate My Professors — Apan Qasem | Professor reviews | https://www.ratemyprofessors.com/professor/1071081 |
| 10 | Rate My Professors — Edwin Vargas | Professor reviews | https://www.ratemyprofessors.com/professor/2494994 |
| 11 | Rate My Professors — Xiaomin Li | Professor reviews | https://www.ratemyprofessors.com/professor/2831104 |
| 12 | Coursicle — Husain Gholoom | Professor reviews | https://www.ratemyprofessors.com/professor/2494994 |
| 13 | Coursicle — Jill Seaman | Professor reviews |https://www.coursicle.com/txstate/professors/Jill+Seaman/ |
| 14 | TXST Official Course Catalog | Course descriptions | https://mycatalog.txstate.edu/courses/cs/ |
| 15 | Reddit r/txstate — CS3358 & CS2318 advice | Forum thread |https://www.reddit.com/r/txstate/comments/1c0xw5d/ |
| 16 | Reddit r/txstate — General CS course data | Forum thread | documents/reddit/coursedata.txt |

---

## Chunking Strategy

1. Review-level chunking

**Chunk size:**
50–300 words per chunk (typically one review per chunk)
**Overlap:**
0 words
**Reasoning:**
The majority of the corpus consists of student reviews from Rate My Professors, Reddit discussions, and other review platforms. Since each review typically expresses a complete opinion about a professor, course, grading style, exams, projects, or office hours, splitting reviews further would risk losing important context. Therefore, each individual review is stored as a separate chunk wherever possible.

For Reddit threads, comments are grouped into chunks of approximately 300–500 words with a 50-word overlap to preserve discussion context between replies. Official sources such as the Texas State course catalog are chunked by logical sections — one course description per chunk — because these documents are already concise.

This strategy improves retrieval precision because semantic search can directly retrieve the specific student experiences most relevant to a user's question. For example, a query such as "Does Gholoom curve exams?" can retrieve individual reviews mentioning curves rather than an entire professor document containing hundreds of unrelated reviews. The smaller chunk size also improves embedding quality and reduces irrelevant information returned during retrieval.

Before chunking, each source file was preprocessed by chunker.py: pipe-delimited metadata headers (Professor: X | Course: Y | Source: Z) were parsed into structured metadata fields rather than stored in the chunk text. A junk filter removed scraper artifacts such as tech news headlines that ended up in some source files, and a separator-stripping step removed trailing ---------- lines that the block splitter was accidentally including in chunk bodies.


**Final chunk count:**
Final chunk count: 665 chunks total (478 embedded after excluding short reviews under 50 words from the vector index). Breakdown: 599 review chunks, 52 catalog chunks, 18 reddit chunks — across 11 professors and 52 courses.

---

## Sample Chunks

The following five labeled chunks are taken directly from `data/chunks.jsonl` after the full ingestion and cleaning pipeline ran.

---

**Chunk 1** — Source file: `rmp/lee_koh_cs2318.txt`

> Dr. Koh expects his students to work really hard in his classes. I ended up having to retake assembly again with another professor, but Koh's class made the other class relatively easy. There's a bit of a language barrier, but if you really want to learn a lot about assembly, take Koh.

Metadata: `professor=Lee Koh | course=CS2318 | source=rmp | chunk_type=review`

---

**Chunk 2** — Source file: `rmp/lee_koh_cs2318.txt`

> You need to dedicate a lot of time to teaching yourself this course in Koh's class. His lectures are loosely structured and incredibly hard to follow. He has thick accent and fluctuates his volume. He will be explaining important information at a mumble. His notes are frustrating to look through b/c it's organized poorly w/ zip files. Good luck.

Metadata: `professor=Lee Koh | course=CS2318 | source=rmp | chunk_type=review`

---

**Chunk 3** — Source file: `rmp/martin_burtscher_cs4380.txt`

> Hard class. Take it. Extremely applicable information with a professor that breaks it down into somewhat easy pieces to understand. PROJECT HEAVY -- Expect 5-6 hours of purely projects a week. Quality, engaging lectures. Don't get it? Office hours are goated af. You will be overwhelmed, but you will learn a ton. 10/10.

Metadata: `professor=Martin Burtscher | course=CS4380 | source=rmp | chunk_type=review`

---

**Chunk 4** — Source file: `rmp/keshav_bhandari_cs2308.txt`

> You won't learn a thing in class because he only goes over the basics and dumps completely foreign content on the exam. He purposely makes confusing questions and only gives a vague idea of the topics covered. Exams are online and proctored and you can't go back to previous questions.

Metadata: `professor=Keshav Bhandari | course=CS2308 | source=rmp | chunk_type=review`

---

**Chunk 5** — Source file: `official/catalog.txt`

> CS1428 — Introductory course for computer science majors, minors and others desiring technical introduction to computer science. Contains overview of history and structure of the digital computer, including binary data representation. Problem solving, algorithm development, structured programming, good coding style, and control structures of C++ are emphasized.

Metadata: `course=CS1428 | source=official | chunk_type=catalog`

---

**Embedding model:**
all-MiniLM-L6-v2 from the Sentence Transformers library. This model is lightweight, efficient, and performs well on semantic search tasks involving student reviews, Reddit discussions, and course information.

**Top-k:**
5 chunks per query. Retrieving the five most relevant chunks provides sufficient context for answer generation while minimizing irrelevant information.

**Production tradeoff reflection:**
If cost and computational resources were not a constraint, I would consider larger embedding models such as BGE-Large or OpenAI embedding models. Larger models generally provide higher retrieval accuracy and better semantic understanding of nuanced questions about professors, exams, grading policies, and teaching styles. I would also consider multilingual embedding models if the system needed to support reviews or queries in multiple languages. However, these benefits come with increased latency, storage requirements, and computational costs. Given the relatively small size of the Texas State Computer Science professor corpus, all-MiniLM-L6-v2 offers a strong balance between retrieval quality, speed, and ease of deployment.

---

## Retrieval Test Examples

The following tests were run via `python test_retreival.py --db data/chroma_db`. All queries used top-k=8. Comparison queries used `retrieve_balanced()`.

---

### Retrieval Example 1

**Query:** *What do students say about Lee Koh's CS2318 Assembly course?*

**Top returned chunks (showing top 3):**

| Rank | Score | Professor | Course | Source | Preview |
|------|-------|-----------|--------|--------|---------|
| 1 | 0.343 | Lee Koh | CS2318 | rmp | "Dr. Koh expects his students to work really hard in his classes…" |
| 2 | 0.384 | Lee Koh | CS2318 | rmp | "Dr. Koh is knowledgeable in assembly language, but his lectures lack engagement and structure…" |
| 3 | 0.385 | Lee Koh | CS2318 | rmp | "Koh is knowledgable and puts a lot of time into his job. He can be a little tough to follow at times but will always be helpful if you ask…" |

**Why these chunks are relevant:** The query asks specifically about Lee Koh's CS2318 course. The professor metadata filter in `retrieve.py` pins results to Lee Koh chunks only. All three top chunks directly answer the question — Chunk 1 confirms the heavy workload, Chunk 2 addresses lecture clarity, and Chunk 3 gives a balanced view of the tradeoff between difficulty and helpfulness. The semantic similarity is high because the review language (assembly, hard, lectures) closely matches the query terms.

---

### Retrieval Example 2

**Query:** *How do students describe Martin Burtscher's workload in CS4380?*

**Top returned chunks (showing top 3):**

| Rank | Score | Professor | Course | Source | Preview |
|------|-------|-----------|--------|--------|---------|
| 1 | 0.458 | Martin Burtscher | CS4380 | rmp | "Burtscher spends his time providing obstacles for students instead of resources…" |
| 2 | 0.535 | Martin Burtscher | CS4380 | rmp | "Easily one of the best CS professors. This is probably one of the more difficult classes to take in the department…" |
| 3 | 0.580 | Martin Burtscher | CS4380 | rmp | "Hard class. Take it… PROJECT HEAVY -- Expect 5-6 hours of purely projects a week…" |

**Why these chunks are relevant:** The query targets both the professor (Burtscher) and the course (CS4380). The metadata professor filter ensures only Burtscher reviews are returned. The embedding similarity is driven by the overlap between the query's workload framing and review tokens like "projects", "hours", "assignments", and "deadlines." The spread of scores (0.458–0.665) reflects that some reviews discuss workload directly while others focus on broader aspects of the class.

---

### Retrieval Example 3

**Query:** *How do Jill Seaman and Husain Gholoom compare for CS1428?*

**Top returned chunks (showing top 4 — comparison query uses `retrieve_balanced()`):**

| Rank | Score | Professor | Course | Source | Preview |
|------|-------|-----------|--------|--------|---------|
| 1 | 0.539 | Jill Seaman | CS1428 | rmp | "Seaman was great… Class is easy if you do the readings & pay attention…" |
| 2 | 0.779 | Husain Gholoom | CS1428 | rmp | "The fact that this professor is allowed near computers at all…" |
| 3 | 0.802 | Husain Gholoom | CS1428 | rmp | "I don't know how I did it, but somehow I managed to scrape by with an A…" |
| 4 | 0.861 | Husain Gholoom | CS1428 | rmp | "A lot of negative feedback but here is something I want share. Mr. Gholoom is the one I like so far…" |

This query used `retrieve_balanced()` which fetches a pool of 30, caps at 4 per professor, and pins named professors even when their chunks score above the default distance cutoff (Fix G in `retrieve.py`). A catalog chunk for CS1428 was also pinned.

---

## Grounded Generation


**System prompt grounding instruction:**
 The LLM receives the following system prompt:

"You are TXST Professor Advisor, a helpful assistant for Texas State University CS students choosing courses and professors. Answer ONLY using information from the CONTEXT provided. Never use prior knowledge. If context lacks enough information, say exactly: 'I couldn't find enough information in the retrieved sources to answer that question.' Never invent, guess, or assume professor traits, grading policies, or difficulty. If reviews conflict (one says easy, another hard), acknowledge both views."

This is enforced structurally as well: the user message prefixes the retrieved chunks with CONTEXT: and explicitly instructs the model to answer using only that context. The model never sees the source list — that is built programmatically from chunk metadata before the API call, so the LLM cannot fabricate or reorder citations.

**How source attribution is surfaced in the response:**
Source attribution is entirely programmatic. The format_sources() function in generate.py reads professor name, course, source platform, and date from each chunk's metadata dictionary and formats them into a numbered list before the LLM is called. This guarantees that sources are always shown and always accurate regardless of what the model says. The Gradio UI displays the answer and source list in two separate output panels.

---

### Response Example 1

**Query:** *What do students say about Martin Burtscher's workload in CS4380?*

**System Answer:**

> Based on student reviews, Martin Burtscher's CS4380 (Parallel Programming) is widely described as a challenging, project-heavy course. Reviewers consistently note that students should expect roughly 5–6 hours of project work per week, with strict deadlines set from the first day of class. One reviewer states: projects and homework aren't trivial — you need to start assignments immediately when they're released.
>
> Despite the heavy workload, the majority of reviews say the course is worth it. Multiple reviewers describe the material as directly applicable to real jobs and recommend Burtscher even for students who find the class overwhelming. A minority of reviews are negative, with one reviewer accusing Burtscher of editing open-note homework after it's released to catch students out.
>
> Office hours are consistently praised as an effective resource. Overall, student consensus is that CS4380 is one of the harder courses in the department, but Burtscher is one of the better instructors.

**Sources:**

> 1. RateMyProfessors — Martin Burtscher · CS4380
> 2. RateMyProfessors — Martin Burtscher · CS4380
> 3. RateMyProfessors — Martin Burtscher · CS4380
> 4. RateMyProfessors — Martin Burtscher · CS4380
> 5. RateMyProfessors — Martin Burtscher · CS4380

---

### Response Example 2

**Query:** *How do Jill Seaman and Husain Gholoom compare for CS1428?*

**System Answer:**

> **Jill Seaman (CS1428)**
> Student reviews for Seaman are strongly positive. Reviewers describe her as fair, approachable, and well-suited for students new to programming. Class expectations are clear — readings and exercises are assigned regularly, but students who keep up report that the workload is manageable. One reviewer notes occasional grading errors by TAs, but says they never significantly impacted their grade. Multiple reviewers recommend her for beginners.
>
> **Husain Gholoom (CS1428)**
> Reviews for Gholoom are predominantly negative. The majority of reviewers warn beginners to avoid him, citing poor communication, unanswered emails, and exams that go beyond what was taught. One outlier review says the course was straightforward and that graders were responsive, suggesting some variation in student experience.
>
> **Recommendation:** Based on the review patterns, Seaman is the clear choice for CS1428, especially for students new to CS. Gholoom may be workable if you are self-sufficient and have prior programming experience, but the overwhelming majority of reviewers advise against him for this course.

**Sources:**

> 1. RateMyProfessors — Jill Seaman · CS1428
> 2. RateMyProfessors — Husain Gholoom · CS1428
> 3. RateMyProfessors — Husain Gholoom · CS1428
> 4. RateMyProfessors — Husain Gholoom · CS1428
> 5. TXST Course Catalog — CS1428

---

### Out-of-Scope Query — Refusal Example

**Query:** *What is the best GPU for machine learning in 2024?*

**System Answer:**

> I couldn't find enough information in the retrieved sources to answer that question.

**Sources:**

> 1. RateMyProfessors — Apan Qasem · CS3339
> 2. RateMyProfessors — Martin Burtscher · CS4380
> 3. RateMyProfessors — Lee Koh · CS2318
> 4. RateMyProfessors — Keshav Bhandari · CS2308
> 5. RateMyProfessors — Jill Seaman · CS1428

The system returned the standard refusal because the question is entirely outside the domain. The retrieved chunks are about TXST professors — not hardware — because the semantic search found no meaningful match. The system prompt explicitly prohibits the LLM from using prior knowledge, so it correctly refused rather than answering from general training data.

---

## Query Interface

The Bobcat Advisor uses a Gradio `Blocks` interface defined in `app.py`.

**Input fields:**
- **Your question** (Textbox, 3 lines) — the student's natural-language question about a professor, course, workload, exams, or grading.
- **Filter by source** (Dropdown) — optionally restrict retrieval to a single source platform: All sources / RateMyProfessors / Coursicle / Reddit r/txstate / Official catalog. Default is "All sources".

**Output fields:**
- **Answer** (Textbox, read-only, 10 lines) — the grounded LLM response, generated using only the retrieved context.
- **Sources** (Textbox, read-only, 10 lines) — a numbered list of sources extracted programmatically from chunk metadata before the LLM is called. Format: `Platform — Professor · Course, Date (if available)`.

The interface also includes a row of clickable example queries. Submitting a question works via the Ask button or by pressing Enter in the question field.

### Sample Interaction Transcript

**User input (question field):** Is Lee Koh's Assembly course worth taking?

**User input (source filter):** All sources

**[User clicks "Ask Bobcat →"]**

**Answer panel:**

> Based on student reviews, Lee Koh's CS2318 Assembly course is worth taking if you are prepared to do a significant amount of self-directed study. Reviews consistently describe Koh as knowledgeable and genuinely passionate about assembly language, and several reviewers credit the course with giving them a strong foundation that made later CS courses easier.
>
> The main caveat is that lectures are difficult to follow for many students — reviewers cite a language barrier, loosely structured notes, and uneven pacing. Students who rely primarily on lecture to learn the material tend to struggle. Students who use the provided lecture notes, past test questions, office hours, and recorded lectures report doing well.
>
> Exams are described as fair and covering material from lectures exclusively. If you are willing to put in time outside of class and engage with the provided resources, the course is considered worthwhile by the majority of reviewers.

**Sources panel:**

> 1. RateMyProfessors — Lee Koh · CS2318
> 2. RateMyProfessors — Lee Koh · CS2318
> 3. RateMyProfessors — Lee Koh · CS2318
> 4. RateMyProfessors — Lee Koh · CS2318
> 5. RateMyProfessors — Lee Koh · CS2318

---

## Evaluation Report

<!-- Run your 5 test questions from planning.md through your system and record the results.
     Be honest — a partially accurate or inaccurate result that you explain well is more
     valuable than a suspiciously perfect result. -->

| # | Question | Expected answer | System response (verbatim) | Retrieval quality | Response accuracy |
|---|----------|-----------------|----------------------------|-------------------|-------------------|
| 1 | What do students say about surviving Lee Koh's CS2318 Assembly course? | Course is demanding, requires significant self-study; lectures hard to follow; use provided resources like notes and recordings | To survive Lee Koh's CS2318 Assembly course, students suggest that you need to be prepared to work really hard and dedicate a lot of time to teaching yourself the material. As one reviewer noted, "Koh expects his students to work really hard in his classes" and another stated, "You need to dedicate a lot of time to teaching yourself this course in Koh's class." Overall, surviving Lee Koh's CS2318 Assembly course requires a significant amount of effort, dedication, and self-motivation. As one reviewer put it, "If you put in the work to truly understand the material, you will be fine." | Relevant | Accurate |
| 2 | What do students say about Keshav Bhandari's exams in CS2308? | Exams significantly harder than lectures; questions beyond what was taught; students felt underprepared | Based on the reviews provided, students have mixed opinions about Keshav Bhandari's exams in CS2308.One review states that his exams are "straight-forward and tests your understanding of lecture material" (Review 1), suggesting that the exams are fair and aligned with the course content. However, other reviews contradict this, stating that the exams are confusing, and the questions are beyond what was taught in class. Review 2 mentions that "he purposely makes confusing questions and only gives a vague idea of the topics covered." | Relevant | Accurate |
| 3 | How do students describe Martin Burtscher's workload in CS4380? | Project-heavy, ~5–6 hours per week, strict deadlines. most say the class is worth it despite the difficulty | According to the reviews, students describe Martin Burtscher's workload in CS4380 as challenging. Review 2 mentions that the class is "one of the more difficult classes to take in the department," but attributes this difficulty to the material, not unnecessary work added by Burtscher. | Relevant | Accurate |
| 4 | How do Jill Seaman and Husain Gholoom compare for CS1428? | Seaman is positive and beginner-friendly; Gholoom reviews are mostly negative and warn beginners away | Let's compare Jill Seaman and Husain Gholoom for CS1428 based on the reviews. Jill Seaman** has 1 review, which is overwhelmingly positive. The reviewer says she is "great" and that the class is "easy if you do the readings & pay attention.*Husain Gholoom**, on the other hand, has 4 reviews, which are mostly negative. Reviewers describe him as a poor teacher, with one saying he makes the rest of the computer science professors "look like shining examples of what a teacher should be." | Relevant | Accurate |
| 5 | How does student feedback about a course differ from the official Texas State course description? | Should compare catalog course topics with student-reported workload, exam style, grading, and professor expectations | "I couldn't find enough information in the retrieved sources to answer that question." | Off-target | Inaccurate |

**Retrieval quality:** Relevant / Partially relevant / Off-target  
**Response accuracy:** Accurate / Partially accurate / Inaccurate

---

## Failure Case Analysis

<!-- Identify at least one question where retrieval or generation did not work as expected.
     Write a specific explanation of *why* it failed, tied to a part of the pipeline.

     "The answer was wrong" is not an explanation.

     "The relevant information was split across a chunk boundary, so retrieval returned
     only half the context — the model didn't have enough to answer correctly" is an explanation.

     "The embedding model treated the professor's nickname as out-of-vocabulary and returned
     results from an unrelated review" is an explanation. -->

**Question that failed:**
How does student feedback about a course differ from the official Texas State course description?
**What the system returned:**
"I couldn't find enough information in the retrieved sources to answer that question." The retrieved chunks were reviews for Apan Qasem CS5331, Jill Seaman CS2308, Husain Gholoom CS3358, Lee Koh CS2318, and Edwin Vargas CS2315 — five different professors and courses with no shared thread, and no catalog chunk.
**Root cause (tied to a specific pipeline stage):**
This is a retrieval failure caused by two compounding problems. First, the query contains no professor name and no course number, so the metadata filters in retrieve.py that normally narrow results to a specific professor or course produce no filter at all. Thus, the query goes out as a pure semantic search across all 478 chunks. Second, the query phrase "student feedback differ from official course description" has weak semantic overlap with any individual review, because no single review talks about the contrast between student experience and official descriptions. The embedding for this query lands in a region of the vector space with no strong neighbors, so ChromaDB returns loosely related chunks from unrelated professors. The catalog chunk pinning logic in _get_catalog_chunk() only triggers for comparison queries that mention a specific course number — this query mentions neither.
**What you would change to fix it:**
Two fixes would address this. First, add "official description" and "course catalog" as triggers for a special retrieval path that fetches both the catalog chunk for a mentioned course and reviews for that course, even when no professor is named. Second, add this query pattern to the evaluation plan during development so the failure surfaces before submission rather than after. A query like "How does CS3358 student feedback differ from the official description?" would succeed because the course number triggers catalog pinning — the failure is specific to the abstract, professor-agnostic phrasing.
---

## Spec Reflection

<!-- Reflect on how planning.md shaped your implementation.
     Answer both questions with at least 2–3 sentences each. -->

**One way the spec helped you during implementation:**
Writing out the chunking strategy before coding actually saved me a lot of time. Because I had already decided on one review per chunk with zero overlap, I knew exactly what the I'd be doing: finding review boundaries and not split by character count. This meant when I realized RMP and Coursicle files had different header formats, I didn't have to rethink the whole approach, just make sure the parser could handle both.

**One way your implementation diverged from the spec, and why:**
The spec described a single retrieval function returning top-5 chunks for all query types. The actual implementation ended has three distinct retrieval modes: standard semantic search with professor and course metadata filters, retrieve_balanced() for comparison queries that caps results per professor and pins named professors regardless of score, and _get_catalog_chunk() that bypasses similarity entirely to guarantee catalog entries appear for course-specific questions. I designed and coded these due retrieval failures discovered while testing. For instance, the original single-function approach returned only Jill Seaman reviews for the Seaman vs. Gholoom comparison query because Seaman's RMP reviews scored better semantically. I tried prompt engineering but it didn't work, so I had to change the architecture.
---

## AI Usage

<!-- Describe at least 2 specific instances where you used an AI tool during this project.
     For each: what did you give the AI as input, what did it produce, and what did you
     change, override, or direct differently?

     "I used Claude to help me code" is not sufficient.
     "I gave Claude my Chunking Strategy section from planning.md and asked it to implement
     chunk_text(). It returned a function using a fixed character split. I overrode the
     chunk size from 500 to 200 because my documents are short reviews, not long guides." -->

**Instance 1**

- *What I gave the AI:* The Retrieval Approach and Pipeline Diagram sections from planning.md, plus the ChromaDB collection schema and a description of the three query types (factual, comparison, catalog-contrast).
- *What it produced:* A complete retrieve.py with a single retrieve() function using semantic search and a basic metadata filter for professor name extracted from the query.
- *What I changed or overrode:*   I caught a bug where almost every query was being treated as a comparison query and debugged it, so it won't be the case. Again, next problem arosed: when I asked about two professors, only one would show up in the results, so I told Claude to rework the retrieval to balance results across both. Even after that one professor kept getting dropped entirely, so I fixed that by always pulling professor reviews when you mention them by name.


**Instance 2**

- *What I gave the AI:*  The terminal output showing 592 chunks dropped by the junk filter with examples like "Comp. Architecture was enjoyable with Dr. Qasem!". This is not a junk, so I wanted to know why that was happening. 
- *What it produced:* It figured out that the dashes separating reviews in the source files were accidentally being included in the chunk text, which made the cleaner think the chunk body was just "----------" and drop it as too short.
- *What I changed or overrode:*
The AI suggested stripping separators in _split_review_blocks() itself. I overrode this and instead added the strip in _review_chunk() directly on the body after _parse_header(). This is safer because it targets the fix at the point where body text becomes chunk text, rather than modifying the block-splitting logic that other functions depend on.
