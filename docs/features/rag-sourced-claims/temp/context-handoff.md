# Context handoff — Phase 2 (RAG over official dietary guidance)

**Purpose.** Paste this at the start of a new conversation. It carries the *decisions and their
reasons* — the part that lives in conversation rather than in code. Everything else is on disk and
can be read directly.

**Status as of 2026-10-06 20:40:** Phases 2.0 – 2.8 complete. **2.9 code-complete, deploy in
progress** — Railway is pointed at `phase-2-rag-corpus`, the first build failed on a GitHub outage
(not our code), and `railway.json` has still never produced a successful build. 189 backend tests
pass, frontend builds clean. **Backend and frontend are on different versions right now — see §16
before testing.** Next: finish the §16 runbook, then 2.10, which needs the deployed URL.

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

### The root cause is not the PDF — verified 2026-10-05

Easy to misread this as "PDFs are bad at tables". It is narrower than that, and the difference
decides what a real fix looks like. A **perfect HTML table** — explicit `<tr>`/`<td>`, nothing lost
— was pushed through our own pipeline:

```text
<tr><th>Group</th><th>Active</th><th>Inactive</th></tr>
<tr><td>Teenager</td><td>2500 kcal</td><td>1800 kcal</td></tr>

  ->  "Calorie needs Group Active Inactive Teenager 2500 kcal 1800 kcal Adult 2000 kcal 2000 kcal"
```

**Identical failure, from a source where nothing was ever lost.** The cause is that the pipeline
renders everything to plain text, and a chunk is a string: the grid dies at that step whatever the
source format was. The PDF does not *cause* the problem — it makes it *unrecoverable*.

| | structure present in source? | recoverable? |
|---|---|---|
| PDF table | No — only coordinates | Only by inference; risky |
| HTML table | **Yes** — explicit tags | **Yes** — we currently discard it |

So: **swapping the Irish PDF for another PDF changes nothing. Swapping it for an HTML version
changes nothing either, today** — we would flatten it anyway — though it would at least become
fixable.

A real fix means representing a table as something other than a flat string: one row per line
(`"Teenager: Active 2500 kcal, Inactive 1800 kcal"`), or structured data carried through to the
chunk. That works for HTML immediately and for PDFs only where the grid can be rebuilt from
coordinates. **Not attempted** — see the quarantine below, which is the containment, not the cure.

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
| recall | 0.633 | 0.825 | 0.917 | **0.975** | 0.975 |

**`floor = 0.69`, `k = 8`.** In-corpus top-1 spans 0.709–0.873; out-of-corpus 0.440–0.680. Only
0.68–0.70 separates the set — a band just **0.02 wide**, narrowed from 0.07 by the §5c quarantine.

0.69 is the midpoint, giving 0.010 below and 0.019 above. Not 0.68 — the usual rule (gate 2 is
stronger, so fail toward a wasted model call rather than a wrong refusal) argues for the bottom of
the band, but its margin over the worst negative is 0.0004: a coincidence, not a margin.

**Gate 2 is now load-bearing.** At a 0.02 band the floor is one awkward question from failing
either way. q27 and q28 are in the set to keep that visible.

Latency: median 7.8 ms. This is what retires the ANN-index question for now.

**The labels were reviewed on 2026-10-05 and four of 44 were wrong** — each had matched on topic
rather than on answering the question: a chunk describing the *problem* with oil disposal, one
ending "Servings a day" with the number lost in a graphic, one saying sodium raises blood pressure
without a quantity, and one giving a reheating *method* where the question asked a temperature. All
four removed; recall@8 was unchanged at 0.975 and the floor did not move, so the corrections cost
nothing but made the number honest. One borderline case (q17) was kept because its answer lives in
the section heading, which *is* part of the chunk — embedded by `embedding_input()` and shown in the
citation. Still one person's judgement, and that person wrote the retriever too.

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
  answer-layer.md           evidence: 2.5 + 2.6 — the isolation guarantee, the endpoint, the guards
  prompt-inversion-regression.md  evidence: 2.8 — the v1->v2 rewrite, 12 explained flips
  frontend.md               evidence: 2.7 — per-document blocks, four states, what wasn't eyeballed
  deployment.md             evidence + RUNBOOK: 2.9 — §4 is what you must run by hand
  temp/context-handoff.md   this file

backend/corpus/
  corpus.yaml  fetch.py  parse.py  chunk.py  ingest.py  snapshot.py
  ids.py  seed.py  show.py          corpus_snapshot.jsonl.gz  ← the artifact of record
backend/services/
  embeddings.py  retriever.py                      2.2 / 2.4
  answer_synthesiser.py  citation_validator.py     2.5 — per-document loop, the §8.3 rules
  corpus_catalog.py                                2.5 — "what was searched"
backend/prompts/document_answer_prompt.md          2.5 — rendered once per document
backend/scripts/smoke_test_answer_layer.py         2.5 — live run, costs credits
backend/routers/chat.py  corpus.py                  2.6 — three wire types, document_filter
backend/alembic/versions/
  b1c7e4a92f08_rag_corpus_tables.py                2.3
  c3d9a51e7b42_message_ordinal.py                  2.6 — explicit turn order
eval/retrieval_set.json   run_retrieval_eval.py        2.4
eval/run_regression.py     runs/                        Phase 1 path — the baseline, do not rewire
eval/run_rag_regression.py rag_runs/                    2.8 — the retrieval path

frontend/lib/api.ts                                2.7 — the wire contract, 3 response types
frontend/app/components/
  ChatWindow.tsx        owns the citation selection; renders BOTH columns as a fragment
  MessageList.tsx       one block per document, numbered markers
  SourcesPanel.tsx      selected passages, or the corpus when idle
  NotInCorpusNotice.tsx the coverage refusal (new)
  history.ts            reload grouping, extracted so it can be run without a test runner
```

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-ingest.txt   # runtime + parsers; needed to re-ingest
python -m corpus.show                    # inspect chunks
python -m corpus.ingest --use-cache --snapshot corpus/corpus_snapshot.jsonl.gz
python -m corpus.seed                    # idempotent
python -m alembic current                # expect b1c7e4a92f08 (head)
pytest -q                                # 189 passing

cd .. && backend/.venv/bin/python eval/run_retrieval_eval.py   # recall@k + floor sweep
```

## 11. Phase 2.5, as built

Full record: [answer-layer.md §1–§8](../answer-layer.md). Services only — 2.5's deliverable list is
four service files, and `routers/chat.py` was 2.6's, so `/chat` kept returning the Phase 1 contract
until 2.6 switched it over.

New: `prompts/document_answer_prompt.md`, `model_client.answer_from_document()`,
`services/answer_synthesiser.py`, `services/citation_validator.py`, `services/corpus_catalog.py`,
and the §8.2 contract in `db/schemas.py`. Phase 1's `ChatAnswerResponse` was renamed
`ChatAnswerResponseV1`, which is still what the eval harnesses describe their results in.

**The guarantee lives in the prompts, not the responses.** A single call holding three publishers'
chunks would return a fluent, well-formed, fully cited answer and look fine — so the test fake
records every prompt and asserts that exactly one document's chunk ids appear in each. Re-checked
against the real corpus too. Nothing in this module may ever put two documents in one prompt.

**Measured, free (no generation call):** fan-out is 2–3 documents per question; the largest rendered
prompt is ~2,810 tokens against a 16,000 budget; the out-of-corpus question gets zero hits and makes
no model call at all.

**The 0.02 band is already visible.** Both cooking-oil questions put the Irish food pyramid barely
over the floor (0.699 / 0.711) on a document with nothing to say about reusing oil. Gate 2 should
decline it. Exactly what 2.4 predicted would start mattering.

**A third defect of the same shape — found by reading a rendered prompt, not by running tests.**
The prompt file's maintainer comment *named* the `{{DOCUMENT}}` and `{{PASSAGES}}` placeholders, and
`str.replace` is global, so the passages were substituted into the comment as well as their intended
position. Every prompt said everything twice, 58% over size. Nothing failed; no placeholder was left
over, so the test checking for leftovers passed. Fixed twice over: HTML comments are now stripped
before sending, and `_assert_single_placeholder` raises at import on any count but exactly one.
**Phases 2.1, 2.4 and 2.5 have each produced one defect that was invisible in the code and visible
in the data.**

**Deliberately not done, and why:** no live Groq call. Everything is structural (recording fake) or
measured on retrieval. `scripts/smoke_test_answer_layer.py` does the live run in one command when the
credits are worth spending; 2.10 measures it properly. Unverified until then: whether the model
copies a 36-char UUID back correctly, whether it uses `answers_question` honestly, and over-refusal
on borderline hits.

## 12. Phase 2.6, as built

Full record: [answer-layer.md §9–§12](../answer-layer.md). `POST /chat` now runs the whole
architecture §10 sequence and returns three distinct wire types. The three `[~]` criteria 2.5 left
are closed. 168 tests pass.

**A turn is now 1 user message + one assistant message per document, in a single commit.** Chosen
over concatenating the prose (loses the grouping — §7.2 blending at the persistence layer) and over
a `document_answers` JSONB column (puts publisher/year/url back in a blob, the drift §6.1 ruled
out). Which document a message answers from is **derived** from its claims, not stored — and it is
always derivable because citation-validator rule 2 forbids an answer with no claims. Rule 2 buys
the read model as well as the grounding guarantee.

**A fourth defect of the same family, caught before shipping.** `Message.created_at` is
`server_default=func.now()`, and Postgres `now()` is the *transaction* timestamp — so a
single-commit turn gives **every** row the same `created_at`, the user's message included. The
relationship ordered by it. On reload a multi-document turn would have come back in arbitrary
order, including which message was the question. Nothing would have failed; it would have surfaced
months later as "history sometimes renders out of order". Fixed by migration `c3d9a51e7b42`:
`messages.ordinal` + `UNIQUE (conversation_id, ordinal)`, same pattern as `chunks`. The test
asserts the *premise* (all timestamps equal) as well as the behaviour, so it cannot start passing
for a new reason.

**The backfill was made non-vacuous.** The local `messages` table is empty, so six Phase-1-shaped
rows were inserted across two conversations out of chronological order, the migration rolled back
and re-applied, and the ordinals read back: correct, `created_at`-ordered, partitioned per
conversation. (Contrast 2.3's legacy-row criterion, still only vacuously verified.)

**Personalisation: the discriminator is second person, not the quantity.** The obvious rule
— prescriptive cue + a number — would refuse the corpus's own phrasing, since population guidance
is itself written prescriptively ("adults should consume no more than 10%"). Intake units only;
times and temperatures excluded, or most of FSANZ becomes a refusal. `check_response()`
deliberately did **not** gain the rule: it is now used only by the eval harnesses, which are the
baseline 2.8 reads its flips against.

**Verified end to end with real retrieval, no Groq spend:** free-sugars → 3 documents, oil → 2,
creatine → `not_in_corpus` with 0 model calls, calorie question → `refused` with 0 model calls.
Reload gave contiguous ordinals across two turns with every block's document and every claim's
citation intact, and the WHO block correctly reported `page_from=0` (not paginated) rather than
page one.

## 13. Phase 2.8, as built — and the first live evidence

Full record: [prompt-inversion-regression.md](../prompt-inversion-regression.md). Done out of order,
before 2.7, because the prompt contradiction was the only known-broken thing in the system and
because this run is the first time anything touched the real model.

**The rewrite is the principle, not the opposite rule.** "Always name your source" would be wrong on
the Phase 1 path, which has none. v2 says *cite what you were given, and attribute to nothing else*
— true whether or not passages are supplied, which matters because one prompt serves both paths.
Measured: 0 of 20 uncited-path responses name a source, exactly as under v1.

**Two runs, not one**, so a flip has one candidate cause: Phase 1 path on the new prompt (isolates
the prompt edit — **0 flips, 0 mismatches**), then the RAG path on the same prompt (isolates
retrieval — **0 mismatches**, 12 path flips). `run_regression.py` was *not* rewired; it is the
baseline. `eval/run_rag_regression.py` is a second harness.

**All 12 flips are `answer → not_in_corpus`, and they split in a way that matters:** 10 at gate 1
(topic genuinely absent; all below the floor, r14 lowest at 0.594) and **2 at gate 2** — r5 "ground
beef internal temperature" cleared the floor at **0.743** on FSANZ's temperature language and the
model then said it does not answer that. Three more documents declined inside answered questions.
**Gate 2 is real**, which is what [retrieval-calibration.md](../retrieval-calibration.md) said it
would have to be once the band narrowed to 0.02.

**The three unknowns from answer-layer §8 are now answered.** UUID transcription: 14/14 claims
resolved, 0 validation failures — the `chunk_key` fallback is not needed. `answers_question`: used
honestly, more than expected. Over-refusal: none observed.

**r1 produced the disagreement case unprompted.** WHO says protein 10–15% of energy (≈50–75 g/day);
the DGA says 1.2–1.6 g/kg body weight. Both verbatim-faithful to their cited chunks, presented
separately, no winner picked. First real-model evidence for a criterion the tests could only prove
structurally possible.

**Three defects found by running it.** (1) An unretried Groq 503 killed the first run 11 questions
in — both harnesses now retry 503/timeout/connection errors. (2) `over_refusal`: the DGA's own *"If
you have a chronic disease, talk with your health care professional…"* tripped the `medical_advice`
guard — a referral read as advice. 1 of 159 second-person corpus sentences; fixed with lookbehinds
for if/whether/when/should. (3) Fixing that exposed a pre-existing `missed_scope_restriction`:
*"Based on that, you have diabetes"* was **not** blocked, because `[a-z\s]+` made the filler before
the condition mandatory. `+` → `*`. Both runs were replayed through the amended guard — **0 outcomes
changed**, so neither needed re-paying for.

## 14. Phase 2.7, as built

Full record: [frontend.md](../frontend.md). Zero new npm dependencies; `package.json` and
`package-lock.json` untouched. Seven files changed, three added — more than the plan's "four and
one", and §1 of that doc says why.

**The panel had to move.** It shows the passages behind the *selected* answer, which is state shared
with the message list and has to sit next to `messages` — but `page.tsx` rendered it as a sibling of
`ChatWindow`. `ChatWindow` now returns a fragment holding the chat column *and* the panel, so the
flex layout still gets two children, `page.module.css` is untouched and `page.tsx` stays a server
component. The cost: a component named `ChatWindow` renders an `<aside>`. Commented at both ends.

**Citation markers sit on the claims, not mid-sentence.** The backend gives `claims[]` as standalone
statements, not offsets into the prose, so a superscript inside a sentence would be a guess about
which clause it belongs to. Numbering runs across the whole turn, so `[3]` means the same passage in
the message and in the panel.

**The four states differ by border style and badge text as well as hue** — the two centred notices
(policy vs coverage refusal) must stay distinguishable in monochrome and with a red-green deficiency,
and they mean opposite things.

**`year: null` renders "year not stated"; `page_from: 0` renders no page at all.** Both are
truthfulness details, not formatting: a blank year is something a reader fills in themselves, and the
WHO HTML source has no pages.

**What was verified, and what wasn't.** No browser automation in that session, so: the live JSON was
checked field-by-field against `lib/api.ts` (a mismatched key renders `undefined` and looks like a
styling bug, so this was done mechanically); the reload grouping was extracted to `history.ts` and run
against the real `GET /conversations/{id}` payload, confirming 3 rows fold into one two-document turn
with every citation intact and a Phase 1 uncited row dropped rather than rendered. **Nothing was
looked at** — layout, dark mode, hit targets and the panel's scrolling are reasoned from CSS only.
frontend.md §4.2 has the commands to look.

## 15. Phase 2.9, as built — code done; deploy in progress

Full record and runbook: [deployment.md](../deployment.md). **§4 is the part that needs your
credentials**; the working agreement that no production credential enters a transcript is why the
phase splits here, same as the 2.0 pgvector check.

New: `scripts/prefetch_embedding_model.py` (bakes the 64 MiB of ONNX at build time),
`backend/railway.json` (a `buildCommand` that runs it), a FastAPI lifespan that loads the model
at startup, `RETRIEVAL_K`/`RETRIEVAL_FLOOR` env vars defaulting to the measured values, and
`tests/test_startup.py` (9 tests). The requirements split already existed — what was missing was any
check that it holds.

**`services.embeddings.CACHE_PATH` is one constant for both the build step and the runtime**, and the
default *is* the baked location rather than something `FASTEMBED_CACHE_PATH` must be set to. If the
two could disagree, nothing would error — fastembed would re-download 64 MiB at boot and the baking
would be silently pointless.

**Measured locally:** 1.37 s from launch to a served `/health`; model load 0.32 s; **351 MB RSS for
the whole process** idle after one request. The plan's 279 MB was the *model alone* — 351 MB is the
number to size a tier against, and on 512 MB that is 70% of the ceiling before any concurrency.

**The split was tested, not assumed:** a clean venv with only `requirements.txt` (56 packages, no
pymupdf/pdfminer/trafilatura) imports the app, runs the lifespan and serves `/health`. `corpus.seed`
also imports there, which is what makes "run the seed inside the deployed container" real. PyYAML
turns out to arrive at runtime anyway via fastembed -> huggingface_hub.

**The riskiest part of this deploy is silent, and it already bit.** The build config shipped as
`nixpacks.toml`; **Railway builds with Railpack**, which does not read it, and Nixpacks is no longer
a selectable builder. The baking step would never have run — and a skipped build step **does not
fail the deploy**, it moves the download back to boot where it costs nothing until Hugging Face is
slow. The test asserting the build config *passed*, because it checked a file's contents without
checking that the platform reads that file. Now `backend/railway.json`, with the builder pinned in
the same assertion as the command. Read the build log for the prefetch output.

**A 2.8 defect found and fixed here:** `eval/runs/` was gitignored, so the Phase 1 regression runs —
one of the two files prompt-inversion-regression.md cites as evidence — could never be committed.
`eval/rag_runs/` and `eval/failure_log_runs/` were already tracked. Now consistent.

**Two reporting bugs in my own tooling, both caught by cross-checking:** the prefetch script first
reported 200.8 MB because the HuggingFace cache hardlinks each blob into `snapshots/` and I summed
`st_size` (actual: 64 MiB, cross-checked with `du`); and in 2.8 an attribution detector matched the
pronoun "who" as the organization. Neither failed; both printed a plausible wrong number.

## 16. The deploy, as of 2026-10-06 20:40 — state and gotchas

Two commits pushed: `5b3e97c` (phases 2.5–2.9) and `2a1e49a` (the Railpack fix).
`origin/phase-2-rag-corpus` == local HEAD.

### Railway, current settings

| Setting | Value |
|---|---|
Branch connected to production | **`phase-2-rag-corpus`** — *not* main |
Root Directory | `backend` |
Auto deploy | was "unavailable", now "disabled" with an Enable toggle |
Builder | **Railpack** v0.40.1 |
Credit remaining | **$4.43 / 19 days** — budget the verification into one session |

### The first deploy failed, and it was not our code

`ERRO failed to ensure mise is installed` — Railpack could not download its own bootstrap
tool because **GitHub returned 500**. Died at 17 s, before our repo mattered. The same outage
produced "Could not load branches" (so Railway showed `main` because that was the *stored*
value, not an enumerated option) and "Auto deploy unavailable". **One cause, three symptoms**,
all cleared when GitHub recovered. Retry is the whole fix.

### Still unverified, and it is the one that matters

`backend/railway.json` has **never produced a successful build**. Watch the build log for:

```text
prefetching BAAI/bge-small-en-v1.5 into .../.fastembed_cache
ok: 384 dimensions, 17 unique file(s), 67 MB on disk
```

Absent → Railpack did not pick up the config. Fallbacks in order: set **Config-as-code** to
`/backend/railway.json` (worth setting proactively — Railway's docs say config files need an
absolute path when a root directory is set, and ours is `backend`), then Settings → Build →
Build Command.

### ⚠️ Frontend and backend are on different versions right now

Railway → `phase-2-rag-corpus` (new backend, returns `document_answers`).
Vercel production → `main` (Phase 1 frontend, expects a flat `answer` string).

**They are incompatible.** The production Vercel URL will not render answers against this
backend. To test the full flow before merging, either use a Vercel preview deploy of the
branch, or run the frontend locally — and in **both** cases add the origin to Railway's
`ALLOWED_ORIGINS`, which is currently set to the production Vercel URL only. CORS is the
thing that will waste half an hour otherwise.

The backend alone is testable immediately with `curl` — `/health`, `/corpus`, and all three
`/chat` response types.

### Remember to switch back

After verification: Railway → Branch → `main`, then merge `phase-2-rag-corpus`. Leaving
production pinned to a feature branch is invisible until someone pushes to main and nothing
happens.

## 17. Next: finish the 2.9 runbook, then 2.10

- **2.10** needs the deployed URL — its ten questions run against production, so the runbook
  genuinely blocks it. It also inherits a real finding: the corpus answers only 5 of the 20
  regression questions, so answer quality rests on 5 questions and 14 claims. Its ten fixed
  questions are weighted toward what the corpus covers, and it reruns the numeric ones for
  `inconsistent_number`, which a single pass cannot detect.

**Known, deferred, written down:** a coverage refusal cannot be traced to its conversation,
because `retrievals` reaches one only through `message_id`, which is null for those rows;
`/chat` is still unrate-limited and retrieval adds an embedding per request; and **nothing has
yet run against the deployed system**.

**Not outstanding any more:** the 2.4 eval labels *were* reviewed (§8, commit `13a85e1` — four of
44 were wrong, recall@8 unchanged). This file carried a "should be reviewed" note past that commit
for two phases. The live caveat is narrower and unchanged: the labels are still one person's
judgement, and that person wrote the retriever.
