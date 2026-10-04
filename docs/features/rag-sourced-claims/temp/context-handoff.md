# Context handoff — Phase 2 (RAG over official dietary guidance)

**Purpose.** Paste this at the start of a new conversation. It carries the *decisions and their
reasons* — the part that lives in conversation rather than in code. Everything else is on disk and
can be read directly.

**Status as of 2026-10-05:** Phases 2.0 – 2.4 complete and verified. **Phase 2.5 (answer layer and
citation validation) is next.**

> This is a working memo, not a specification. Where it disagrees with
> [`architecture.md`](../architecture.md) or [`implementation-plan.md`](../implementation-plan.md),
> those win.

---

## 1. What the project is

A nutrition assistant that answers **only** from retrieved text drawn from official public dietary
guidance, with a real citation on every claim. Phase 1 built the chat app; Phase 2 adds the
retrieval layer.

**The guiding principle, from `architecture.md` §1 — apply it to every new decision:**

> *Make the failure structurally impossible rather than prompting against it.*

Two consequences that look odd until you know the reason:

- **One generation call per document.** Not one call with all documents in context. This makes
  blending two publishers' guidance into one sentence *unrepresentable*, rather than discouraged.
- **Citation validation is mechanical**, not instructed. "Model knowledge is not a source" is
  enforced by checking the citation resolves, not by asking the model nicely.

## 2. Stack decisions, and why

| Decision | Reason |
|---|---|
| **No new LLM provider** — Groq `openai/gpt-oss-120b` stays | Phase 1's choice; Phase 2 adds no generation provider |
| **Embeddings run locally** — `fastembed` 0.8.1, ONNX | Groq has **no embeddings endpoint**. Local keeps the project single-provider instead of adding a second vendor just for vectors |
| **`BAAI/bge-small-en-v1.5`, 384 dims** | User's suggestion, and a good fit: small, strong on retrieval, cheap enough to embed a query per turn (~4 ms) |
| **pgvector, not Chroma/Pinecone** | Railway Postgres already exists. A separate vector store would add an operational dependency for 105 rows |
| **No ANN index** (no IVFFlat/HNSW) | At ~103 chunks an exact cosine scan is single-digit ms. ANN would add tuning surface and *approximate* recall to a solved problem. Revisit above ~50k chunks |
| **Snapshot is gzipped JSONL, not Parquet** | The snapshot is committed so corpus changes are **reviewable git diffs**. Parquet is binary. A Parquet export exists separately for grid viewers and is gitignored |
| **Ingestion is offline, not at boot** | Production never parses a PDF or reaches a publisher's site. `Procfile` runs migrations + uvicorn, nothing else |

### BGE is asymmetric — this matters

Queries must be prefixed `"Represent this sentence for searching relevant passages: "`; passages
must be bare. `services/embeddings.py` exposes **two separate methods**, `embed_query()` and
`embed_passages()`, so the distinction cannot be forgotten at a call site.

**Do not use `fastembed.query_embed()`.** It does *not* apply the prefix — its output is
byte-identical to `embed()`. We verified this (`np.allclose(...) is True`). Using it would have
silently degraded retrieval and presented as over-refusal. `test_query_encoding_equals_manually_prefixed_passage`
exists to catch even a typo in the prefix string.

## 3. The corpus — frozen, do not change casually

Seven documents, 50 PDF pages + 1 web page, **103 chunks** (105 chunked, 3 quarantined — §5c). Ceiling of 23 pages per document (started
at 15; raised once for FSANZ).

| id | publisher | year | type |
|---|---|---|---|
| `icmr-nin-my-plate-2024` | ICMR–NIN (India) | 2024 | PDF, 4p |
| `usda-hhs-dga-2025` | USDA & HHS (US) | 2025 | PDF, 10p |
| `ie-doh-food-pyramid-2016` | Dept. of Health (Ireland) | 2016 | PDF, 7p |
| `who-healthy-diet-2026` | WHO | 2026 | **HTML** |
| `who-five-keys-2013` | WHO | 2013 | PDF, 2p |
| `fssai-used-cooking-oil-2018` | FSSAI (India) | 2018 | PDF, 4p |
| `fsanz-temperature-control-2002` | FSANZ | 2002 | PDF, 23p |

`year` is nullable and `year_source` records provenance (`pdf_creation_date`, `document_text`,
`page_last_updated`). **Never invent a year** — the brief forbids it.

**Document 4 was swapped** from WHO's superseded 2018 PDF to WHO's current 2026 HTML page. The old
PDF still carried `"less than 5%"` free sugars and `"30% of total energy"` from fat — guidance WHO
has since revised away. The corpus was shipping stale advice.

## 4. Decisions inside the pipeline

**Chunking: 400 text tokens, 64 overlap, 40 minimum.** 400 = 512 window − 64 header − 48 margin.
Every chunk's embedding input is prefixed with `"{document_name} — {section_heading}"` so a chunk
carries its own context into the vector.

**Chunk ids are `uuid5(CORPUS_NAMESPACE, "slug:ordinal")`, never `uuid4`.** `claims.chunk_id` is a
foreign key; random ids would orphan every past citation on every re-seed. Guidance documents *are*
revised (the WHO page changes in place under a stable URL), so re-ingestion is routine.
Hashing the chunk *text* was considered and rejected: it survives re-chunking but mints new ids on
any whitespace tweak — trading a loud surprise for a quiet one.

**`claims.chunk_id` is `ON DELETE RESTRICT`.** Deleting a cited chunk must fail loudly. CASCADE
would silently delete the claim and leave an answer whose citation evaporated.

**Bibliographies are excluded** (`NON_CONTENT_HEADINGS`, plus a substring test for
`reference`/`bibliograph`). WHO's REFERENCES section was the single largest chunk — 618 tokens of
study names, a direct `unverifiable_source` re-entry vector. FSSAI titles its bibliography "Other
References", which is why the substring test exists.

## 5. The HTML parser story — the one worth remembering

`_parse_html()` hardcoded `is_heading=False` on every block ([`parse.py`](../../../../backend/corpus/parse.py)).

It raised **no error**. It would have silently produced `section_heading: None` on every chunk from
the HTML document — failing an acceptance criterion — *and* disabled the bibliography guard for that
document, since the guard keys off headings. The PDFs were fine throughout; only the HTML path was
broken, which is why "PDF seems to work fine" was both true and beside the point.

Fixed by rewriting against `trafilatura.extract(raw, output_format="xml", ...)` and mapping
`<head>` → `is_heading=True`, with `<p>/<list>/<table>/<quote>` as body. List items are rejoined
with `• ` so `_looks_structured()` recognises them.

**The lesson, which generalises:** the defect produced plausible output. Nothing failed. It was
found by *looking at the data*, not by running the code. Phase 2.4's labelled set exists for the
same reason.

## 5b. Every heading was attached to the wrong section

The most serious defect found in Phase 2 so far. Fixed 2026-10-05 in `parse._reading_order`. Read
this one before touching the parser.

### What a PDF actually is

A PDF does not store a document. It stores a pile of text boxes, each saying *"put this text at
this spot on the page."* **The order the boxes are stored in does not have to match the order a
person reads them.** The page still looks right, because every box carries its coordinates.

Our parser read the boxes in stored order. That was the whole bug.

### The example

Page 3 of the US Dietary Guidelines, as a person sees it:

```text
Gut Health                                ← heading
• Your gut contains trillions of bacteria...

Eat Vegetables & Fruits                   ← heading
• Eat a variety of colorful vegetables and fruits...
```

The order those boxes are stored in:

```text
• Your gut contains trillions of bacteria...
Gut Health                                ← heading comes AFTER its own bullets
• Eat a variety of colorful vegetables and fruits...
Eat Vegetables & Fruits                   ← and again
```

`chunk._sections()` walked that list and reasoned, correctly: *"I just saw a heading, so what
follows belongs under it."* It therefore filed the **vegetables** text under **Gut Health**. Every
heading in the document shifted one section down. `_sections()` was never wrong — its input was.

### Why it mattered, twice

1. **The citation pointed at the wrong place.** "Eat a variety of colourful vegetables — *Dietary
   Guidelines for Americans, § Gut Health*." Open the PDF, turn to Gut Health, find text about
   bacteria. A checkable citation is the entire point of this milestone.
2. **It poisoned the search index.** `Chunk.embedding_input()` glues the heading onto the front of
   the text before embedding, so the vegetables chunk was indexed as partly about gut health.

### The part worth sitting with

**Nothing broke.** No error, no warning, no crash. Every chunk had a real heading that genuinely
appeared in that document — just not *that chunk's* heading. Output looked entirely reasonable.

It was found only because Phase 2.4 required reading all 105 chunks by hand to build the eval set.
No test would have caught it. And a "known limitation" had already been recorded against the wrong
component for a whole phase: *"fruit and vegetable portions returns DGA § Gut Health — retrieval
weakness."* Retrieval was finding exactly the right text. The label on it was wrong.

### Two fixes that failed first

Each failure is why the final shape is what it is — do not "simplify" it back.

| Attempt | Why it failed |
|---|---|
| Sort boxes by `(y, x)` (PyMuPDF's `sort=True`) | Headings correct, but the DGA's two bullet columns interleave: *"…nutrient-dense protein **+ Consume meat with no…**"*, spliced mid-sentence |
| Bands, with columns split at the page midpoint | DGA correct, but **FSANZ regressed 6 → 11 severed chunks**. FSANZ is single-column from x=147 to x=497 on a 595pt page, so a midpoint test files its long lines as "right column" and its short ones as "left" |

### What works

`parse._reading_order`: group boxes into **bands** delimited by headings, detect columns **within
each band** by finding a vertical strip no body box crosses, then order band → heading → column → y.

- **Per band, not per page**, because one DGA page sets three cards in two columns and a fourth full
  width. No single page-wide gutter describes that page.
- **Headings excluded from the gutter test**, because they span both columns and would mask it.
- **No gutter found → plain top-to-bottom**, so single-column documents are untouched.

Result: 105 chunks before and after, FSANZ byte-identical, every DGA heading correct.

### Then the fix nearly didn't land

Re-running the loader after fixing the parser printed `skipped=7, chunks written=0` — "nothing
changed." It decides whether a document needs reloading by hashing the **original PDF bytes**. The
PDFs had not changed; only our reading of them had. So it skipped everything, kept every wrong
heading, and reported success.

That is arguably worse than the original bug: a tool claiming to be up to date when it is not.
`corpus.seed` now also fingerprints the chunking itself — ordinal, heading, text — so the skip means
what it says.

### Three lessons, which are really one

- The defect was **invisible in the code and visible in the data**. Look at the data.
- **Two of the three defects filed against the chunker were actually the parser.** Check where the
  input comes from before blaming the thing that consumes it.
- **"Idempotent" has to mean idempotent with respect to the thing you actually changed.**

## 5c. Tables that extraction destroys, and the corpus quarantine

Found right after §5b, the same way: reading the data. Different problem, different answer.

### What goes wrong

A PDF stores positioned text boxes, not rows and columns. Extract a table and the labels and the
numbers come out as separate runs of text with nothing linking them. The Irish food pyramid's
calorie table became:

```text
Active Child Teenager Adult Adult Inactive Teenager Adult Adult …
Active 2000kcal Inactive 1800kcal Active 2500kcal Inactive 2000kcal
```

Four labels, four values, no way to pair them. On the page the labels sit at y=265/356 and the
values at y=529 — the relationship was only ever column alignment.

**This is not §5b again.** That defect was *recoverable*: the right order was sitting in the
coordinates and only needed sorting. Here the information was never in the text. Reconstructing the
grid means inferring which header owns which cell, and half-right is worse than nothing — a
confidently cited calorie figure attached to the wrong age group.

### Why it had to be acted on

Measured, not assumed: the chunk cleared the relevance floor on **every** calorie question tried and
ranked **1st** for *"calories for an inactive adult over 51?"* (0.676). Phase 1's scope guard blocks
none of those questions. The path from an ordinary question to a wrong number under a
legitimate-looking citation was open end to end. That is `inconsistent_number`, one of the five
named failure modes this milestone exists to prevent.

### The fix: a reviewed list, not a rule

`corpus.yaml` → `quarantine`, three passages, each with its reason. Applied in
`chunk._quarantine_reason`. `verify_quarantine_rules` **raises** if a rule stops matching, because a
stale rule means the passage is silently back in the index while the manifest claims otherwise.

A manifest list rather than a detector because three text statistics were measured across all 105
chunks — repetition ratio, prose density, function-word density — and **none separates a destroyed
table from ordinary bulleted guidance**. Anything aggressive enough to catch these would also drop
real advice. So the judgement sits in data, reviewable and diffable, not hidden in a threshold.

**The test for exclusion is not "is this untidy".** It is: *does this passage pair a number with a
label it may not belong to?*

### Three things learned doing it

1. **Excluding half a table made it worse.** With only the label row quarantined, the orphaned
   values re-chunked under the heading *"Average daily calorie needs for all foods and drinks for
   adults"* — which reads as authoritative — and scored **higher** than before, 0.719. Both halves
   had to go.
2. **A gutted table is safer than a scrambled one.** The ICMR "My Plate" grams table was reviewed
   and **kept**: its values did not survive extraction at all, so there is no number to
   misattribute. A model asked for grams finds none and declines — the safe failure.
3. **Removing a figure does not remove the topic.** Questions about the excluded figures now land
   on neighbouring chunks from the same pages, scoring 0.66–0.68. This narrowed the floor's
   separation band from 0.07 to **0.02** wide. The corpus can no longer state a weekly alcohol
   limit at all, and that is the intended outcome — a refusal beats a 50/50 chance of giving a
   woman a man's limit.

### Consequence for the design

**Gate 2 is now load-bearing, not a backstop.** The floor was comfortable at a 0.07 band; at 0.02 it
is one awkward question from failing either way. Architecture §7.3 always made the model's
`answers_question` the stronger gate — it is now carrying the weight it was designed for.

## 6. Other defects found by running it — all fixed

| Defect | Why it mattered |
|---|---|
| Truncated PDF downloads (45s timeout) | **A truncated PDF opens silently.** Two documents parsed as 5 and 4 pages instead of 7 and 12. Fixed: 240s timeout + `Content-Length` assertion |
| `Content-Length` vs decompressed bytes | Server gzipped, httpx decompressed, check saw *more* bytes than declared and aborted. Fixed: `Accept-Encoding: identity`, skip check if encoding survives |
| Heading detection severing sentences | 37/143 chunks under 50 tokens; body text classified as headings. Fixed: size ratio 1.25, bold rule, and a continuation test. FSANZ went 54 → 13 headings |
| 12 sentences severed across chunk boundaries | PyMuPDF emits separate blocks when a paragraph crosses a column/page break. Fixed by `_join_continuations()`; 12 → 0 |
| Page footers surviving as chunks | A footer ending in its page number is unique per page, so repeat-detection missed it. Fixed by normalising digits before comparing |
| `--use-cache` dropped `page_last_updated` | Wrote `None` for the WHO page, whose `year_source` *is* `page_last_updated` |
| **Verification ran against unpinned versions** | Every Phase 2.1–2.3 run used a throwaway scratch venv resolving `pgvector` 0.5.0 / `trafilatura` 2.3.0 / `pymupdf` 1.28.2 against pins of 0.4.1 / 2.0.0 / 1.26.6. Fixed 2026-10-05: `requirements.txt` installed into `backend/.venv`, everything re-verified |
| `template1` collation mismatch blocked `CREATE DATABASE` | Left over from the `postgres:16` → `pgvector/pgvector:pg16` swap. Fixed with `ALTER DATABASE template1 REFRESH COLLATION VERSION` |

## 7. Known limitations — recorded, not solved

- ~~The 400/64 chunking parameters are reasoned, not measured.~~ **Measured in Phase 2.4:**
  recall@8 = 0.975. See [retrieval-calibration.md](../retrieval-calibration.md).
- ~~"How many portions of fruit and vegetables a day?" returns DGA § *Gut Health*.~~ **This was never
  a retrieval weakness.** Retrieval had the right chunk; the *heading* was wrong, one section behind,
  because of the PDF block-order bug in §5b. Filed against the wrong component for a whole phase.
- **The Irish food pyramid fragments badly**: 19 chunks, median ~77 tokens. It is a poster, not prose.
- ~~One 3-token chunk.~~ **Resolved** by the same block-order fix; the smallest chunk is now 40 tokens.
- **q02 is the one labelled chunk retrieval misses.** FSANZ `:6` states the fridge temperature in
  statutory phrasing with none of the question's vocabulary, so nine plainer chunks outrank it. Not
  harmful — `:8` says the same thing and ranks first.
- **No verified machine-readable table anywhere in the corpus.** An earlier claim that FSANZ had
  dense time/temperature tables was wrong — `find_tables()` found 2, both bulleted prose misread as
  grids. The FSANZ swap was still right, but for the 2-hour/4-hour guide and the 5–60 °C danger zone,
  not tables.
- **Cover-page headings are title fragments.**
- **Phase 1 legacy-row compatibility is only vacuously verified** — the local `claims` table is empty.
- The folder is still `rag-sourced-claims`; `guidance-retrieval` was suggested and never actioned.

## 8. Retrieval, as measured in Phase 2.4

Full record: [retrieval-calibration.md](../retrieval-calibration.md). Labelled set:
`eval/retrieval_set.json` (20 in-corpus + 8 out-of-corpus). Re-run with
`backend/.venv/bin/python eval/run_retrieval_eval.py` — no Groq credits.

| k | 1 | 3 | 5 | **8** | 10 |
|---|---|---|---|---|---|
| recall | 0.575 | 0.825 | 0.917 | **0.975** | 0.975 |

**`floor = 0.69`, `k = 8`.** In-corpus top-1 spans 0.709–0.873; out-of-corpus 0.440–0.680. Only
0.68–0.70 separates the set — a band just **0.02 wide**, narrowed from 0.07 by the §5c quarantine.

0.69 is the midpoint, giving 0.010 below and 0.019 above. Not 0.68 — the usual rule (gate 2 is
stronger, so fail toward a wasted model call rather than a wrong refusal) argues for the bottom of
the band, but its margin over the worst negative is 0.0004: a coincidence, not a margin.

**Gate 2 is now load-bearing.** At a 0.02 band the floor is one awkward question from failing
either way. q27 and q28 are in the set to keep that visible.

Latency: median 8.7 ms. This is what retires the ANN-index question for now.

**Labels are keyed by `slug:ordinal`, which shifts when a document is re-chunked.** This bit once:
the §5c quarantine renumbered every later Irish chunk and six labels silently pointed at the wrong
text, dropping recall 0.975 → 0.800 in a way that looked exactly like a real regression.
`run_retrieval_eval.py` now refuses to run if a labelled key no longer exists.

**What it does not establish:** the labels are unreviewed, 28 questions is a calibration not a
guarantee, and nothing here tests *answering* — that is Phase 2.5, and this phase exists so that
failure can be attributed cleanly when it happens.

## 9. Working agreements

- **Do not implement before approval.** Define the problem and the fix, then wait.
- **One phase at a time, in order.**
- **No production credentials in the transcript.** The Railway `DATABASE_PUBLIC_URL` was never
  requested; the user ran the pgvector verification in the Railway console themselves. Keep this.
- **Write evidence down as you go** — `ingestion-report.md` is the running record, one section per
  phase. This file exists because Phase 2.3's evidence was the one thing that *wasn't* written down.

## 10. Where things are

```text
docs/features/rag-sourced-claims/
  problemStatement.md       the brief, merged and annotated
  architecture.md           16 sections; §1 principle, §5 parsing, §6 data model, §7 retrieval
  implementation-plan.md    phases 2.0 – 2.10 with exit criteria
  ingestion-report.md       evidence: §1–6 = 2.1, §7 = 2.2, §9 = 2.3 (§9.5–9.6 = the block-order fix)
  retrieval-calibration.md  evidence: 2.4 — recall@k, the floor sweep, the quarantine, why 0.69
  temp/context-handoff.md   this file

backend/corpus/
  corpus.yaml  fetch.py  parse.py  chunk.py  ingest.py  snapshot.py
  ids.py  seed.py  show.py          corpus_snapshot.jsonl.gz  ← the artifact of record
backend/services/embeddings.py   retriever.py
backend/routers/corpus.py
backend/alembic/versions/b1c7e4a92f08_rag_corpus_tables.py
eval/retrieval_set.json   run_retrieval_eval.py
```

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-ingest.txt   # runtime + parsers; needed to re-ingest
python -m corpus.show                    # inspect chunks
python -m corpus.ingest --use-cache --snapshot corpus/corpus_snapshot.jsonl.gz
python -m corpus.seed                    # idempotent
python -m alembic current                # expect b1c7e4a92f08 (head)
pytest -q                                # 68 passing

cd .. && backend/.venv/bin/python eval/run_retrieval_eval.py   # recall@k + floor sweep
```

## 11. Next: Phase 2.5 — the answer layer

Retrieval is calibrated, so any wrong answer from here is attributable to generation rather than to
retrieval. That was the whole point of doing 2.4 first.

- **One model call per document**, never one call with all chunks — this is what makes blending two
  publishers' guidance *unrepresentable* rather than merely discouraged (architecture §7.2).
- `DocumentAnswer.answers_question` is a schema-forced field, so "this document has nothing to say"
  is a first-class output rather than something inferred from a score. It is **gate 2**, and
  deliberately the stronger of the two gates.
- **Citation validation is mechanical.** Every `CitedClaim.source.chunk_id` must resolve to a chunk
  that was actually retrieved for that turn; an unresolvable citation is a failed response, not a
  warning.
- The response contract changes — `claims[].source` becomes required and non-nullable. Architecture
  §8.1 explains why per-document answering, not citation, is what forces it.

**Outstanding from 2.4:** the eval labels are one person's judgement and should be reviewed. Every
number in [retrieval-calibration.md](../retrieval-calibration.md) rests on them.
