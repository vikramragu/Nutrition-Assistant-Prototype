# Implementation Plan — Dietary Guidance RAG Chatbot (Phase 2)

Sequences the work in [problemStatement.md](./problemStatement.md) and [architecture.md](./architecture.md)
into phases. Each produces a working, testable increment and lists exit criteria traced back to
[problemStatement.md §8](./problemStatement.md).

Phases are numbered **2.0–2.10** to avoid collision with the Phase 0–8 of
[../../implementation-plan.md](../../implementation-plan.md), which built the prototype this extends.

**The ordering has one non-obvious property worth stating up front:** the relevance floor is
calibrated (2.4) *before* the answer layer is written (2.5), and the corpus is frozen (2.0) *before
anything is fetched* (2.1). Both reverse the tempting order. The floor **is** the not-in-corpus
refusal, so guessing it and discovering the mistake through bad answers means debugging two things at
once; and re-ingesting after a corpus change invalidates every chunk id, every embedding, and the
labelled retrieval set built on top of them.

---

## Phase 2.0 — Preflight: resolve the blockers

**Goal:** no code written against an assumption that turns out false. [architecture.md §12.3](./architecture.md)
flags three unverified things and [problemStatement.md §11](./problemStatement.md) leaves two corpus
decisions open. All five are cheap now and expensive later.

- **Verify pgvector on Railway.** Run in the Railway Postgres query console:
  `SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';`
  Zero rows means migrating to a pgvector image — a real migration, not a config flag, and it must
  happen before 2.3.
- **Fix local Postgres.** `docker-compose.yml` pins `postgres:16`, which does **not** ship the
  extension. Change to `pgvector/pgvector:pg16` and confirm `CREATE EXTENSION vector;` succeeds
  locally.
- **Verify PyMuPDF against the two damaged PDFs.** Document 3 and the Eatwell candidate have broken
  xref tables; `pypdf` refuses both and `pdfminer.six` recovers both (verified 2026-10-04). PyMuPDF
  is untested on them. If it also fails, the fallback in 2.1 is load-bearing rather than defensive.
- **Decide the corpus** — [problemStatement.md §11.1 and §11.8](./problemStatement.md), together,
  because they interact. Document 7 (FSANZ *Storing food safely*) is business-facing, table-free and
  under 3,000 characters; the natural replacement is the FSANZ temperature-control PDF, which needs
  the ceiling raised to 23 pages. Alternatively, attempt a manual browser download of Health Canada's
  *Safe food storage* page, which is blocked to scripted clients but carries the per-food
  fridge/freezer table.
- **Re-run the access check** on whatever final list emerges, recording retrieval dates for real.

**Exit criteria**
- [ ] pgvector confirmed available on Railway, or a migration path decided.
- [ ] `CREATE EXTENSION vector;` succeeds against local Docker Postgres.
- [ ] PyMuPDF's behaviour on both damaged PDFs is known and recorded.
- [ ] The final 5–7 document list is frozen, with the page ceiling decided and written down.
- [ ] The corpus has at least one strong source for **each** question shape in
      [problemStatement.md §3](./problemStatement.md) — dietary pattern *and* fridge/storage.
- [ ] Every final URL re-verified: HTTP 200, `Content-Length` matched, content confirmed.

---

## Phase 2.1 — Corpus ingestion: fetch, parse, chunk

**Goal:** the frozen corpus turned into chunks that carry full provenance. No database, no embeddings
yet — this phase is pure text processing and is the one most worth eyeballing by hand.

- `backend/corpus/corpus.yaml` — the manifest ([architecture.md §4](./architecture.md)), including
  the `year_source` enum. Three documents state no publication year; an inferred year that can't be
  distinguished from a stated one *is* an invented year.
- `corpus/fetch.py` — browser user-agent, redirects followed, **timeout ≥ 240 s**, assert
  `Content-Length` equals bytes received, assert content type and `%PDF-` magic, record `sha256` and
  UTC retrieval date, capture the HTML "last updated" string verbatim.
- `corpus/parse.py` — PyMuPDF primary, `pdfminer.six` on exception or zero pages, `trafilatura` for
  HTML. Record which parser produced each document. Assert parsed page count equals
  `expected_pages`.
- `corpus/chunk.py` — heading-aware with paragraph fallback, ~512 tokens, ~64 overlap, **never split
  a table or a numbered-recommendation list**. Documents with no headings use their own numbered
  structure.

**Exit criteria**
- [ ] Every document fetches with byte count matching `Content-Length`.
- [ ] Parsed page count matches `expected_pages` for every PDF.
- [ ] HTML boilerplate — nav, cookie banner, footer — is absent from extracted text.
- [ ] Every chunk carries non-null `document_name`, `publisher`, `year` (or an explicit null with
      `year_source`), `section_heading`, `page_from`, `page_to`, `ordinal`.
- [ ] **Manual read-through of at least 20 chunks**, chosen to include every table in the corpus. No
      table is split; no numbered recommendation is severed from its qualifier.
- [ ] Chunk-count and token-length distribution recorded — this is the evidence for the README's
      "what the chunking strategy cost."

> **Why the manual read matters:** table bisection is
> [problemStatement.md §10](./problemStatement.md)'s first named risk and nothing downstream detects
> it. A half-table chunk retrieves confidently and answers wrongly.

---

## Phase 2.2 — Embeddings and the committed snapshot

**Goal:** chunks become vectors, pinned in a reviewable artifact.

- `services/embeddings.py` — `EmbeddingClient` protocol with **two methods**, `embed_passages()` and
  `embed_query()`. Not one method with a flag: [architecture.md §5.5](./architecture.md)'s asymmetry
  must be impossible to forget at a call site.
- `fastembed` implementation for `BAAI/bge-small-en-v1.5`. Passages bare; queries prefixed
  `"Represent this sentence for searching relevant passages: "`.
- Embed `f"{document_name} — {section_heading}\n\n{text}"`, not bare chunk text.
- Write `corpus/corpus_snapshot.jsonl.gz` with an **embedding model id in the header**, and commit it.

**Exit criteria**
- [ ] Vectors are 384-dimensional.
- [ ] A unit test asserts `embed_query()` applies the prefix and `embed_passages()` does not, by
      comparing the two encodings of identical text.
- [ ] The snapshot carries the model id; loading refuses a mismatch.
- [ ] Snapshot committed; regenerating it from the same corpus is byte-stable or the difference is
      explained.

---

## Phase 2.3 — Data model and seeding

**Goal:** durable storage for the corpus and the evidence trail, per [architecture.md §6](./architecture.md).

- One Alembic revision: `CREATE EXTENSION IF NOT EXISTS vector`; create `documents`, `chunks`
  (`vector(384)`), `retrievals`, `retrieval_hits`; add `claims.chunk_id` FK.
- SQLAlchemy models, including the `pgvector` `Vector` column type.
- `corpus/seed.py` — idempotent, skipping rows whose `content_sha256` already matches, refusing a
  snapshot whose embedding model id doesn't match configuration.

**Exit criteria** — all verified, evidence in [ingestion-report.md §9.2](./ingestion-report.md)
- [x] Migration applies cleanly to an empty database and to the existing Phase 1 database.
- [x] Seed is idempotent: running twice produces identical row counts.
- [x] Deleting a document cascades to its chunks; a `claims.chunk_id` cannot reference a
      non-existent chunk.
- [~] Phase 1 rows still read correctly — `claims.chunk_id` null, legacy `source` null.
      *Vacuous locally: the `claims` table has 0 rows. Untested against real legacy data.*
- [x] No ingestion step was added to the `Procfile`.

---

## Phase 2.4 — Retrieval, and calibrating the floor

**Goal:** both retrieval modes working, **and the relevance floor chosen with evidence rather than
guessed.** This resolves [problemStatement.md §11.2](./problemStatement.md).

- `services/retriever.py` — cosine over `chunks.embedding`, exact scan, no ANN index, optional
  `document_id` filter.
- `GET /corpus` — name, publisher, year, url, retrieval date, chunk count.
- `eval/retrieval_set.json` — questions with hand-labelled relevant chunk ids, including questions the
  corpus genuinely **cannot** answer.
- `eval/run_retrieval_eval.py` — recall@k and floor precision, no generation call.
- Sweep `floor` and `k`; pick values from the curve and record why.

**Exit criteria** — all met, evidence in [retrieval-calibration.md](./retrieval-calibration.md)
- [x] Retrieval across all documents works; retrieval filtered to one named document works.
- [x] Labelled retrieval set exists, including out-of-corpus questions — 20 in-corpus, 8 out.
- [x] recall@k reported for at least three values of `k` — five: 0.500 / 0.825 / 0.917 / 0.975 / 0.975
      at k = 1 / 3 / 5 / 8 / 10.
- [x] **`floor` chosen from measured data**, with the number and its justification written down —
      **0.69**, the midpoint of a 0.68–0.70 band. The band is only 0.02 wide after the corpus
      quarantine, so gate 2 is now load-bearing rather than a backstop.
- [x] A question known to be outside the corpus returns zero chunks above the floor — 8/8.
- [x] Query latency measured — median **8.7 ms**.

> **This phase is the single highest-leverage one in the plan.** The floor is the not-in-corpus
> refusal. Calibrate it here, against retrieval alone, and a later wrong answer is unambiguously the
> model's fault. Skip it, and every downstream failure has two candidate causes.

---

## Phase 2.5 — Answer layer and citation validation

**Goal:** grounded, per-document, cited answers — with grounding enforced in code.

- `prompts/document_answer_prompt.md` — the per-document generation prompt. It receives one
  document's chunks with their ids and must answer **only** from them.
- `model_client.answer_from_document()` — structured output `{answers_question, answer, claims[]}`
  where each claim is `{claim, chunk_id}`. The model emits **only a chunk id**, never a publisher,
  year or URL; the backend expands it by join, so a citation cannot be fabricated.
- `services/answer_synthesiser.py` — group hits by document, one call per document, concurrently.
- `services/citation_validator.py` — see below.

**Exit criteria** — evidence in [answer-layer.md](./answer-layer.md)
- [x] A question answerable by one document returns one `DocumentAnswerOut` with cited claims.
- [x] A cross-document question (cooking oil) returns **separate** answers with separate citations,
      and no claim mixes material from two documents — asserted on the *prompts*, not just the
      results: no generation call ever receives two documents' passages.
- [x] **A mocked model response citing a chunk id not in its context returns HTTP 502** — not a
      dropped claim, not a repaired answer. *Raised as `CitationError` in 2.5 and mapped to 502 by
      the endpoint in 2.6.*
- [x] A mocked response with a non-empty answer and empty `claims` returns HTTP 502.
- [x] When every document returns `answers_question: false`, the result is a not-in-corpus refusal
      naming the **full** corpus, not just the documents that scored above the floor.
- [x] Lexical-overlap warnings are logged and non-blocking — plus a quantity-agreement warning on
      the same footing, the sharpest available signal for `inconsistent_number`.

> **Three of these were `[~]` when 2.5 shipped**, because they are phrased as HTTP outcomes and
> 2.5 owns no route: its deliverable list is four service files, and `routers/chat.py` is 2.6's.
> They were closed by 2.6 and are now asserted at the endpoint — see
> [answer-layer.md §11](./answer-layer.md).

---

## Phase 2.6 — Chat endpoint and scope guard

**Goal:** the full [architecture.md §10](./architecture.md) sequence, with the new personalisation
boundary.

- `scope_guard.py` — new **personalisation** rule set: second-person prescriptive framing co-occurring
  with a retrieved quantity. Pre-model gate still runs **before retrieval**, so out-of-scope questions
  never touch the index.
- `routers/chat.py` — three response types, optional `document_filter`, persistence in a single step
  after all gates pass.
- Coverage refusals persist to `retrievals` with zero `used` hits; policy refusals to
  `scope_refusals` as in Phase 1.

**Exit criteria** — all met, evidence in [answer-layer.md §9–§12](./answer-layer.md)
- [x] Out-of-scope question refuses **before** any embedding or retrieval call — assert via a spy,
      not by reading the code. *`get_searcher` is an injected seam; the spy records zero calls.*
- [x] A guidance chunk containing a calorie figure does not let a calorie-target question through —
      asserted with the searcher primed with a 0.95-scoring hit, which it must still never consult.
- [x] A mocked answer converting population guidance into "you should…" is blocked and never
      persisted. The paired test matters more: the corpus's **own** population phrasing must not be
      blocked, which is why the rule keys on second person rather than on "should + a number".
- [x] The two refusals are distinct wire types; a client can tell them apart without parsing prose.
- [x] No partial writes on any failure path — user message never persisted without its assistant
      message. Tested by *breaking* it: a citation to a nonexistent chunk fails the flush, and the
      user message does not survive.
- [x] `GET /conversations/{id}` returns history with grouped, cited claims.

**One migration, not in the original plan:** `messages.ordinal` with
`UNIQUE (conversation_id, ordinal)`. A turn is now one user message plus one assistant message per
document, written in a single commit — and Postgres `now()` is the transaction timestamp, so every
row of that turn shares a `created_at`. Ordering by it would scramble the turn. See
[answer-layer.md §9](./answer-layer.md).

---

## Phase 2.7 — Frontend

**Goal:** the Phase 1 shell filled in, not rebuilt. Four files changed, one added, **zero new
dependencies**.

- `SourcesPanel.tsx` — shows the chunks behind the selected answer; with nothing selected, shows the
  corpus from `GET /corpus`, so the panel always tells the user what the assistant can see.
- `MessageList.tsx` — **one block per document**, headed by publisher and year. Inline citation
  markers select a source in the panel.
- `NotInCorpusNotice.tsx` — new, visually distinct from `RefusalNotice`, listing what was searched.
- `types.ts`, `lib/api.ts` — new `not_in_corpus` variant; `Claim.source` widens from `null` to
  `Citation`.

**Exit criteria** — all met, evidence in [frontend.md](./frontend.md)
- [x] Two-document answers render as two visibly separate blocks — visual merging would undo
      [architecture.md §7.2](./architecture.md). Separate `<article>`s with their own borders, a
      real 12px gap, and a publisher/year header each.
- [x] Every claim's citation shows document name, publisher, year, and a working link — verified
      against live payloads, including `year: null` rendering as "year not stated" rather than a gap.
- [x] Answer, policy refusal, coverage refusal, and generic error are four distinguishable states —
      differing in alignment, **border style** and badge text as well as hue, so the distinction
      survives a monochrome screenshot and a red-green colour deficiency.
- [x] Sources panel never shows an empty state when an answer is selected — structural: the citation
      validator rejects an answer with no claims, so a selected turn always has a passage.
- [x] Page refresh reloads history with citations intact — the grouping logic was extracted to
      `history.ts` and run against the real `GET /conversations/{id}` payload.
- [x] No new npm dependency added — `package.json` and `package-lock.json` unchanged.

**Seven files changed, three added**, not four and one. The plan's list omitted `ChatWindow.tsx` and
`page.tsx`, which must change for the panel to see the selected answer at all, and `history.ts` is an
extraction made so the reload grouping could be verified without a test runner.

**The appearance criteria are claimed on markup and CSS, not on a screenshot** — no browser
automation was available, so layout, dark mode and hit targets are reasoned rather than seen.
[frontend.md §4.2](./frontend.md) says exactly what was and was not verified, and how to look.

---

## Phase 2.8 — System prompt inversion and regression

**Goal:** flip Phase 1's attribution rule and prove nothing else broke.

- Edit `prompts/system_prompt.md`: remove the rule forbidding named sources — added in Phase 1 to fix
  three `unverifiable_source` findings — and replace it with the citation requirement.
- Extend `eval/regression_questions.json`: at least one question that **should** produce a
  not-in-corpus refusal, one cross-document question, and one that tests population-level framing.
- Run the full regression suite per [../../eval.md §2.3](../../eval.md) and read the diff.

**Exit criteria** — all met, evidence in
[prompt-inversion-regression.md](./prompt-inversion-regression.md)
- [x] Regression set includes not-in-corpus, cross-document, and personalisation cases — r17–r20,
      and the set gained a second expectation field so the Phase 1 baseline does not move.
- [x] Full regression run completed after the prompt edit — **two** runs, deliberately: the Phase 1
      path on the new prompt (isolating the prompt edit) and then the RAG path on the same prompt
      (isolating retrieval). One run would leave every flip with two candidate causes.
- [x] **Every outcome-type flip is explained in writing** — 12 flips, all `answer → not_in_corpus`,
      split by gate: 10 at gate 1 (topic genuinely absent) and **2 at gate 2** (the document had the
      topic and declined anyway, r5 at score 0.743). Gate 2 is not decoration.
- [x] Scope cases r9–r11 still refuse — all three at `pre_model`, identically on both paths.
- [x] Negation and informational-number cases (r12, r14) still do **not** refuse — neither is
      `refused`; both return `not_in_corpus`, which is the coverage type, not the policy one. The
      interpretation is recorded in [§7.1](./prompt-inversion-regression.md) and in the question
      set's own `_about` block.

**The prompt rewrite is not the opposite rule.** "Always name your source" would be wrong on the
Phase 1 path, which has none. v2 states the principle under both versions — *cite what you were
given, and attribute to nothing else* — which is correct whether or not passages are supplied.
Measured: 0 of 20 uncited-path responses name a source, same as under v1.

**Three defects found by running it:** an unretried Groq 503 killed the first run eleven questions
in; the DGA's own *"If you have a chronic disease, talk with your health care professional"* tripped
the `medical_advice` guard (`over_refusal` on a referral, 1 of 159 second-person corpus sentences);
and fixing that exposed a pre-existing `missed_scope_restriction` — "Based on that, you have
diabetes" was **not** blocked, because the pattern made the filler before the condition mandatory.

---

## Phase 2.9 — Deployment

**Goal:** the RAG assistant live at the existing public URL.

- Split `requirements.txt` (runtime: `pgvector`, `fastembed`) from `requirements-ingest.txt`
  (`pymupdf`, `pdfminer.six`, `trafilatura`, `pyyaml`, `httpx`) — the production container never
  parses a PDF.
- **Bake the 64 MB of ONNX weights into the image at build time** and point `FASTEMBED_CACHE_PATH` at
  them. A container that downloads from Hugging Face at boot is a deploy that fails on a slow day.
- Load the embedding model at application startup, not lazily, so the ~0.35 s lands on the health
  check rather than a user.
- Railway variables: `EMBEDDING_MODEL`, `FASTEMBED_CACHE_PATH`, `RETRIEVAL_K`, `RETRIEVAL_FLOOR`.
- Apply the migration, then run `seed.py` manually against production.

**Exit criteria** — code done and locally verified; **four need production access**. Evidence and
runbook in [deployment.md](./deployment.md)
- [ ] Migration applied to Railway Postgres; `seed.py` run; chunk count matches the snapshot.
      *Open — needs the Railway shell. Target: head `c3d9a51e7b42`, 7 documents, 103 chunks.
      `corpus.seed` was verified to import under runtime-only dependencies, so it can be run
      inside the deployed container.*
- [x] No ingestion in the `Procfile`; no network call to any publisher at boot — verified by test
      rather than by reading: the Procfile is checked for `corpus.ingest`/`seed`/`fetch`, and a
      subprocess import of the app is checked for every ingest-only package. A clean venv with only
      `requirements.txt` (56 packages, no `pymupdf`/`pdfminer`/`trafilatura`) runs the app.
- [ ] Memory headroom confirmed on the actual tier (expect ~279 MB RSS for the model alone).
      *Open. Measured locally: **351 MB for the whole process**, idle, after one request — 279 MB
      was the model in isolation, so that is the wrong number to size a tier against.*
- [ ] Cold-start time measured against the deployed URL. *Open. **1.37 s** locally from process
      launch to a served `/health`, with the weights baked.*
- [ ] Full flow verified in production: cited answer, cross-document answer, not-in-corpus refusal,
      policy refusal, page reload. *Open — checklist in [deployment.md §4.4](./deployment.md).*
- [x] No secret in the frontend bundle; `GROQ_API_KEY` still the only provider secret — scanned the
      production build: no `groq`/`gsk_`/`DATABASE_URL`/postgres URL in any asset, and
      `NEXT_PUBLIC_API_BASE_URL` is the only inlined variable. Re-confirm on the deployed bundle,
      which inlines Vercel's value rather than the local one.

**The split is deliberate.** No production credential enters a transcript — the same agreement under
which the Phase 2.0 pgvector check was run by hand in the Railway console. Everything buildable and
measurable without one is done; the rest is a runbook.

**The riskiest part of this deploy is silent, and it already bit once.** The build config shipped as
`nixpacks.toml` — but **Railway builds with Railpack**, which does not read it, and Nixpacks is no
longer a selectable builder. The weight-baking step would never have run, and **a skipped build step
does not fail the deploy**: the service goes green and pays 15 s on cold starts whenever Hugging Face
is slow. The test asserting the build config *passed*, because it checked the contents of a file
without checking that the platform reads it. Now `backend/railway.json`, with the builder pinned in
the same assertion as the command. Found on the first real deploy, 2026-10-06.

**Found while doing this, and fixed:** `eval/runs/` was gitignored, so the Phase 1 regression runs —
including one of the two files [prompt-inversion-regression.md](./prompt-inversion-regression.md)
cites as evidence — could never be committed, making the 2.8 comparison unreproducible from a clone.
`eval/rag_runs/` and `eval/failure_log_runs/` were already tracked. Now consistent.

---

## Phase 2.10 — Failure log and documentation

**Goal:** the durable evaluation artifact and the README the brief requires.

- Extend the failure taxonomy with `uncited_claim`, `blended_sources`, `citation_mismatch`,
  `over_refusal`; `unverifiable_source` **changes meaning** — under Phase 1 any source was a failure,
  now a missing or wrong one is.
- Run the 10 fixed questions from [../../eval.md §3.2](../../eval.md) through the RAG pipeline.
  Several will now refuse as not-in-corpus; that is a finding, not a broken run.
- Review every response against all nine failure types; record findings; group and count.
- Write `failure-log.md` in this folder.
- **README** — chunk size, overlap, embedding model, index type (`none — exact scan`) and `k`, plus
  the chunking strategy and its cost, plus the §8.1 schema change and why.

**Exit criteria**
- [ ] All 10 questions run against the deployed pipeline.
- [ ] Every response reviewed against all nine failure types.
- [ ] Findings grouped and counted in `failure-log.md`.
- [ ] README states all five required parameters, the chunking cost, and the schema change.
- [ ] Each finding resolves to a rule change, a prompt edit re-validated through 2.8, or a documented
      accepted limitation — never silently dropped.
- [ ] Compare against [../../failure-log.md](../../failure-log.md): did retrieval actually fix the
      three `inconsistent_number` and `unsupported_claim` findings it was supposed to fix?

---

## Dependency summary

```
2.0 (preflight — unblocks everything)
 │
 ▼
2.1 (fetch/parse/chunk) → 2.2 (embed/snapshot) → 2.3 (schema/seed) → 2.4 (retrieval + floor)
                                                                           │
                                                                           ▼
                                                                  2.5 (answer + citations)
                                                                           │
                                                      ┌────────────────────┼────────────────────┐
                                                      ▼                    ▼                    ▼
                                              2.6 (endpoint)         2.7 (frontend)      2.8 (prompt+regression)
                                                      │                    │                    │
                                                      └────────────────────┴────────────────────┘
                                                                           ▼
                                                                    2.9 (deployment)
                                                                           ▼
                                                                  2.10 (failure log + README)
```

2.7 can start as soon as the §8.2 response contract is fixed in 2.5 — it needs the shape, not a
working backend. 2.8 can run in parallel with 2.6 and 2.7 once answers are generated. Everything else
is strictly sequential: each phase consumes the previous one's artifact.

---

## Traceability to acceptance criteria

| [problemStatement.md §8](./problemStatement.md) criterion | Phase |
|---|---|
| 5–7 prose documents from recognised authorities | 2.0, 2.1 |
| Publisher, year, source URL, retrieval date stored | 2.1, 2.3 |
| No year invented | 2.1 (`year_source`) |
| Every URL returns 200 and serves the content | 2.0, 2.1 |
| HTML boilerplate stripped; last-updated stored | 2.1 |
| Chunk carries name, publisher, year, section heading | 2.1 |
| README states chunking strategy and cost | 2.1, 2.10 |
| Retrieval across all documents | 2.4 |
| Retrieval filtered to one document | 2.4 |
| Answers only from retrieved chunks | 2.5 |
| Every claim has document, publisher, year, link | 2.3, 2.5 |
| No claim ships without a citation | 2.5 |
| Per-document answers, separate citations | 2.5 |
| Never blended | 2.5 |
| Disagreement shown, no winner picked | 2.5, 2.7 |
| Not-in-corpus refusal names what was searched | 2.4, 2.5 |
| Scope refusal still enforced in code | 2.6 |
| Two refusals distinguishable | 2.6, 2.7 |
| Frontend/backend extended, not rebuilt | 2.7 |
| Sources panel shows chunks | 2.7 |
| `claims[].source` carries a real citation | 2.3, 2.5 |
| Schema change documented | 2.10 |
| Population-level stays population-level | 2.6 |
| README states chunk size, overlap, model, index, k | 2.10 |

---

## What this plan does not schedule

- **The missing pipeline diagram** ([problemStatement.md §11.7](./problemStatement.md)). Recover it
  before 2.1 if possible and reconcile against [architecture.md §3](./architecture.md), which is this
  project's own reading of the pipeline rather than a transcription of the brief's image.
- **ANN indexing.** Deliberately out of scope — [architecture.md §2](./architecture.md) specifies
  exact scan until the corpus exceeds roughly 50k chunks.
- **The cross-cutting gaps Phase 1 named and did not fix** — rate limiting on a public `/chat`, PII
  retention, conversation-history truncation. Retrieval adds embedding cost per request, which makes
  the rate-limiting gap slightly worse; still unaddressed, still worth naming.
