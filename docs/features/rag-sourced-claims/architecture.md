# Architecture — Dietary Guidance RAG Chatbot (Phase 2)

Technical architecture for the milestone described in [problemStatement.md](./problemStatement.md).
It extends [../../architecture.md](../../architecture.md) (Phase 1) rather than replacing it: every
Phase 1 component stays, and this document specifies only what is added or changed.

Where a decision is not forced by the brief, it is marked **[decision]** with its rationale, or
**[open]** where [problemStatement.md §11](./problemStatement.md) still owns it.

---

## 1. Guiding principle

Phase 1's principle was *a reliable contract over model cleverness*, enforced by code that runs
independently of the model. Phase 2 applies the same method to grounding:

> **Make the failure structurally impossible rather than prompting against it.**

Three places where that choice drives the design:

| Requirement | Prompt-based approach (rejected) | Structural approach (chosen) |
|---|---|---|
| Never blend two sources into one claim (§4.5) | Instruct the model not to blend | **One generation call per document.** The model never sees two documents at once, so it cannot merge them. §7.2 |
| Every claim carries a citation (§4.4) | Instruct the model to cite | **`source` is a required, non-nullable field** in the structured output, and the backend rejects any claim whose cited chunk was not in that call's context. §8.3 |
| Answers come from retrieved text only (§7) | Instruct the model not to use prior knowledge | **Citation validation.** A claim can only cite a chunk id that was passed in; an invented or unsupplied id is a hard validation failure, not a warning. §8.3 |

This mirrors `services/scope_guard.py`: the system prompt is reinforcement, the code is the guarantee.

---

## 2. Chosen stack

Phase 1's stack is unchanged (Next.js / FastAPI / Groq `openai/gpt-oss-120b` / Railway Postgres /
Vercel). New choices, all drawn from the brief's option list in
[problemStatement.md §5](./problemStatement.md):

| Concern | Choice | Rationale |
|---|---|---|
| **Vector store** | **pgvector in the existing Railway Postgres** **[decision]** | No new service, no new connection string, no new secret. Chunk text, chunk metadata and chunk vector live in one row, so a citation is a foreign key rather than a cross-system join — and Alembic already manages this database. |
| **Index type** | **None — exact (flat) scan** **[decision]** | The corpus is ~33 pages ≈ 150–350 chunks. An IVFFlat or HNSW index over a few hundred rows adds tuning surface and *approximate* recall to a problem that exact search solves in single-digit milliseconds. Add an index when the corpus exceeds ~50k chunks, not before. The README states this as the index type, with this reason. |
| **Embeddings** | **`BAAI/bge-small-en-v1.5`** (384 dims), served locally as ONNX via **`fastembed`** **[decision]** | Keeps the project on **one model provider**. Groq offers no embeddings endpoint, so the alternative was an OpenAI key — a second secret, a second billing relationship and a second outage surface, in a codebase whose Phase 1 architecture made a point of isolating exactly one provider. ONNX rather than `sentence-transformers` avoids shipping PyTorch. **Measured 2026-10-04:** 188 MB installed, 64 MB of weights, 279 MB peak RSS, 0.09 s warm model load, 4 ms per query embedding (§12.3). BGE-small also outranks the obvious local alternative, `all-MiniLM-L6-v2`, on MTEB retrieval at the same vector width. See §5.5 for the query-prefix requirement this imposes. |
| **PDF parsing** | **PyMuPDF**, with `pdfminer.six` as a defensive fallback **[decision]** | **Verified 2026-10-04 against all nine candidate PDFs** — correct page count and clean text on every one, including the two with damaged cross-reference tables that `pypdf` refuses outright, and the three using CID-encoded fonts. The fallback is insurance, not load-bearing. §5.2. |
| **HTML extraction** | **`trafilatura`** **[decision, not in the brief's list]** | The brief's parser list covers PDFs only, but document 7 is HTML and needs boilerplate stripping. `trafilatura` is purpose-built for main-content extraction. Any equivalent is fine; the requirement is that nav, cookie banner and footer never reach the index. |
| **Orchestration** | **Plain Python** **[decision]** | The pipeline is six auditable steps. Every claim must be traceable to a chunk id, and a framework that hides the retrieve→prompt→parse boundary makes that traceability harder to verify, not easier. This also matches Phase 1's thin-layer convention. |

---

## 3. Component diagram

```
      OFFLINE (run manually, committed as an artifact)         ONLINE (per request)
      ──────────────────────────────────────────────           ────────────────────

  ┌────────────────┐                                        ┌─────────────────────┐
  │ corpus.yaml    │  7 documents: url, publisher,          │      Browser         │
  │ (checked in)   │  year, year_source                     │  Next.js Chat UI      │
  └───────┬────────┘                                        │  + SourcesPanel       │
          │                                                 └──────────┬───────────┘
          ▼                                                            │ POST /chat
  ┌────────────────┐  HTTP 200 + Content-Length match                  ▼
  │ 1. Fetch       │  → sha256 content hash              ┌─────────────────────────┐
  └───────┬────────┘                                     │    FastAPI Backend       │
          ▼                                              │                          │
  ┌────────────────┐  PyMuPDF │ pdfminer.six │ trafilatura│  ┌────────────────────┐ │
  │ 2. Parse       │  → text + headings + page spans     │  │ ScopeGuard         │ │
  └───────┬────────┘                                     │  │ check_request()    │ │
          ▼                                              │  └─────────┬──────────┘ │
  ┌────────────────┐  heading-aware, paragraph fallback  │   allowed  ▼            │
  │ 3. Chunk       │  → every chunk keeps doc/pub/year/  │  ┌────────────────────┐ │
  └───────┬────────┘    section/page                     │  │ Retriever          │ │
          ▼                                              │  │ embed → cosine →   │ │
  ┌────────────────┐  bge-small-en-v1.5 (ONNX, local)    │  │ floor → group by   │ │
  │ 4. Embed       │  → 384-dim vectors                  │  │ document           │ │
  └───────┬────────┘                                     │  └─────────┬──────────┘ │
          ▼                                              │            │ per doc    │
  ┌────────────────┐                                     │            ▼            │
  │ corpus_snapshot│  ──── loaded by seed command ────▶  │  ┌────────────────────┐ │
  │ .jsonl.gz      │                                     │  │ AnswerSynthesiser  │ │
  │ (checked in)   │                                     │  │ 1 model call PER   │ │
  └────────────────┘                                     │  │ document (§7.2)    │ │
                                                         │  └─────────┬──────────┘ │
  ┌──────────────────────────────┐                       │            ▼            │
  │  Postgres + pgvector          │◀──────────────────────│  ┌────────────────────┐ │
  │  documents / chunks /         │                       │  │ CitationValidator  │ │
  │  conversations / messages /   │                       │  │ (§8.3) chunk ids   │ │
  │  claims / scope_refusals /    │                       │  │ must be in context │ │
  │  retrievals / eval_*          │                       │  └─────────┬──────────┘ │
  └──────────────────────────────┘                        │            ▼            │
                                                          │  ┌────────────────────┐ │
                                                          │  │ ScopeGuard         │ │
                                                          │  │ check_response() + │ │
                                                          │  │ personalisation    │ │
                                                          │  └─────────┬──────────┘ │
                                                          │            ▼            │
                                                          │   persist → respond     │
                                                          └─────────────────────────┘
```

---

## 4. Corpus manifest

`backend/corpus/corpus.yaml` — the checked-in, reviewable definition of what the assistant is allowed
to know. One entry per document, mirroring [problemStatement.md §4.1](./problemStatement.md):

```yaml
- id: who-healthy-diet-394
  name: "Healthy diet"
  publisher: "World Health Organization"
  year: 2026
  year_source: page_last_updated    # document_text | pdf_creation_date | page_last_updated
  source_url: "https://www.who.int/news-room/fact-sheets/detail/healthy-diet"
  media_type: text/html
  # No expected_pages and no sha256: an HTML page is revised in place, so a content
  # pin would fail on every legitimate update. Version identity is
  # page_last_updated + retrieval_date.
```

`year_source` is a required enum, not a comment. Three of the seven documents state no publication
year and their year is inferred from the PDF creation date; the brief forbids inventing a year, and
an inferred year that cannot be distinguished from a stated one *is* an invented year once anyone
reads the citation.

`expected_pages` is an assertion, not documentation. §5.1 fails ingestion if the parsed count differs.

---

## 5. Ingestion pipeline (offline)

Run by `python -m corpus.ingest`. **Never runs at application boot** — see §12.2.

### 5.1 Fetch

For each manifest entry:

1. GET with a browser user-agent, following redirects, **timeout ≥ 240s**.
2. **Assert `Content-Length` equals bytes received.** Non-negotiable: on 2026-10-04 two documents
   truncated silently under a 45-second limit and parsed as 5 and 4 pages instead of 7 and 12. A
   truncated PDF opens, returns fewer pages, and the corpus is quietly short.
3. Assert the content type matches `media_type`, and that a PDF starts `%PDF-` — a landing page,
   redirect stub or error body is not content ([problemStatement.md §4.1](./problemStatement.md)).
4. Record `sha256` of the bytes and `retrieval_date` (UTC).
5. For HTML, also capture the page's own "last updated" string verbatim. For document 7 that string
   plus the retrieval date **is** the version identifier, because the URL is revised in place.

### 5.2 Parse

| Media type | Primary | Fallback |
|---|---|---|
| `application/pdf` | PyMuPDF | `pdfminer.six` on any exception or zero-page result |
| `text/html` | `trafilatura`, **`output_format="xml"`** | — |

The `output_format="xml"` is load-bearing, not incidental. trafilatura's default plain-text output
flattens the document into lines and discards its structure; the XML form preserves
`<head rend="h2">`, `<list>` and `<table>` as distinct elements, which map onto `Block` directly:
a `<head>` becomes `is_heading=True`, and a `<list>` or `<table>` becomes **one** block so it cannot
be split. That last point is the HTML counterpart of §5.3's "never split a table" rule — and here it
is exact rather than heuristic, because the markup states where the list ends.

A web page has no pages, so HTML blocks carry `page=0`, meaning *not paginated*. Claiming page 1
would put a false page number in every citation drawn from the source.

**Verified 2026-10-04 — PyMuPDF handles the entire candidate corpus**, all nine PDFs, with page
counts matching expectations exactly:

| Document | Pages | Extracted chars | |
|---|---|---|---|
| 1 ICMR-NIN brief | 4 | 10,518 | CID fonts |
| 2 DGA 2025–30 | 10 | 17,798 | |
| 3 Ireland Food Pyramid | 7 | 10,242 | **damaged xref** |
| 4 WHO Fact sheet 394 | 6 | 18,914 | CID fonts |
| 5 WHO Five Keys | 2 | 3,639 | |
| 6 FSSAI used cooking oil | 4 | 7,949 | CID fonts |
| Eatwell Guide (candidate) | 12 | 30,192 | **damaged xref** |
| WHO SFA/TFA summary (candidate) | 24 | 75,334 | |
| FSANZ temp control (candidate) | 23 | 41,402 | |

The two damaged-xref documents that `pypdf` rejects with `Invalid object in /Pages` open cleanly in
PyMuPDF. So `pdfminer.six` is **defensive insurance, not load-bearing** — keep it for the unknown
future document, but no document in the current corpus depends on it. Still log which parser produced
each document.

**Import note:** use `import pymupdf`, not `import fitz`. The `fitz` alias is deprecated and warns on
every import.

Parse output per document: ordered blocks of `(text, page_number, heading_level, heading_text)`.

**Known limit, recorded not solved:** documents 1, 3 and 5 are graphic-heavy, and content that exists
only inside a figure is never extracted. Document 3 is 6.37 MB for 7 pages. Nothing in this pipeline
OCRs images; a question answerable only from a figure will correctly produce a not-in-corpus refusal
rather than a wrong answer, which is the right failure direction.

### 5.3 Chunk **[open — problemStatement.md §11.3]**

Proposed strategy: **heading-aware with a paragraph fallback and a token budget.**

1. Split at heading boundaries where the parser found headings.
2. Within a section, accumulate whole paragraphs up to **~512 tokens**, **~64 tokens overlap**.
3. **Never split a table or a numbered-recommendation list.** If one exceeds the budget, it becomes an
   oversized chunk rather than two broken ones — a chunk holding half a storage-time table retrieves
   confidently and answers wrongly, which is worse than no chunk.
4. If a document has no headings (the 2-page brochure), use its own numbered structure — the Five
   Keys — as the section boundary, and set `section_heading` to that key's title.

The real risk in *this* corpus is the inverse of the brief's warning: with 2-to-12-page leaflets, a
whole document can land in one or two chunks, so retrieval returns the leaflet instead of the
passage. Keeping the budget at 512 rather than 1024 tokens is a deliberate bias toward passage-level
granularity. **The README must state the final numbers and what they cost.**

#### Measured 2026-10-04: naive heading detection does not work on this corpus

Step 1 above assumes headings are detectable. A font-size sweep over seven of the candidate PDFs
(spans larger than 1.25× the body size) shows that assumption is **mostly false**:

| Document | Body size | Candidates | What the candidates actually are |
|---|---|---|---|
| 2 DGA | 12.0 | 33 | Cover title fragments: `'Dietary'`, `'Guidelines'`, `'For Americans'` |
| 5 WHO Five Keys | 11.0 | 16 | Title fragments: `'Prevention of'`, `'Foodborne Disease:'` |
| FSANZ temp control | 9.0 | 11 | Title fragments: `'Food Safety:'`, `'Temperature control of'` |
| 3 Ireland Pyramid | 10.0 | 29 | Plausible, but graphic-heavy leaflet |
| 1 ICMR-NIN | 12.0 | 4 | Title + wrapped subtitle |
| 6 FSSAI oil | 12.0 | 2 | Title only, wrapped across two lines |
| 4 WHO FS394 (since replaced by its HTML edition) | 11.0 | **1** | One heading across six pages |

Two distinct problems, both of which Phase 2.1's chunker must handle:

1. **Large text is fragmented across lines and spans.** A cover title becomes three "headings."
   Consecutive spans at the same size on adjacent lines must be merged *before* anything is treated
   as a heading.
2. **Several documents genuinely have no section structure.** Document 4 yields one heading in six
   pages; document 6 yields none beyond its title. For these, the numbered-structure fallback in step
   4 is not an edge case — it is the primary path.

**Consequence for the contract:** [problemStatement.md §8](./problemStatement.md) requires every
chunk to carry a section heading, but some source documents have no sections to carry. `chunks.section_heading`
is nullable in §6, so the schema permits it; what is **not** acceptable is a fabricated heading.
Phase 2.1 must decide between an explicit null, the document's own numbered-item label, or a
synthetic `"<document name> (no sections)"` — and record which, since this is a visible field in
every citation.

Every chunk carries, non-nullable: `document_id`, `document_name`, `publisher`, `year`,
`section_heading`, `page_from`, `page_to`, `ordinal`. A chunk that lost its provenance cannot be
cited, and an uncitable claim does not ship.

### 5.4 Embed and snapshot

Embed `f"{document_name} — {section_heading}\n\n{text}"`, not the bare chunk text. The heading and
document name carry real retrieval signal for short chunks, and a 2-page brochure's chunks are
otherwise nearly contextless.

Output: **`backend/corpus/corpus_snapshot.jsonl.gz`**, checked into the repo — one JSON object per
chunk, with text, all metadata, and the vector.

Record the **embedding model id and revision in the snapshot header**. A vector embedded by one model
is meaningless to another, and a silent model change would degrade retrieval without any error. §12.2's
seed command refuses a snapshot whose model id does not match the running configuration.

### 5.5 Asymmetric encoding — the BGE query prefix

BGE v1.5 English models are trained for **asymmetric** retrieval, and this is the single easiest thing
to get wrong about them, because getting it wrong raises no error:

| Side | Encoding |
|---|---|
| **Passage** (ingestion, §5.4) | The text, bare. **No prefix.** |
| **Query** (request time, §7.1) | `"Represent this sentence for searching relevant passages: " + question` |

Omitting the query prefix does not fail, log, or throw. It quietly costs retrieval quality, which then
reads as "the corpus doesn't cover this" — and with §7.3's not-in-corpus refusal keyed to a similarity
floor, a missing prefix would present as *over-refusal* rather than as a bug.

Two consequences for the implementation:

- `EmbeddingClient` exposes **two distinct methods**, `embed_passages()` and `embed_query()`, rather
  than one `embed()` with a boolean flag. The asymmetry should be impossible to forget at the call
  site.
- `eval/retrieval_set.json` (§13.3) is the regression test for this. A prefix regression shows up as a
  recall@k drop with no other symptom.

**[decision] Why a committed snapshot rather than ingesting against production:**

- Production deploys need no network access to seven government websites, several of which already
  return 403 to non-browser clients.
- The corpus is **pinned**. Re-running ingestion cannot silently change what the deployed assistant
  knows; changing the corpus is a reviewable diff.
- The stale-guidance risk ([problemStatement.md §10](./problemStatement.md)) becomes visible — the
  snapshot has a date and a git history.
- Cost: the file is a few MB, and re-embedding requires a deliberate commit. That is the point.

---

## 6. Data model

New tables, and changes to one existing table.

```sql
documents
  id                uuid PK
  slug              text UNIQUE          -- manifest id, e.g. 'who-healthy-diet-394'
  name              text
  publisher         text
  year              int NULL             -- NULL allowed; never invented
  year_source       text                 -- 'document_text'|'pdf_creation_date'|'page_last_updated'
  source_url        text
  media_type        text
  retrieval_date    timestamptz
  page_last_updated text NULL            -- verbatim, HTML sources only
  content_sha256    text
  page_count        int NULL
  parser_used       text                 -- 'pymupdf'|'pdfminer'|'trafilatura'

chunks
  id              uuid PK               -- DETERMINISTIC uuid5, not uuid4 -- see below
  chunk_key       text UNIQUE           -- 'fsanz-temperature-control-2002:15'
  document_id     uuid FK → documents.id ON DELETE CASCADE
  ordinal         int
  section_heading text NULL
  page_from       int NULL              -- 0 = not paginated (HTML sources)
  page_to         int NULL
  text            text
  token_count     int
  embedding       vector(384)           -- bge-small-en-v1.5
  UNIQUE (document_id, ordinal)

retrievals                              -- one row per /chat turn that reached retrieval
  id              uuid PK
  message_id      uuid FK → messages.id ON DELETE CASCADE
  query_text      text
  k               int
  floor           real
  document_filter uuid NULL FK → documents.id
  created_at      timestamptz

retrieval_hits                          -- the evidence trail for every answer
  id              uuid PK
  retrieval_id    uuid FK → retrievals.id ON DELETE CASCADE
  chunk_id        uuid FK → chunks.id
  score           real
  rank            int
  used            bool                  -- was this chunk passed into a generation call
```

### 6.1 Change to `claims`

```sql
claims
  id          uuid PK
  message_id  uuid FK → messages.id ON DELETE CASCADE
  claim_text  text
  chunk_id    uuid NULL FK → chunks.id          -- NEW: the citation
  source      text NULL                          -- LEGACY, Phase 1 only, always NULL
```

**[decision]** The citation is a **foreign key to `chunks`**, not a denormalised string. Publisher,
year, document name and URL are then derived by join and cannot drift from the corpus. The legacy
`source` Text column stays unused rather than being dropped, so Phase 1 rows remain readable; drop it
in a later migration once no `source IS NOT NULL` rows exist (there are none today).

**`ON DELETE RESTRICT`, not CASCADE.** Deleting a chunk that a conversation cites must fail loudly.
Cascading would silently delete the claim and leave a published answer with one fewer citation than
it was written with — an uncited claim, which [§7](./problemStatement.md) forbids outright.

#### 6.1.1 Chunk ids are deterministic **[decision, revised during Phase 2.3]**

This document originally specified `uuid4` for `chunks.id`. That was wrong, and the reason matters:

> A random key is regenerated on every seed. Re-ingest after a publisher revises a document — which
> [problemStatement.md §10](./problemStatement.md) expects, and which the WHO page in this corpus
> invites, being revised in place under a stable URL — and **every stored citation points at a row
> that no longer exists.**

`chunks.id` is therefore `uuid5(CORPUS_NAMESPACE, "<document_slug>:<ordinal>")`, computed in
[`corpus/ids.py`](../../../backend/corpus/ids.py). It stays a real UUID, consistent with every other
key in the schema, while being reproducible:

| Operation | Effect on ids | Citations |
|---|---|---|
| Re-seed the same snapshot | identical | intact |
| Re-seed after a document is revised | identical where chunking is unchanged | intact for unchanged chunks |
| Re-chunk the corpus (ordinals shift) | change | **affected — deliberately visible** |

Hashing the chunk *text* instead would also survive re-chunking, but any whitespace or overlap tweak
would silently mint new ids. That trades a loud failure for a quiet one, which is the wrong direction
for this project.

`chunk_key` stores the same natural key in readable form. A UUID is not navigable in a log, a query,
or `corpus.show --id`.

`chunk_id` is nullable only because Phase 1 rows exist. **For any message created in Phase 2 it is
enforced NOT NULL in application code** (§8.3) — a claim without a citation does not ship.

### 6.2 Migration

One Alembic revision: `CREATE EXTENSION IF NOT EXISTS vector`, create the four new tables, add
`claims.chunk_id`. No destructive change, no data migration.

---

## 7. Retrieval and answering

### 7.1 Retriever

```python
class RetrievedChunk(BaseModel):
    chunk_id: uuid.UUID
    document: DocumentRef
    section_heading: str | None
    text: str
    score: float

def retrieve(
    query: str, *, k: int = 8, floor: float = 0.69, document_id: uuid.UUID | None = None
) -> list[RetrievedChunk]: ...
```

The query is encoded with `embed_query()` — **with the BGE prefix, per §5.5** — then matched by cosine
similarity over `chunks.embedding`, exact scan, `ORDER BY embedding <=> :q LIMIT k`, filtered by
`document_id` when the caller scopes to one document. That filter is the brief's second retrieval
mode, and it is what the §7.2 per-document loop uses.

`k = 8` and `floor = 0.69` are **measured**, not assumed — Phase 2.4 calibrated both against a
labelled set. See §7.3 and [retrieval-calibration.md](./retrieval-calibration.md).

### 7.2 Per-document answering — the core structural decision

```
retrieve(query, k=8)
  → group surviving chunks by document_id
  → for each document, ONE model call with ONLY that document's chunks
  → collect DocumentAnswer results
```

**Why one call per document rather than one call with all chunks:** the brief forbids blending two
sources into one claim and forbids picking a winner when documents disagree. A single call holding
chunks from three publishers is one fluent sentence away from violating both, and fluency is the
default behaviour of a language model. Splitting the calls makes blending **unrepresentable** — each
call can only cite chunks it was given, and it was given one document's chunks.

Cost: typically 2–3 calls instead of 1, on a corpus where most questions touch one or two documents.
The calls are independent and may run concurrently.

The per-document structured output:

```python
class DocumentAnswer(BaseModel):
    answers_question: bool           # does THIS document address the question at all?
    answer: str                      # "" when answers_question is False
    claims: list[CitedClaim]         # [] when answers_question is False
```

`answers_question` makes "this document has nothing to say" a **first-class, schema-forced output**
rather than something inferred from a similarity score. A chunk can be lexically similar and still
not answer the question; only reading it settles that.

### 7.3 Not-in-corpus refusal

Two gates, cheap first:

1. **Vector floor.** If no chunk scores above `floor`, refuse without any generation call.
2. **Model verdict.** If every `DocumentAnswer.answers_question` is `False`, refuse.

Either path returns:

```jsonc
{
  "type": "not_in_corpus",
  "message": "The guidance documents I searched don't cover this.",
  "searched": [ { "name": "...", "publisher": "...", "year": 2018, "url": "..." }, ... ]
}
```

`searched` is **always the full corpus list** (or the single document, when filtered) — not just the
documents that scored above the floor. The brief requires naming what was searched, and a user cannot
judge the gap from a list that silently omits the documents that scored zero.

**✅ RESOLVED (Phase 2.4, 2026-10-05) — `floor = 0.69`, measured.** Full sweep and reasoning in
[retrieval-calibration.md](./retrieval-calibration.md); the short version:

| | |
|---|---|
| in-corpus top-1 scores | 0.709 – 0.873 (20 questions) |
| out-of-corpus top-1 scores | 0.440 – 0.680 (8 questions) |
| floors that separate the set | 0.68 – 0.70 |
| chosen | **0.69** (midpoint) |

The midpoint, because the band is only **0.02 wide** and neither edge offers a usable margin. 0.68
would follow the usual rule — gate 2 is the stronger gate, so a mis-set floor should fail toward a
wasted model call rather than a wrong refusal — but its margin over the worst negative is 0.0004, a
coincidence rather than a margin. 0.69 gives 0.010 below and 0.019 above.

**The band narrowed from 0.07 to 0.02** when three destroyed tables were quarantined out of the
corpus. Removing the figures did not remove the topics, so questions about them now land on
neighbouring chunks scoring 0.66–0.68. Consequence for this design: **gate 2 is now load-bearing
rather than a backstop**, which is the role §7.3 always assigned it.

Measured at `k = 8`: recall@8 = 0.975, identical at k = 10, so the curve is flat past 8.
Latency median 8.7 ms.

---

## 8. Response contract

### 8.1 The contract changes — deliberately

Phase 1's [../../architecture.md §5](../../architecture.md) aimed for a later milestone to populate
`claims[].source` *without changing the request/response contract*. That goal does not survive
contact with the brief, and the reason is worth stating precisely:

**It is not the citation that breaks it — it is per-document answering.** A citation fits inside the
existing `claims[]`. But a single top-level `answer` string cannot represent "two documents, answered
separately, never merged" without either blending them (forbidden) or picking one (forbidden). The
shape has to change.

The brief anticipates this: *"If your schema doesn't fit real citations, change it and note what you
changed."* This section is that note, and
[problemStatement.md §6](./problemStatement.md) requires it in the README too.

### 8.2 New schema

```python
class DocumentRef(BaseModel):
    id: uuid.UUID
    name: str
    publisher: str
    year: int | None
    url: str

class Citation(BaseModel):
    chunk_id: uuid.UUID
    document: DocumentRef
    section_heading: str | None
    quote: str                      # the chunk text backing this claim

class CitedClaim(BaseModel):
    claim: str
    source: Citation                # was Literal[None]; now REQUIRED and non-nullable

class DocumentAnswerOut(BaseModel):
    document: DocumentRef
    answer: str
    claims: list[CitedClaim]

class ChatAnswerResponse(BaseModel):
    type: Literal["answer"] = "answer"
    document_answers: list[DocumentAnswerOut]   # 1..n, never merged
    searched: list[DocumentRef]
```

`source: Citation` — required, not `Optional`. Phase 1 used `Literal[None]` to make "always null" a
type-level guarantee; Phase 2 uses a required nested model to make "always cited" the same kind of
guarantee. Both are the same technique pointed in opposite directions.

Three places must change together, as
[problemStatement.md §4.7](./problemStatement.md) notes:
[`db/schemas.py`](../../../backend/db/schemas.py),
the JSON Schema in [`services/model_client.py`](../../../backend/services/model_client.py), and
[`lib/api.ts`](../../../frontend/lib/api.ts).

**Note on the model-facing schema:** the model is *not* asked to emit a full `Citation`. It emits
`{claim, chunk_id}` only, choosing from the ids it was given. The backend expands `chunk_id` into the
full citation by database join. A model cannot fabricate a publisher or a URL it never writes.

### 8.3 Citation validation — code, not prompt

After each per-document generation call, before anything is persisted:

1. Every `claim.chunk_id` **must** be in the set of chunk ids passed into *that* call. An id from
   another document, or an invented one, is a `CitationError` → **HTTP 502**, the same hard-failure
   path as a Phase 1 schema violation. No repair, no dropping the offending claim and keeping the
   rest.
2. `claims` must be non-empty whenever `answer` is non-empty. An uncited answer does not ship.
3. **Lexical overlap warning (non-blocking).** Log when a claim shares very little vocabulary with its
   cited chunk. This cannot be a hard gate — paraphrase is legitimate — but it is the signal that
   surfaces "cited the wrong chunk" during evaluation.

Rule 1 is what makes *"model knowledge is not a source"* enforceable rather than aspirational.

---

## 9. Scope guard

[`services/scope_guard.py`](../../../backend/services/scope_guard.py) carries over unchanged in
structure, with its pre-model gate still running **before retrieval** — out-of-scope questions never
reach the index, never cost an embedding call, and never pull a calorie figure out of a guidance
document.

### 9.1 One new category: personalisation

The brief adds a rule Phase 1 has no check for: *"Population-level guidance stays population-level.
The assistant doesn't turn it into a personal recommendation."*

Retrieval makes this a live risk in a way it was not before. Guidance documents say things like
"adults should limit free sugars to under 10% of total energy intake"; that is population guidance,
and restating it is correct. The violation is converting it: *"so you should keep your sugar under
50 g."*

A new response-side rule set: second-person prescriptive framing (`you should`, `your target`,
`for you`, `in your case`) co-occurring with a retrieved quantity. Phase 1's `_PRESCRIPTIVE_CUE`
already catches part of this and is the natural place to extend.

This runs on each `DocumentAnswer.answer` **before** persistence. A tripped check discards the whole
response and returns a scope refusal — the blocked answer and its claims are never written, matching
Phase 1's post-check behaviour.

### 9.2 Two refusals are different types on the wire

```jsonc
{ "type": "refused",       "reason": "calorie_target", "message": "..." }   // policy
{ "type": "not_in_corpus", "message": "...", "searched": [...] }            // coverage
```

Separate discriminated types, not one refusal with a reason code. The brief requires the user to tell
them apart, and they mean opposite things: one says *the assistant will not*, the other says *the
corpus does not*. They also persist differently — policy refusals to `scope_refusals` as in Phase 1,
coverage refusals to `retrievals` with zero `used` hits.

---

## 10. Request sequence

```
Client → POST /chat {conversation_id, message, document_filter?}
  → ScopeGuard.check_request(message)
      ├─ blocked → persist scope_refusals(pre_model) → {type:"refused"}
      └─ allowed ↓
  → embed_query(message)                             [bge-small-en-v1.5, WITH prefix — §5.5]
  → Retriever.retrieve(query, k, floor, document_filter)
      ├─ no chunk above floor → persist retrieval → {type:"not_in_corpus", searched:[all]}
      └─ hits ↓
  → group hits by document_id
  → for each document (concurrently):
        ModelClient.answer_from_document(system_prompt, history, question, chunks)
          → DocumentAnswer | ModelResponseError → 502
  → if every answers_question is False → {type:"not_in_corpus", searched:[all]}
  → CitationValidator.validate(each answer, its context chunk ids)   → CitationError → 502
  → ScopeGuard.check_response(each answer) + personalisation check
      ├─ blocked → persist scope_refusals(post_model) → {type:"refused"}
      └─ allowed ↓
  → persist: user message, assistant message, claims (with chunk_id), retrieval + hits
  → {type:"answer", document_answers:[...], searched:[...]}
```

Persistence remains a single step after **all** gates pass — no partial writes, per Phase 1's
[../../edge-case.md](../../edge-case.md) Phase 4 notes.

### 10.1 Endpoints

| Method | Path | Change |
|---|---|---|
| `POST` | `/chat` | Extended: optional `document_filter`; three response types. |
| `GET` | `/conversations/{id}` | Extended: assistant messages carry grouped, cited claims. |
| `POST` | `/conversations` | Unchanged. |
| `GET` | `/health` | Unchanged. |
| `GET` | `/corpus` | **New.** Lists the corpus — name, publisher, year, url, retrieval date, chunk count. Powers the sources panel's idle state and the document filter. |

---

## 11. Frontend

No rebuild. Four files change, one is new.

| Component | Change |
|---|---|
| [`SourcesPanel.tsx`](../../../frontend/app/components/SourcesPanel.tsx) | Stops being a stub. Shows the chunks behind the selected answer: document name, publisher, year, section heading, the quoted passage, and a link. With no answer selected, shows the corpus from `GET /corpus` — so the panel always tells the user what the assistant can see. |
| [`MessageList.tsx`](../../../frontend/app/components/MessageList.tsx) | An assistant turn renders **one block per document**, each headed by publisher + year. The Phase 1 "Claims (unsourced)" disclosure becomes inline citation markers that select a source in the panel. Two documents must look like two documents — visual merging would undo §7.2. |
| `NotInCorpusNotice.tsx` | **New.** Distinct from `RefusalNotice`. Lists what was searched. Styled as information, not as a policy boundary or an error. |
| [`types.ts`](../../../frontend/app/components/types.ts) | New `DisplayMessage` variant for `not_in_corpus`. |
| [`lib/api.ts`](../../../frontend/lib/api.ts) | `Claim.source` widens from `null` to `Citation`; response union gains the third member. |

Three visually distinct states — answer, policy refusal, coverage refusal — plus the existing generic
error. [../../edge-case.md](../../edge-case.md) already flags refusal/error ambiguity; this adds a
third thing to keep apart.

---

## 12. Deployment

### 12.1 New configuration

| Variable | Where | Notes |
|---|---|---|
| `EMBEDDING_MODEL` | Railway | Default `BAAI/bge-small-en-v1.5`. Must match the snapshot header (§5.4). |
| `FASTEMBED_CACHE_PATH` | Railway | Where the ONNX weights live. See §12.3 — baking them into the image beats downloading at boot. |
| `RETRIEVAL_K`, `RETRIEVAL_FLOOR` | Railway | Tunable without a redeploy of code. |

### 12.2 Seeding, not ingesting

The `Procfile` currently runs `alembic upgrade head && uvicorn ...`. It gains **no** ingestion step.
Loading the committed snapshot is a separate, idempotent command:

```
python -m corpus.seed --snapshot corpus/corpus_snapshot.jsonl.gz
```

Run manually after the migration, skipping rows whose `content_sha256` already matches. Ingestion
needs network access to seven government sites and several minutes; neither belongs in a boot path
that Railway will retry.

### 12.3 Risks to check on first deploy

Both are unvalidated assumptions of the same class [../../deployment.md §5](../../deployment.md)
flagged for Nixpacks' Python detection — cheap to check early, expensive to discover in production.

**1. pgvector availability — ✅ RESOLVED 2026-10-04.** Verified against the project's own Railway
Postgres (`industrious-abundance` / `production`) from the Railway query console:

```sql
SELECT (SELECT count(*) FROM pg_extension            WHERE extname = 'vector') AS installed,
       (SELECT count(*) FROM pg_available_extensions WHERE name    = 'vector') AS available;
-- installed = 1, available = 1
```

`CREATE EXTENSION IF NOT EXISTS vector;` succeeded on the existing database — no image migration
needed, and the Phase 2.3 Alembic revision can create the extension as specified in §6.2. The
fallbacks that were on standby (Railway's pgvector template image, or Qdrant at the cost of the
citation foreign key) are not required.

**Caveat worth keeping:** the extension now exists because it was created by hand during preflight.
The migration must still run `CREATE EXTENSION IF NOT EXISTS vector` so a fresh database — a new
environment, a rebuilt instance, a reviewer's local clone — gets it too. Do not rely on the
hand-created one.

**2. Embedding model footprint.** **Measured locally on 2026-10-04** (`fastembed` 0.8.1,
`onnxruntime` 1.30.0, `BAAI/bge-small-en-v1.5`), so this is no longer an estimate:

| Metric | Measured | Implication |
|---|---|---|
| Installed packages | **188 MB** (`onnxruntime` 80 MB, `numpy` 34 MB, `PIL` 15 MB) | Image grows by ~190 MB |
| Model weights on disk | **64 MB** | Bake into the image — see below |
| Peak RSS, model loaded | **279 MB** | On top of FastAPI + pool. Comfortable on Railway's current tiers; verify yours |
| Import + model load, warm | **0.35 s** (0.26 s import, 0.09 s load) | Negligible at startup |
| Model load, cold (downloading) | **15 s** | The failure mode this section exists to prevent |
| Query embedding | **4 ms** single, 25 ms for a batch of 10 | Irrelevant next to the generation call |
| Output dimensionality | **384** | Confirms `vector(384)` in §6 |

Three conclusions:

- **Weights must be baked into the image.** The 15 s cold load is a network download. A container
  that fetches 64 MB from Hugging Face at boot is a deploy that fails when that host is slow,
  rate-limited or blocked. Fetch at build time and point `FASTEMBED_CACHE_PATH` at the result; warm
  load is then 0.09 s.
- **Load the model at application startup, not lazily.** Even 0.35 s belongs on the health check
  rather than on a user's first request. [../../edge-case.md](../../edge-case.md) Phase 6 already
  flags Railway cold starts; don't add to them.
- **`PIL` is 15 MB of dead weight.** `fastembed` pulls it in for image-embedding models this project
  will never use. Worth trimming if image size matters; harmless otherwise.

If memory proves tight on the chosen tier, the escape hatch is an API-based embedding provider — at
the cost of the single-provider property that motivated this choice (§2).

**3. Local Postgres does not have pgvector either.** `docker-compose.yml` pins `postgres:16`, the
official image, which **does not ship the `vector` extension**. Local dev needs
`pgvector/pgvector:pg16` (same Postgres, extension added) or the Alembic migration fails on a
developer machine before it ever reaches Railway. One-line change, easy to miss.

---

## 13. Evaluation

[../../eval.md](../../eval.md)'s two mechanisms survive. Both need extension, and
`eval/regression_questions.json` needs new cases — the current set has no question that should
produce a not-in-corpus refusal, and no cross-document question.

### 13.1 The prompt inversion is a regression event

Phase 1's system prompt forbids naming an organization as a source — the fix for three
`unverifiable_source` findings. Phase 2 **requires** exactly that. Per
[../../eval.md §2.3](../../eval.md), removing that rule demands a full regression run, and the
highest-severity signal is an outcome-type flip. Expect several: that is the intended change, which is
precisely why the run has to be read rather than skipped.

### 13.2 New failure types

The five types in [../../eval.md §3.3](../../eval.md) stay. `unverifiable_source` changes meaning
entirely — under Phase 1 *any* source was a failure; now a **missing or wrong** source is. Four
additions:

| Type | Definition |
|---|---|
| `uncited_claim` | A factual statement in `answer` prose with no corresponding entry in `claims`. The schema forces claims to be cited; it cannot force the answer to claim everything it asserts. |
| `blended_sources` | One claim's text draws on material from a document other than the one it cites. §7.2 makes this structurally hard, so any occurrence is a design failure worth investigating. |
| `citation_mismatch` | The cited chunk does not actually support the claim. §8.3's lexical-overlap warning is the detector; confirmation is manual. |
| `over_refusal` | Not-in-corpus fired on a question the corpus does answer. Direct feedback on the §7.3 floor. [../../edge-case.md](../../edge-case.md) Phase 8 flagged that over-blocking has no home in the current taxonomy; this is that home. |

### 13.3 Retrieval quality is measurable separately

`eval/retrieval_set.json` — questions with hand-labelled relevant chunk ids, scored for **recall@k**
and **floor precision** without any generation call. This isolates "the retriever missed it" from
"the model ignored it," which a single end-to-end number cannot do.

---

## 14. Directory structure

```
backend/
├── corpus/
│   ├── corpus.yaml                  # the manifest (§4)
│   ├── corpus_snapshot.jsonl.gz     # committed chunks + vectors (§5.4)
│   ├── ingest.py                    # fetch → parse → chunk → embed → snapshot
│   ├── seed.py                      # snapshot → Postgres
│   ├── fetch.py / parse.py / chunk.py
├── services/
│   ├── embeddings.py                # NEW: EmbeddingClient protocol (embed_query/embed_passages)
│   ├── retriever.py                 # NEW: §7.1
│   ├── answer_synthesiser.py        # NEW: §7.2 per-document loop
│   ├── citation_validator.py        # NEW: §8.3
│   ├── model_client.py              # +answer_from_document()
│   └── scope_guard.py               # +personalisation rules (§9.1)
├── prompts/
│   ├── system_prompt.md             # EDITED — attribution rule inverted (§13.1)
│   └── document_answer_prompt.md    # NEW: the per-document generation prompt
├── routers/chat.py                  # +not_in_corpus, +document_filter
└── alembic/versions/xxxx_rag.py

eval/
├── retrieval_set.json               # NEW (§13.3)
└── regression_questions.json        # +not-in-corpus, +cross-document cases

frontend/app/components/
└── NotInCorpusNotice.tsx            # NEW
```

---

## 15. Traceability to acceptance criteria

| [problemStatement.md §8](./problemStatement.md) criterion | Where enforced |
|---|---|
| 5–7 prose documents, recognised authorities | §4 manifest |
| Publisher, year, source URL, retrieval date stored | §6 `documents` |
| No year invented | §4 `year_source` enum, §6 `year` nullable |
| Every URL returns 200 and serves the content | §5.1 steps 2–3 |
| HTML boilerplate stripped; last-updated stored | §5.1 step 5, §5.2 |
| Chunk carries name, publisher, year, section heading | §5.3, §6 `chunks` |
| README states chunking strategy and cost | §5.3 |
| Retrieval across all documents | §7.1 |
| Retrieval filtered to one document | §7.1 `document_id`, §10.1 `document_filter` |
| Answers only from retrieved chunks | §8.3 rule 1 |
| Every claim has document, publisher, year, link | §8.2 `Citation` via FK join |
| No claim ships without a citation | §8.2 required field + §8.3 rule 2 |
| Per-document answers, separate citations | §7.2 |
| Never blended | §7.2 — structurally unrepresentable |
| Disagreement shown, no winner picked | §7.2, §11 per-document rendering |
| Not-in-corpus refusal names what was searched | §7.3 `searched` |
| Scope refusal still enforced in code | §9 |
| Two refusals distinguishable | §9.2 separate wire types, §11 separate components |
| Frontend/backend extended, not rebuilt | §11, §14 |
| Sources panel shows chunks | §11 |
| `claims[].source` carries a real citation | §6.1, §8.2 |
| Schema change documented | §8.1 |
| Population-level stays population-level | §9.1 |
| README states chunk size, overlap, model, index, k | §2, §5.3, §7.1 |

---

## 16. Open decisions this document does not settle

Carried from [problemStatement.md §11](./problemStatement.md), narrowed where possible:

1. **Corpus page ceiling** (§11.1) — unchanged; architecture is indifferent, but §5.3's chunking
   strategy is near-pointless on leaflets and earns its keep on the 23-page FSANZ document.
2. **Relevance floor** (§11.2) — §7.3 gives a placeholder and a two-gate design that limits the
   damage of getting it wrong. Calibration is still owed.
3. **Chunking parameters** (§11.3) — §5.3 proposes 512/64; unvalidated against the real corpus.
4. **Document 7 replacement** (§11.8) — unchanged, and still the highest-value open item: without it
   the corpus has no strong source for one of the system's two stated question shapes.
5. **Pipeline diagram** (§11.7) — §3 is this document's own reading of the pipeline, not a
   transcription of the brief's missing image. Reconcile them when it is recovered.
