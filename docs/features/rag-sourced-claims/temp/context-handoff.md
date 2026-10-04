# Context handoff — Phase 2 (RAG over official dietary guidance)

**Purpose.** Paste this at the start of a new conversation. It carries the *decisions and their
reasons* — the part that lives in conversation rather than in code. Everything else is on disk and
can be read directly.

**Status as of 2026-10-05:** Phases 2.0 – 2.3 complete and verified. **Phase 2.4 is next.**

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
| **No ANN index** (no IVFFlat/HNSW) | At ~105 chunks an exact cosine scan is single-digit ms. ANN would add tuning surface and *approximate* recall to a solved problem. Revisit above ~50k chunks |
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

Seven documents, 50 PDF pages + 1 web page, 105 chunks. Ceiling of 23 pages per document (started
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

- **The 400/64 chunking parameters are reasoned, not measured.** Phase 2.4 owes recall@k.
- **"How many portions of fruit and vegetables a day?"** returns DGA § *Gut Health* at 0.758 — a
  plausible score for the wrong section. The clearest known retrieval weakness.
- **The Irish food pyramid fragments badly**: 20 chunks, median 77 tokens. It is a poster, not prose.
- **One 3-token chunk** (`usda-hhs-dga-2025:24` = `'January 2026'`). `MIN_CHUNK_TOKENS` merges
  forward but not on a section's final flush.
- **No verified machine-readable table anywhere in the corpus.** An earlier claim that FSANZ had
  dense time/temperature tables was wrong — `find_tables()` found 2, both bulleted prose misread as
  grids. The FSANZ swap was still right, but for the 2-hour/4-hour guide and the 5–60 °C danger zone,
  not tables.
- **Cover-page headings are title fragments.**
- **Phase 1 legacy-row compatibility is only vacuously verified** — the local `claims` table is empty.
- The folder is still `rag-sourced-claims`; `guidance-retrieval` was suggested and never actioned.

## 8. Early floor evidence (four questions — a sanity check, not a calibration)

| Query | Top hit | Score |
|---|---|---|
| cooked food out of the fridge | FSANZ § Temperature control | 0.778 |
| how much free sugar | WHO § Sugars | 0.827 |
| reuse frying oil | FSSAI § Handling and disposal | 0.800 |
| **capital of France** | *(noise)* | **0.440** |

0.78–0.83 in-corpus against 0.44 out suggests a floor near 0.55–0.65. **Phase 2.4 must replace this
with a measured sweep.**

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
  problemStatement.md     the brief, merged and annotated
  architecture.md         16 sections; §1 principle, §5 parsing, §6 data model, §12 deploy
  implementation-plan.md  phases 2.0 – 2.10 with exit criteria
  ingestion-report.md     evidence: §1–6 = 2.1, §7 = 2.2, §9 = 2.3
  temp/context-handoff.md this file

backend/corpus/
  corpus.yaml  fetch.py  parse.py  chunk.py  ingest.py  snapshot.py
  ids.py  seed.py  show.py          corpus_snapshot.jsonl.gz  ← the artifact of record
backend/services/embeddings.py
backend/alembic/versions/b1c7e4a92f08_rag_corpus_tables.py
```

```bash
cd backend && source .venv/bin/activate
python -m corpus.show                  # inspect chunks
python -m corpus.seed                  # idempotent
python -m alembic current              # expect b1c7e4a92f08 (head)
pytest -q                              # 38 passing
```

## 11. Next: Phase 2.4

The phase flagged in the plan as **highest-leverage**, because *the floor is the not-in-corpus
refusal*. Calibrate it against retrieval alone and a later wrong answer is unambiguously the model's
fault; skip it and every downstream failure has two candidate causes.

- `services/retriever.py` — exact cosine, optional `document_id` filter
- `GET /corpus` — name, publisher, year, url, retrieval date, chunk count
- `eval/retrieval_set.json` — hand-labelled relevant chunk ids, **including out-of-corpus questions**
- `eval/run_retrieval_eval.py` — recall@k and floor precision, no generation call
- Sweep `floor` and `k`; record the chosen numbers *and why*

The labelling needs the user's review — that judgement is what every later number rests on.
