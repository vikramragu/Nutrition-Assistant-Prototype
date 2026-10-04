# Ingestion Report — Phases 2.1 – 2.3

Evidence for the Phase 2.1 (§1–6), 2.2 (§7) and 2.3 (§9) exit criteria in
[implementation-plan.md](./implementation-plan.md), and the source for the "what the chunking
strategy cost" section the README owes in Phase 2.10.

Produced by `python -m corpus.ingest` on **2026-10-04** against the corpus frozen in
[problemStatement.md §4.1](./problemStatement.md).

---

## 1. Parameters

| Parameter | Value | Why |
|---|---|---|
| Chunk budget | **400 tokens** | `bge-small-en-v1.5` truncates at 512, and §5.4 prepends `"{document} — {heading}\n\n"` before embedding. A 512-token chunk would lose its tail **silently**. 400 leaves room for the header plus margin. |
| Overlap | **64 tokens**, at sentence boundaries | Half a sentence carried forward is noise in the embedding, not context. |
| Minimum chunk | **40 tokens** | Below this a chunk matches on a phrase and then gives the model almost nothing to answer from. Undersized chunks merge forward where the budget allows. |
| Strategy | Heading-aware, paragraph fallback, whole blocks | Blocks are never split except as a last resort; see §4. |
| Tokenizer | `BAAI/bge-small-en-v1.5`, exact | Not an approximation — the budget is bounded by a hard model limit. |

---

## 2. Result

| Document | Parser | Pages | Blocks | Headings | Chunks | Chars | Tokens med/max |
|---|---|---|---|---|---|---|---|
| `icmr-nin-my-plate-2024` | PyMuPDF | 4 | 83 | 6 | 8 | 10,254 | 261 / 379 |
| `usda-hhs-dga-2025` | PyMuPDF | 10 | 205 | 25 | 25 | 16,646 | 110 / 396 |
| `ie-doh-food-pyramid-2016` | PyMuPDF | 7 | 135 | 27 | 20 | 9,201 | 77 / 249 |
| `who-healthy-diet-2026` | **trafilatura** | — | 49 | 13 | 17 | 21,325 | 224 / 475 |
| `who-five-keys-2013` | PyMuPDF | 2 | 30 | 7 | 5 | 3,498 | 79 / 354 |
| `fssai-used-cooking-oil-2018` | PyMuPDF | 4 | 42 | 9 | 7 | 7,032 | 212 / 400 |
| `fsanz-temperature-control-2002` | PyMuPDF | 23 | 597 | 13 | 23 | 35,438 | 389 / 400 |
| **Total** | | **50 + 1 page** | **1,141** | **100** | **105** | **103,394** | **166 / 475** |

Distribution: median 166, mean 203, max 475. Two chunks exceed the 400-token text budget; both are
lists kept whole rather than split (§1, row 3).

**Embedding-input check** — the number that matters, since it is what the model actually sees:
**max 480 tokens, zero chunks over the 512 window, 32 tokens of headroom on the largest.** Every
chunk embeds whole. The tightest case is `who-healthy-diet-2026:5`, a 475-token `<list>` under the
heading *Fats*, kept intact deliberately.

The `pdfminer.six` fallback was never invoked — PyMuPDF handled all six PDFs.

---

## 3. What it cost

- **Chunks are shorter than the budget allows.** Median 167 against a 400 budget, because section
  boundaries dominate: six of seven documents are leaflets whose sections are a heading plus a
  paragraph or two. The budget is rarely the binding constraint; the document's own structure is.
- **The Irish Food Pyramid fragments worst** — 20 chunks from 7 pages, median 77 tokens. It is a
  visual leaflet with many short labelled panels, and each becomes its own section. Retrieval over
  that document will match narrow phrases.
- **No real tables were preserved, because none were found.** The "never split a table" rule is
  implemented and did fire once, on a structured block in the WHO fact sheet, but the corpus contains
  no verified machine-readable table (see §5).
- **Four sections were dropped entirely** — see §4.1. That is ~1,200 tokens of text deliberately not
  retrievable.

---

## 3a. The HTML parser, and why document 4 changed

Phase 2.1 was first completed against a PDF-only corpus. Reopening it to add an HTML source exposed
that `_parse_html` was **worse than missing** — it hardcoded `is_heading=False` on every block, so
every chunk from an HTML document would have carried `section_heading: None`, failing an acceptance
criterion with no error anywhere. It would also have disabled the §4.1 bibliography guard, which keys
on the heading.

The rewrite uses `trafilatura.extract(output_format="xml")` instead of the default plain-text output,
which is the whole difference:

| trafilatura XML | Block |
|---|---|
| `<head rend="h2">Key facts</head>` | `is_heading=True` |
| `<p>…</p>` | body block |
| `<list><item>…</item></list>` | **one** block, items rejoined with `•` |
| `<table><row><cell>…` | **one** block, cells joined with `\|` |

Keeping `<list>` and `<table>` whole is the HTML counterpart of the "never split a table" rule — and
here it is **exact rather than heuristic**, because the markup states where the list ends. The bullet
markers are deliberate: `chunk._looks_structured()` counts them to decide whether a block may be
split, so dropping them would let a long list be cut in half.

A web page has no pages, so HTML blocks carry `page=0` meaning *not paginated*. Claiming page 1 — as
the old stub did — would put a false page number in every citation from that source.

**Result on the WHO page:** 13 sections extracted (`Key facts`, `Overview`, `Carbohydrates`, `Sugars`,
`Fats`, `Protein`, `Salt/sodium and potassium`, `Vitamins and minerals`, `Foods`, `For infants and
young children`, `How to promote healthy diets`, `WHO response`), 17 chunks, **none without a
heading**.

**Why that document.** It replaces WHO's 2018 *Fact Sheet N°394* PDF, which is superseded — the 2026
page no longer contains `"less than 5%"` or `"30% of total energy"`, figures the PDF states. The
corpus was carrying revised-away guidance.

**Two costs, both accepted:**

- **No completeness check.** WHO serves no `Content-Length`, and the page is revised in place so a
  `sha256` pin would fail on every legitimate update. Ingestion warns on every run rather than
  staying quiet about it.
- **Date extraction needed changing.** The page renders its date as a bare *"26 January 2026"* with
  no "last updated" label, which the existing regex missed entirely. `fetch.py` now tries
  `trafilatura.extract_metadata()` first — it returns `2026-01-26` cleanly — and keeps the regex as a
  fallback for pages that do label it.

---

## 4. Defects found by running it

Five, all found by reading output rather than by reading code. Each is now a guard in the pipeline.

### 4.1 Bibliographies were being indexed

The largest chunk in the first clean run was the WHO fact sheet's **REFERENCES** section — 618 tokens
of `Hooper L, Abdelhamid A… Cochrane Database Syst Rev. 2015`.

This is worse than noise. A retrieved chunk of study names is a direct invitation for the model to
attribute a claim to a paper it has merely seen nearby — reintroducing the `unverifiable_source`
failure that Phase 1 suppressed with a prompt rule, and inverting the point of the milestone: a
citation must point at a passage the reader can open, not at a name.

`chunk.NON_CONTENT_HEADINGS` now excludes reference lists, tables of contents, acknowledgements and
indexes. An exact-match set was not enough — FSSAI titles its bibliography *"Other References"* — so
headings containing `reference` or `bibliograph` are excluded by substring.

Dropped: `REFERENCES` (WHO 394), `Other References` (FSSAI), `Acknowledgments` and `Contents`
(FSANZ).

### 4.2 Heading detection was severing sentences

The first run produced 143 chunks, **37 of them under 50 tokens**, because ordinary body lines were
being classified as headings. The clearest case:

```
heading: "The Standard requires you to ensure that the temperature of potentially
          hazardous food is 5 C or colder or 60 C or hotter ... when"
text:    "you:"
```

Two causes, both fixed:

- **The size ratio was too tight.** At 1.15× body size, the FSANZ document's ordinary text (which
  varies between 9.0 and 10.4 pt) qualified. Raised to **1.25×**, with a separate **bold + 1.02×**
  rule so that run-in bold headings at near-body size are still caught.
- **No continuation test.** A line that does not end a sentence and is followed by a line starting in
  lower case is a **wrapped sentence**, never a heading — whatever size it is set in. This single
  rule removed most of the false positives.

Effect on the FSANZ document: **54 detected headings → 13**, and those 13 are the document's real
sections — Glossary, Introduction, Temperature control requirements, The 2 hour/4 hour guide,
Enforcement, Food receipt, Food storage, Food display.

### 4.3 `Content-Length` was compared against decompressed bytes

Ingestion aborted on FSANZ: *declared 149,624 bytes, received 163,862.* More than declared, not less.
The server had gzipped the response and `httpx` transparently decompressed it, so the check was
comparing the compressed length to the decompressed body.

Fixed by requesting `Accept-Encoding: identity`, and skipping the check entirely if a server
compresses anyway. An unverifiable check is better skipped than silently wrong.

### 4.4 The document missing `Content-Length` was the wrong one

Phase 2.0 recorded that *document 5 (WHO Five Keys)* serves no `Content-Length`. Running the real
pipeline showed it is **document 3 (Irish Food Pyramid)** — and only on `GET`. It does send the
header on `HEAD`, which is why a HEAD-based survey got it backwards. The manifest comment is
corrected.

### 4.6 Sentences severed across chunk boundaries

Found by auditing the chunks before Phase 2.3, specifically to answer "is the chunking actually right
for this data". **12 chunk pairs had a sentence split across the boundary** — 9 of them in the FSANZ
document, the only one long enough for the token budget to bind:

```
chunk N   ends: "...Healthy diet and adequate physical activity are the"
chunk N+1 head: "only strategies for halting or preventing the development..."
```

`_pack()` never splits a block, so this was not a chunking bug in the obvious sense. The cause is
upstream: **PyMuPDF emits a separate block when a paragraph crosses a column or page break**, so one
sentence can arrive as two blocks. Packing then legitimately splits *between* them.

Fixed with `_join_continuations()`, a post-pass over blocks using the same rule heading detection
already uses — the previous block does not end a sentence and the next begins in lower case. The
lowercase requirement is what stops it merging genuinely separate items, since labelled panels and
list entries begin with a capital.

trafilatura has the same problem (markup is not a sentence boundary either: one element ended
*"...review portion sizes and pricing;"* and the next began *"through subsidies) for producers..."*),
so the join runs on both parsers.

**Result: 12 → 0.** Blocks dropped from 1,141 to 694 as fragments rejoined; chunk count held at 105
and median token count rose from 166 to 177 — the same text, packed less raggedly.

One apparent survivor turned out to be a **false positive in the detector**: chunks 12 and 13 of the
WHO document share 1,024 characters of deliberate overlap, so the later chunk legitimately begins
mid-sentence. No text is lost. Worth recording because the obvious "does this chunk start with a
lowercase letter" check cannot by itself distinguish severance from overlap.

### 4.5 Page footers survived as chunks

`"Dietary Guidelines for Americans, 2025–2030 | 6"` became a 12-token chunk. The running-furniture
filter compares line text across pages, and a footer carrying its own page number is textually unique
on every page. Digits are now normalised before the comparison.

---

## 5. Known limitations, recorded not solved

- **Cover-page headings are title fragments.** `who-five-keys-2013:0` is headed `'Prevention of'`;
  `fsanz-temperature-control-2002:0` is headed `'potentially hazardous foods'`. Cover pages have no
  reading order a font-size heuristic can recover. Affects the first chunk of each document only.
- ~~**One 3-token chunk survives** — `'January 2026'` under heading `'Vegetarians & Vegans'` in the
  DGA.~~ **Resolved** by the §9.5 reading-order fix, which placed the stray date inside the block it
  belongs to. The smallest chunk is now 40 tokens, exactly `MIN_CHUNK_TOKENS`.
- **Graphic-only content is not indexed.** Documents 1, 3 and 5 are graphic-heavy; anything that
  exists only inside a figure never reaches a chunk. A question answerable only from a figure will
  correctly produce a not-in-corpus refusal rather than a wrong answer — the right failure direction.
- **`find_tables()` is still not used.** It reported bulleted prose as 5-column grids during Phase
  2.0. The "never split a table" rule is driven by counting list markers instead, which errs toward
  keeping blocks together.

---

## 6. Exit criteria

- [x] Every document fetches with byte count matching `Content-Length` — where served; two documents
      cannot be checked that way and fall back to the `sha256` pin (§4.3, §4.4).
- [x] Parsed page count matches `expected_pages` for every PDF — 7/7, asserted in code.
- [x] HTML boilerplate absent — **now genuinely exercised.** Document 4 is HTML; `trafilatura` with
      `favor_precision=True` strips nav, cookie banner and footer, and the extracted 13 sections
      contain no site furniture (§3a).
- [x] Every chunk carries document name, publisher, year, section heading, page range, ordinal —
      **0 of 105 chunks lack a section heading**, including all 17 from the HTML source.
- [x] Manual read-through of 20+ chunks — 21 read across the PDF corpus, plus all 17 HTML chunks, plus
      targeted reads of the 2 hour/4 hour guide, the danger-zone definition, the oversized chunks and
      the shortest chunks. Defects in §4 are what those reads found.
- [x] Chunk-count and token-length distribution recorded — §2.

---

## 7. Phase 2.2 — embeddings and the snapshot

### 7.1 The finding that justified the whole §5.5 warning

[architecture.md §5.5](./architecture.md) warns that forgetting the BGE query prefix fails silently.
It turns out the trap is one level deeper than written, and `fastembed`'s own API is the thing that
sets it.

**`fastembed.TextEmbedding.query_embed()` does not apply the BGE prefix.** Measured 2026-10-04:

```
embed(["<text>"])  vs  query_embed(["<text>"])   ->  np.allclose(...) is True
query_embed(...)   vs  embed([PREFIX + text])    ->  cosine 0.979  (different vectors)
```

A method literally named `query_embed` is byte-identical to the passage encoder for this model.
Delegating to it — the obvious implementation, and the one a reviewer would assume correct — would
have meant the prefix was never applied anywhere, costing retrieval quality with nothing in any log
to say so.

`services/embeddings.py` therefore prefixes explicitly in `embed_query()` and never calls
`query_embed()`. `tests/test_embeddings.py` pins this with seven tests, two of which matter most:

- **query ≠ passage** for identical text — catches a dropped prefix.
- **query == manually-prefixed passage** — catches a *typo* in the prefix, which would still produce
  an asymmetry and still pass the first test while silently degrading retrieval.

Two further tests guard failures that nothing else would detect: vectors are unit-normalised (which
is why pgvector's `<=>` cosine operator is the right choice), and **batch order is preserved** —
chunks are zipped with vectors positionally when the snapshot is written, so a reordering client
would pair every chunk with another chunk's vector, undetectably.

### 7.2 Snapshot

`backend/corpus/corpus_snapshot.jsonl.gz` — **105 chunks, 177 KB gzipped.** Line 1 is a header; every
subsequent line is one chunk plus its 384-float vector.

```jsonc
{"schema":"corpus-snapshot/1","embedding_model":"BAAI/bge-small-en-v1.5","dimension":384,
 "query_prefix":"Represent this sentence for searching relevant passages: ","chunk_count":105,
 "documents":[{"id":"who-healthy-diet-2026","publisher":"World Health Organization",
               "retrieval_date":"...","content_sha256":"...","page_last_updated":"2026-01-26",
               "parser_used":"trafilatura", ...}]}
{"document_id":"who-healthy-diet-2026","ordinal":3,"text":"• The consumption of free sugars ...",
 "section_heading":"Sugars","embedding":[0.0231,-0.0417, ... ]}
```

`query_prefix` is in the header deliberately: the prefix is part of the contract between index and
query, so a snapshot built without it is not interchangeable with one built with it, and the header
should say which.

**Mismatch is refused, not warned about.** Seeding a pgvector index with vectors from a different
model produces a database that answers every question confidently and wrongly, with nothing in the
logs. Verified: a wrong `expect_model` and a wrong `expect_dimension` both raise `SnapshotError`.

**Determinism** — two consecutive runs over the same corpus: **all 105 chunk lines byte-identical**;
the only difference is `created_at` in the header. `GzipFile(mtime=0)` removes the gzip timestamp so
that is the sole source of churn. The committed file therefore diffs as one line unless the corpus
actually changed.

### 7.3 A defect the snapshot exposed

The header carried `page_last_updated: None` for the WHO page — a document whose manifest declares
`year_source: page_last_updated`, making that date the provenance for its year.

Cause: `--use-cache` rebuilds a `FetchedDocument` from disk and silently skipped the date extraction
that the network path performs. The cached path must reproduce *everything* the network path derives,
not just the bytes. Fixed, and `retrieval_date` now comes from the file's mtime rather than the clock
— the document was retrieved when it was downloaded, not when the cache was read.

### 7.4 Early evidence for the Phase 2.4 relevance floor

Not a Phase 2.2 deliverable, but it fell out of verifying the vectors retrieve at all:

| Query | Top hit | Score |
|---|---|---|
| "How long can I leave cooked food out of the fridge?" | FSANZ § Temperature control requirements | 0.778 |
| "How much free sugar should I eat?" | WHO § Sugars (the "less than 10%" chunk) | 0.827 |
| "Can I reuse frying oil?" | FSSAI § Handling and disposal | 0.800 |
| **"What is the capital of France?"** | *(noise)* | **0.440** |

In-corpus 0.78–0.83 against out-of-corpus 0.44 is a wide, clean separation. It suggests a floor
somewhere around 0.55–0.65 — but this is four questions, not a calibration. [Phase
2.4](./implementation-plan.md) still owes the labelled sweep.

### 7.5 Exit criteria

- [x] Vectors are 384-dimensional — asserted in the client and in `test_dimension_matches_the_vector_column`.
- [x] A unit test asserts `embed_query()` applies the prefix and `embed_passages()` does not — §7.1,
      7 tests passing.
- [x] The snapshot carries the model id; loading refuses a mismatch — §7.2, both mismatch paths verified.
- [x] Snapshot committed; regeneration byte-stable or the difference explained — §7.2: chunk lines
      byte-identical, `created_at` is the only changing field.

---

## 8. Inspecting the chunks

`python -m corpus.show` exists so the review above is repeatable rather than ad hoc:

```bash
python -m corpus.ingest --out chunks.json   # produce
python -m corpus.show                       # one line per document
python -m corpus.show --headings            # every section heading, by document
python -m corpus.show --doc fsanz           # every chunk in a document
python -m corpus.show --find "2 hour/4 hour"
python -m corpus.show --id who-healthy-diet-2026:5 --full
python -m corpus.show --short 60            # suspiciously small chunks
```

After Phase 2.3 the same questions are answerable in SQL against the `chunks` table. Browse with an
explicit column list — `chunks.embedding` is a 384-float vector and will make a row unreadable in
most clients:

```sql
SELECT id, document_id, ordinal, section_heading, page_from, page_to,
       token_count, left(text, 120) AS preview
FROM chunks ORDER BY document_id, ordinal;
```

A Parquet export exists for grid viewers (VS Code **Parquet Viewer**), generated from the snapshot
with DuckDB and deliberately excluding `embedding` — 384 floats no viewer can render, and ~90% of
the bytes:

```bash
python -c "
import duckdb
duckdb.connect().sql(\"\"\"COPY (
  SELECT document_id, ordinal, document_id||':'||ordinal AS chunk_key,
         section_heading, page_from, page_to, token_count,
         document_name, publisher, year, text
  FROM read_json_auto('corpus/corpus_snapshot.jsonl.gz', ignore_errors=true)
  WHERE document_id IS NOT NULL ORDER BY document_id, ordinal
) TO 'corpus/chunks.parquet' (FORMAT PARQUET)\"\"\")"
```

`chunks.parquet` is gitignored: it is derived, and the snapshot is the artifact of record.

---

## 9. Phase 2.3 — data model and seeding

### 9.1 What was built

| Artifact | Purpose |
|---|---|
| `alembic/versions/b1c7e4a92f08_rag_corpus_tables.py` | `CREATE EXTENSION vector`; `documents`, `chunks`, `retrievals`, `retrieval_hits`; `claims.chunk_id` FK |
| `db/models.py` (extended) | SQLAlchemy models, `Vector(384)` column |
| `corpus/ids.py` | deterministic `uuid5` chunk ids |
| `corpus/seed.py` | idempotent snapshot → Postgres load |

Two decisions carry most of the weight, and both are about **citation durability**:

**Chunk ids are `uuid5(CORPUS_NAMESPACE, "slug:ordinal")`, not `uuid4`.** `claims.chunk_id` is a
foreign key. With random ids, re-seeding would regenerate every key and orphan every citation in
every past conversation. Guidance documents *are* revised — the WHO page in this corpus is revised
in place under a stable URL — so re-ingestion is expected, not hypothetical. A citation pointing at
a vanished chunk is precisely the failure this milestone exists to eliminate.

**`claims.chunk_id` is `ON DELETE RESTRICT`, not `CASCADE`.** Deleting a chunk a conversation cites
should fail loudly. CASCADE would silently delete the claim, leaving an answer whose citation
evaporated — the quiet version of the same failure.

### 9.2 Exit criteria

Re-verified 2026-10-05 in the **project** virtualenv against the pinned versions, not the scratch
environment the original run used (see §9.3).

| Criterion | Evidence |
|---|---|
| Migration applies to an empty database | Fresh `migration_probe` DB: `-> fe0fc63c332c -> b1c7e4a92f08`, clean |
| Migration applies to the existing Phase 1 database | `alembic current` = `b1c7e4a92f08 (head)` on `nutrition` |
| Downgrade is correct | `downgrade fe0fc63c332c` leaves exactly the 7 Phase 1 tables, no `vector` columns |
| Seed is idempotent | Re-run: `documents inserted=0 skipped=7, chunks written=0` |
| Row counts stable | 7 documents, 105 chunks, `vector_dims` = 384, 0 orphans, pgvector 0.8.7 |
| Deleting a document cascades | `DELETE FROM documents WHERE slug='who-five-keys-2013'` → its 5 chunks gone, 0 orphans (rolled back) |
| A cited chunk cannot be deleted | `ERROR: update or delete on table "chunks" violates foreign key constraint "fk_claims_chunk_id"` — `confdeltype` = `r` |
| Ids match the generator | `ids.py chunk_id('fsanz-temperature-control-2002', 0)` = `2aadd41b-…92e0` = the stored row |
| Phase 1 rows still read | `claims`: 0 rows locally, so **vacuously true** — the constraint is nullable and the column defaults NULL, but this is untested against real legacy data |
| No ingestion step in `Procfile` | `web: alembic upgrade head && uvicorn main:app …` — unchanged |

Both destructive checks ran inside transactions that were rolled back; the seeded data is untouched.

### 9.3 Defects found by re-verifying

#### 9.3.1 The verification had never run against the pinned versions

The project virtualenv did not contain `fastembed` or `pgvector` at all. Every Phase 2.1–2.3 run had
used a throwaway environment under the session scratchpad, which resolved **unpinned**:

| Package | `requirements*.txt` pin | What actually ran |
|---|---|---|
| `pgvector` | 0.4.1 | 0.5.0 |
| `trafilatura` | 2.0.0 | 2.3.0 |
| `pymupdf` | 1.26.6 | 1.28.2 |

So the pins were never exercised, and the scratch environment is deleted on reboot — the work was
reproducible only by accident. Fixed by installing `requirements.txt` into `backend/.venv` and
re-running everything above; **38 tests pass** against the pinned set.

This is the failure mode worth remembering: not a wrong answer, but a *verification* that proved
something about an environment nobody would ever run again.

#### 9.3.2 The image swap left `template1` unable to spawn databases

Moving to `pgvector/pgvector:pg16` carried over a collation version mismatch (2.41 recorded, 2.36
provided). Earlier this was fixed on the `nutrition` database only. `template1` and `postgres` were
still stale, and there the mismatch is not a warning:

```text
ERROR:  template database "template1" has a collation version mismatch
```

`CREATE DATABASE` was therefore **impossible** — which is how this was found, trying to build the
empty-database probe. Any new environment, test database or reviewer's clone would have hit it.
Fixed with `ALTER DATABASE template1 REFRESH COLLATION VERSION` and the same on `postgres`.

#### 9.3.3 One chunk is below `MIN_CHUNK_TOKENS`

```text
3t  usda-hhs-dga-2025:24  [Vegetarians & Vegans]  'January 2026'
```

A trailing date fragment survived as its own chunk. Recorded here as unfixed, with the plan of
letting Phase 2.4's sweep decide whether it mattered.

**Resolved incidentally by §9.5.** The date was not a chunking problem at all — it was the last
block of a page whose reading order was wrong, and once the blocks were ordered correctly it merged
into the text it belongs to. The smallest chunk in the corpus is now 40 tokens. Worth noting as a
pattern: two of the three defects filed against the chunker turned out to be the parser.

### 9.5 PDF block order — found in Phase 2.4, fixed in the parser

Found while reading the corpus to hand-label the Phase 2.4 retrieval set, which is the only reason
it was found at all: it produces no error, no warning and entirely plausible output.

**Every section heading in the DGA was attached to the wrong section** — one behind. A chunk whose
text read *"Eat a variety of colorful, nutrient-dense vegetables and fruits…"* was filed under
`section_heading: "Gut Health"`.

Two consequences, both bad for a project whose entire premise is a checkable citation:

- **The citation names the wrong section.** A reader following it looks in the wrong place —
  arguably worse than no heading at all, which at least does not mislead.
- **The wrong heading is embedded.** `Chunk.embedding_input()` prefixes
  `"{document_name} — {section_heading}"`, so every DGA vector carried a heading from a different
  section.

This also retires a "known limitation" recorded in §5. *"How many portions of fruit and vegetables a
day?"* returning DGA § *Gut Health* at 0.758 was never a retrieval weakness. Retrieval had found
exactly the right chunk; the chunk was mislabelled. The symptom was recorded against the wrong
component for a full phase.

**Cause.** A PDF's content-stream order is arbitrary, and the DGA emits each 18pt heading *after*
the bullets it introduces. `chunk._sections()` is correct — given `[bullets_A][HEADING_A][bullets_B]`
it can only attach `bullets_B` to `HEADING_A`. The input order was wrong, not the logic. Measured:
content-stream order disagrees with position on 9/9 DGA pages, 13/23 FSANZ, 7/7 Irish — **most pages
of every document**.

**Two failed fixes before the one that worked**, each of which is why the final one is shaped as it is:

| Attempt | Result |
|---|---|
| `sort=True`, i.e. order by `(y, x)` | Headings correct, but the DGA's two columns of bullets interleave: *"…nutrient-dense protein **+ Consume meat with no…**"*, spliced mid-sentence |
| Band → column → y, with the column split at the page midpoint | DGA correct; **FSANZ regressed 6 → 11 severed chunks**. FSANZ is single-column from x=147 to x=497 on a 595pt page, so a midpoint test files its long lines as "right column" and its short lines as "left", shuffling ordinary paragraphs |

**The fix** (`parse._reading_order`): group blocks into bands delimited by headings, detect columns
*within each band* by finding a vertical strip no body block crosses, and order band → heading →
column → y. Per band rather than per page because one DGA page sets three cards in two columns and a
fourth full width — no single page-wide gutter describes it. Headings are excluded from the gutter
test because they span both columns and would mask it.

A page with no gutter is left in plain top-to-bottom order, so single-column documents are untouched.

**Result:** 105 chunks before and after; FSANZ byte-identical; DGA headings all correct; severed
chunks 25 → 24 overall.

### 9.6 Re-seeding silently kept the old chunks

Immediately after the fix above, `python -m corpus.seed` reported `skipped=7, chunks written=0` —
and the database kept every wrong heading.

`content_sha256` hashes the **source bytes**. The PDFs had not changed; only the parser had. So the
skip-if-unchanged test was comparing the wrong thing, and would have done so after *any* parser or
chunker fix. The corrected corpus would have stayed out of the database with the log reporting
success.

Fixed by comparing a fingerprint of the chunking itself — ordinal, heading and text, in order —
against the rows already stored, so the skip means what it claims. No migration: the comparison is
computed from the `chunks` table at seed time. Re-seeding now reports
`same source, re-chunked -- replacing` and is idempotent on the run after.

### 9.8 Destroyed tables, and the corpus quarantine

Also found in Phase 2.4, by the same means: reading the data.

A PDF stores positioned text boxes, not rows and columns. When a table is extracted, its labels and
its numbers arrive as separate runs of text with nothing linking them. The Irish food pyramid's
calorie table became:

```text
Active Child Teenager Adult Adult Inactive Teenager Adult Adult ...
Active 2000kcal Inactive 1800kcal Active 2500kcal Inactive 2000kcal
```

Four labels, four values, no way to pair them. On the page the labels sit at y=265 and y=356 and the
values at y=529; the relationship was only ever column alignment.

**This is not a parsing bug and not a chunking bug**, and — verified 2026-10-05 — **it is not a PDF
bug either.** A perfect HTML table with explicit `<tr>`/`<td>` was pushed through this pipeline and
came out identically flattened: *"Group Active Inactive Teenager 2500 kcal 1800 kcal Adult 2000 kcal
2000 kcal"*. The cause is that the pipeline renders everything to plain text and a chunk is a
string, so the grid dies at that step regardless of source format.

The PDF does not cause the problem; it makes it **unrecoverable**. In HTML the rows and columns are
present in the source and merely discarded, so that case is fixable. In a PDF they exist only as
coordinates, reconstructing them means inferring which header owns which cell, and `find_tables()`
was already shown unreliable here. Half-right is worse than nothing: a confidently cited calorie
figure attached to the wrong age group.

Consequence: **replacing this document with another PDF would change nothing, and replacing it with
an HTML version would change nothing today either.** A real fix means representing a table as
something other than a flat string — one row per line, or structured data carried into the chunk.
Not attempted; the quarantine below is containment, not a cure.

**Measured risk, not assumed.** The chunk scored above the relevance floor on every calorie question
tried, ranking **1st** for *"calories for an inactive adult over 51?"* (0.676), and Phase 1's scope
guard blocks none of those questions. The path from an ordinary question to a wrong number under a
valid-looking citation was open end to end.

**Resolution: a reviewed quarantine list in `corpus.yaml`**, three passages, each with its reason.
Applied in `chunk._quarantine_reason`; `verify_quarantine_rules` raises if a rule stops matching,
because a stale rule means the passage is back in the index while the manifest claims otherwise.

Two findings from doing it:

- **Excluding one half of a table made things worse.** With only the label row quarantined, the
  orphaned values re-chunked under the heading *"Average daily calorie needs for all foods and
  drinks for adults"* — which reads as authoritative — and scored **higher** than before, 0.719.
- **No automatic rule works.** Repetition ratio, prose density and function-word density were each
  measured across all 105 chunks; none separates a destroyed table from ordinary bulleted guidance.
  A detector aggressive enough to catch these would drop real advice.

**Reviewed and deliberately kept:** the ICMR "My Plate" grams-per-day table, whose *values* did not
survive extraction at all. With no numbers present there is nothing to misattribute — a model asked
for grams finds none and declines, which is the safe failure. It also carries a genuine standalone
sentence (sugar restricted to 25–30 g/day).

**Cost:** 105 → 103 chunks, and the retrieval floor's separation band narrowed from 0.07 to 0.02
wide. The corpus can no longer state a weekly alcohol limit at all. Both are accepted:
see [retrieval-calibration.md §4](./retrieval-calibration.md).

### 9.7 Known limitation carried forward

`claims.chunk_id` is nullable, which is what keeps Phase 1 rows valid. Nothing yet enforces that a
*new* claim has one. That enforcement belongs to Phase 2.5's citation validation, not to the schema —
noted here so it is not mistaken for an oversight.
