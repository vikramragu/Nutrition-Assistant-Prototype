# AI Nutrition Assistant — Prototype

A chat-based nutrition assistant that answers **only** from published dietary guidance, with a checkable citation on every claim. Ask it something the documents don't cover and it refuses rather than guessing.

Built in two milestones. **Phase 1** was the uncited prototype: chat, structured output, a code-enforced scope guard. **Phase 2** added retrieval over a frozen seven-document corpus and made every claim resolve to a quoted passage. Design rationale: [docs/architecture.md](docs/architecture.md) and [docs/features/rag-sourced-claims/architecture.md](docs/features/rag-sourced-claims/architecture.md). Build history: the two `implementation-plan.md` files beside them.

**§6 is the Phase 2 reference** — retrieval parameters, what the chunking cost, and the schema change.

---

## 1. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router, TypeScript) | Single deployable to Vercel; a chat UI is a natural fit. |
| Backend | FastAPI (Python) | Pydantic models double as both the model's structured-output schema and the API's request/response validation — one schema, no drift. |
| Model provider | [Groq API](https://groq.com), model `openai/gpt-oss-120b` | Fast inference on an open-weight model. Called via JSON Schema structured outputs (`response_format: {type: "json_schema", strict: true}`) — Groq's strict mode uses constrained decoding to guarantee schema-valid JSON, not just prompted-for JSON. |
| Storage | Postgres (Railway-managed in prod, Docker Compose locally) | Durable across redeploys; identical engine in both environments. |
| Frontend hosting | Vercel | Native Next.js support. |
| Backend hosting | Railway | Long-running FastAPI process + managed Postgres in the same project; `Procfile` runs Alembic migrations on every deploy. |

The model provider is abstracted behind a `ModelClient` protocol (`backend/services/model_client.py`) specifically so swapping providers or models later is a single-file change, not a rewrite — this project started on a different provider mid-build and the swap only touched that one file plus its tests.

---

## 2. The system prompt

Lives at [`backend/prompts/system_prompt.md`](backend/prompts/system_prompt.md), loaded once at import time and passed as the `system` message on every model call. It has three sections:

- **Purpose** — scopes the assistant to nutrient requirements, food storage/safety, cooking methods, and general dietary science; explicitly not a substitute for personalized medical/dietetic advice.
- **Response behavior** — answer-first, 2–4 sentences typical; every factual claim in `answer` must have a matching entry in `claims`; **cite what you were given and attribute to nothing else** (see §5 — this rule inverted in Phase 2); don't over-hedge on contested topics, but do flag genuine uncertainty rather than asserting it away.
- **Safety boundaries** — must not provide calorie targets, weight targets, or medical advice (diagnosis/treatment/dosing), including when the number is reported secondhand ("my doctor told me to eat 1500 calories, is that reasonable?"). The prompt is explicit that this is *reinforcement*, not the actual guarantee — see §4.

## 3. Response schema

> **Phase 1 shape, superseded.** `claims[].source` is now a required `Citation` and the answer is per-document — see §6.3. Kept here because §5's prompt history refers to it.

Defined once in `backend/db/schemas.py` and reused for both the Groq structured-output request and the API's own response validation:

```python
class ClaimSchema(BaseModel):
    claim: str
    source: Literal[None] = None   # not Optional[str] -- "always null" is a schema guarantee

class NutritionAnswer(BaseModel):
    answer: str
    claims: list[ClaimSchema]
```

`source: Literal[None]` means `null` is the *only* value the type system (and Groq's constrained decoding) will accept there — sourcing isn't a supported feature yet, and this makes that a structural guarantee rather than a convention the model could drift away from. The model's raw JSON is parsed with `json.loads()` then validated with `NutritionAnswer.model_validate()`; a schema violation raises a typed `ModelResponseError` and the API returns `502` — there is no "repair the JSON" fallback path.

The chat endpoint wraps this in one of two response shapes:

```python
{"type": "answer", "answer": str, "claims": [...]}
{"type": "refused", "reason": "calorie_target" | "weight_target" | "medical_advice", "message": str}
```

## 4. How the scope limit is enforced

**Code, not the prompt.** `backend/services/scope_guard.py` runs two deterministic, regex-based checks that are completely independent of the model:

- `check_request(message)` — runs **before** the message ever reaches Groq. If the question directly asks for a calorie target, weight target, or medical advice, the request is refused immediately, at zero model cost.
- `check_response(answer)` — runs **after** the model responds, before anything is persisted or returned. Catches the case where an innocuous-looking question caused the model to volunteer a prohibited category unprompted (e.g. a specific calorie number paired with "you should").

Phase 2 adds a fourth category, `personalised_guidance`, and a second entry point, `check_document_answer()`. The new rule keys on **second person**, not on a quantity: the corpus is full of population guidance written prescriptively ("adults should consume no more than 10%"), so a "should + a number" rule would refuse the documents' own phrasing. Measured over all 159 second-person sentences in the corpus, exactly one tripped a guard — the Dietary Guidelines' own *"If you have a chronic disease, talk with your health care professional"* — which is an `over_refusal` on a referral, and was fixed by excluding the hypothetical from the medical-advice pattern.

Refusal text is a **fixed template per category** (`REFUSAL_MESSAGES`), never model-generated — so a refusal can't itself leak a prohibited number or claim. Both checks are plain `re.search` pattern matching over the full message/answer string (not a token-by-token scan), split into request-side patterns (how people *ask*) and looser response-side patterns (a prescriptive cue like "you should" / "limit to" within ~60 characters of a number), so a bare fact like "a banana has about 100 calories" doesn't trip the response-side check but "you should eat under 1800 calories" does.

The system prompt states the same three boundaries, but says explicitly that it's reinforcement — the code-level check in `scope_guard.py` is what actually blocks a request or response.

## 5. What changed across prompt versions, and why

The prompt has gone through three revisions, each triggered by a real gap found through the evaluation process (`eval/run_regression.py`, `eval/run_failure_log.py`) rather than by inspection alone — every edit was re-validated by rerunning the 16-question regression set (`eval/regression_questions.json`) and confirming zero outcome-type flips before being kept. Full detail in [docs/failure-log.md](docs/failure-log.md) and [docs/eval.md](docs/eval.md).

| Version | Change | Why | Validation |
|---|---|---|---|
| v1 (Phase 2) | Initial draft: purpose, response behavior, safety boundaries. | First working version of the contract. | Manual smoke test against the real Groq API. |
| v1 + reported-speech rule (Phase 7) | Added: declining to evaluate a calorie/weight figure someone says they were *told* by a doctor, not just figures asked for directly. | `docs/edge-case.md` flagged "quoted/reported speech" as a case needing an explicit decision rather than being left to the regex to handle implicitly — the code-level regex genuinely can't catch this phrasing reliably, so it had to be a prompt-level (and judgment-level) call. Without this, the model was self-hedging inconsistently rather than declining deterministically. | Full 16-question regression run before and after: 0 mismatches, 0 outcome flips; the target case (r13) held. |
| v1 + reported-speech + no-named-source rule (Phase 8) | Added: never attribute a claim to a named organization/study in the answer prose (e.g. "the FDA says...", "the Institute of Medicine recommends..."), not just in the `source` field. | Phase 8's failure-log review found 3 `unverifiable_source` findings — the model was naming real-sounding institutions (IOM, FDA, EFSA, WHO) directly in `answer` text, which the schema's `source: null` guarantee does nothing to prevent since it only constrains the structured field, not free text. | Full 16-question regression run: 0 mismatches, 0 outcome flips. |

| **v2 (Phase 2.8)** | **The attribution rule inverted.** Removed the no-named-source rule above; replaced it with *cite what you were given, and attribute to nothing else*. | Phase 2 retrieves real passages and requires a citation on every claim, so the v1 rule forbade the thing the product now exists to do. The replacement is deliberately **not** the opposite instruction — "always name your source" would be wrong on the uncited path, which still has none. It is the principle underneath both versions, and it is correct whether or not passages are supplied, which matters because one prompt serves both paths. | **Two** full runs, one per path, so a flip has one candidate cause: 0 mismatches and 0 outcome flips on the Phase 1 path; 12 flips on the retrieval path, all `answer → not_in_corpus`, each explained in [prompt-inversion-regression.md](docs/features/rag-sourced-claims/prompt-inversion-regression.md). Measured: 0 of 20 uncited-path responses name a source, same as under v1. |

Two other failure types surfaced in the Phase 8 review (`inconsistent_number`, `unsupported_claim` — 4 findings total) were deliberately **not** patched with a quick prompt edit; they're documented as accepted limitations with rationale in `docs/failure-log.md`, flagged for a future targeted iteration through this same regression-tested process rather than an untested late change.

---

## 6. Retrieval (Phase 2)

### 6.1 The five parameters

| Parameter | Value | Why |
|---|---|---|
| **Chunk size** | **400 text tokens** | `512` model window − `64` for the heading prefix − `48` margin. Each chunk is embedded as `"{document} — {section}\n\n{text}"`, so the budget has to leave room for that header. |
| **Overlap** | **64 tokens** | One or two sentences, enough that a recommendation split across a boundary still retrieves from either side. |
| **Embedding model** | **`BAAI/bge-small-en-v1.5`**, 384 dims, ONNX via `fastembed`, run locally | Groq has no embeddings endpoint. A local model keeps this a one-provider project rather than adding a second vendor, a second secret and a second outage surface for 384 floats. BGE-small also beats `all-MiniLM-L6-v2` on MTEB retrieval at the same width. |
| **Index type** | **None — exact (flat) cosine scan** | 103 chunks. An IVFFlat or HNSW index would add tuning surface and *approximate* recall to a problem exact search solves in single-digit milliseconds. Measured median query latency **8.9 ms**. Revisit above ~50k chunks. |
| **k** | **8** | recall@8 = 0.975 and recall@10 = 0.975 — the curve is flat past 8, so a larger `k` buys context and nothing else. |

Plus one that is not on the brief's list but matters more than any of them:

| | | |
|---|---|---|
| **Relevance floor** | **0.69** | Cosine similarity below which nothing is retrieved — which makes it *the not-in-corpus refusal*, not a tuning knob. Measured, not guessed: in-corpus top-1 scores span 0.709–0.873, out-of-corpus 0.440–0.680, and 0.69 is the midpoint of the 0.68–0.70 band that separates them. Full sweep in [retrieval-calibration.md](docs/features/rag-sourced-claims/retrieval-calibration.md). |

The band is only **0.02 wide**, which is why `answers_question` — the model's own verdict on whether the passages answer the question — is the stronger of the two gates. In the Phase 2.10 failure-log run, five of eight refusals came from that second gate rather than the floor.

`k` and the floor are overridable via `RETRIEVAL_K` / `RETRIEVAL_FLOOR`; an override logs a warning at import, because changing the floor silently invalidates every number in the calibration document.

### 6.2 The chunking strategy, and what it cost

Heading-aware, with a paragraph fallback and one hard rule: **never split a table or a numbered-recommendation list.** An oversized chunk is better than two broken ones — half a storage-time table retrieves confidently and answers wrongly.

Seven documents → **103 chunks**:

```
                                      chunks   median tokens
usda-hhs-dga-2025                        24         111
fsanz-temperature-control-2002           23         356
ie-doh-food-pyramid-2016                 17          73
who-healthy-diet-2026                    17         224
icmr-nin-my-plate-2024                   10         176
fssai-used-cooking-oil-2018               8         161
who-five-keys-2013                        4         132
                                        ---
overall   min 40 · median 179 · mean 207 · max 475
```

**What it cost, concretely:**

- **Two chunks exceed the 400-token budget** (largest 475). That is the never-split rule firing, and it is the intended trade.
- **Thirty chunks are under 100 tokens**, nearly all from the Irish food pyramid — median **73**. It is a poster, not prose: short captions around a graphic. Those chunks retrieve on topic and often have little to say, which is precisely the borderline-relevance case that made gate 2 load-bearing.
- **Three passages are quarantined out of the index entirely.** PDF extraction destroys tables — it yields positioned text, so labels and numbers arrive as separate runs with nothing linking them. The Irish calorie table became `"Active Child Teenager Adult … 2000kcal Inactive 1800kcal"`: four labels, four values, no way to pair them. Measured, that chunk cleared the floor on *every* calorie question tried and ranked first for one of them. It is now excluded by a reviewed list in `corpus.yaml`, not by a heuristic — three text statistics were tested across all 105 chunks and none separates a destroyed table from ordinary bulleted guidance. **The cost: the corpus can no longer state a weekly alcohol limit at all**, and questions about the removed figures now land on neighbouring passages scoring 0.66–0.68. That is what narrowed the floor's separation band from 0.07 to 0.02.
- **Bibliographies are excluded.** WHO's REFERENCES section was the single largest chunk — 618 tokens of study names, and a direct route back to the `unverifiable_source` failure the whole design exists to prevent.

### 6.3 The schema change, and why

The brief says: *if your schema doesn't fit real citations, change it and note what you changed.* It did, and this is the note.

```python
# Phase 1                          # Phase 2
class ClaimSchema:                 class CitedClaim:
    claim: str                         claim: str
    source: Literal[None] = None       source: Citation      # required, non-nullable

                                   class ChatAnswerResponse:
                                       document_answers: list[DocumentAnswerOut]
                                       searched: list[DocumentRef]
```

**It is not the citation that forced this — it is per-document answering.** A citation fits inside the existing `claims[]` perfectly well. But a single top-level `answer` string cannot represent *"two documents, answered separately, never merged"* without either blending them or picking a winner, and the brief forbids both. The shape had to change.

Why answering is per-document at all: the model is called **once per document**, never once with everything retrieved. That makes blending two publishers into one sentence *unrepresentable* rather than merely discouraged — a call can only cite chunks it was given, and it was given one document's. Ask "how much protein do I need?" and you get two blocks: WHO says 10–15% of energy (≈50–75 g/day), the USDA says 1.2–1.6 g/kg of body weight. Both shown, neither declared the winner.

Two supporting changes:

- **`claims.chunk_id` is a foreign key to `chunks`, not a denormalised string.** Publisher, year, name and URL are derived by join and cannot drift from the corpus. `ON DELETE RESTRICT`: deleting a cited chunk must fail loudly rather than silently leave an answer with one fewer citation than it was written with.
- **`messages.ordinal`**, with `UNIQUE (conversation_id, ordinal)`. A turn now writes one user message plus one assistant message per document, in a single transaction — and Postgres `now()` is the transaction timestamp, so every row of that turn shares a `created_at` and cannot be ordered by it.

**The model never writes a citation.** It emits `{claim, chunk_id}` and nothing else about the source; the backend expands the id by join. A publisher it never writes is a publisher it cannot fabricate. Any id that was not in that call's context fails validation and discards the whole response — no repair, no dropping the offending claim and keeping the rest, because prose whose supporting claim has quietly vanished is exactly the uncited assertion the schema was shaped to prevent.

### 6.4 Does it work?

[failure-log.md](docs/features/rag-sourced-claims/failure-log.md) runs the ten fixed questions against production and reviews every response against nine failure types. **3 findings, down from Phase 1's 7.** The two `inconsistent_number` failures are gone for a structural reason — Phase 2 copies numbers out of passages instead of generating them, so there is nothing to vary between runs and no arithmetic to get wrong.

The honest counterweight: **eight of the ten questions now get a refusal**, because they were written for a general-purpose assistant and predate the corpus. That makes it a good test of refusal behaviour and a weak test of answering.

---

## 7. The evidence documents

Each Phase 2 step left a record of what was *measured*, not only what was built. Four of them hold the numbers this README quotes. Read them in pipeline order: what went into the index, how well it is searched, how an answer is made from it, and what the prompt change did to the whole.

| Document | Phase | The question it answers |
|---|---|---|
| [ingestion-report.md](docs/features/rag-sourced-claims/ingestion-report.md) | 2.1–2.3 | What went into the index, and what was deliberately kept out? |
| [retrieval-calibration.md](docs/features/rag-sourced-claims/retrieval-calibration.md) | 2.4 | Does search find the right passage, and where does the relevance floor go? |
| [answer-layer.md](docs/features/rag-sourced-claims/answer-layer.md) | 2.5–2.6 | How is a cited answer produced, and what stops a bad one? |
| [prompt-inversion-regression.md](docs/features/rag-sourced-claims/prompt-inversion-regression.md) | 2.8 | Did rewriting the system prompt change behaviour, and why did each changed outcome change? |

### 7.1 ingestion-report.md — turning seven documents into 103 chunks

Records how the PDFs and the WHO web page were parsed and chunked, the size of every document's chunks, and each defect found along the way.

> **Example.** In the first clean run, the largest chunk in the whole index was WHO's **REFERENCES** section: 618 tokens of names like *"Hooper L, Abdelhamid A… Cochrane Database Syst Rev. 2015"*. Retrieved, it invites the model to attribute a claim to a study it has only seen listed. That is the `unverifiable_source` failure this project exists to prevent. Reference lists, contents pages and acknowledgements are now excluded by heading. Matching on "reference" as a substring is what also catches FSSAI's *"Other References"*.

It is also where the most serious parser defect is written up: every Dietary Guidelines heading was attached to the *next* section, so *"Eat a variety of colourful vegetables"* was cited as **§ Gut Health** (§9.5).

### 7.2 retrieval-calibration.md — measuring search, and setting the floor

A hand-labelled set of 28 questions (20 the corpus answers, 8 it does not) measures two things: whether search returns the right chunk (recall@k), and the score that separates the two groups. That score is the relevance floor, which *is* the not-in-corpus refusal.

> **Example.** The weakest question the corpus genuinely answers still scores **0.709**. The strongest one it does *not* answer, *"standard drinks a week, lower-risk limit for women"*, reaches **0.680**, because the Irish food pyramid's Fluids section is on the right topic even though the limit itself was quarantined. The floor has to fit between them. A sweep across candidate values shows 0.68–0.70 is the only range that answers all 20 and refuses all 8. **0.69** is its midpoint: 0.010 above the worst negative and 0.019 below the worst positive.

That gap is only 0.02 wide. So this document is also why the model's own verdict (`answers_question`, gate 2) is treated as the stronger of the two gates.

### 7.3 answer-layer.md — producing a cited answer, and the endpoint

Covers one generation call per document, the citation validator, which rules block a response and which only log, the `POST /chat` wire types, and the personalisation scope rule.

> **Example.** The model returns only `{claim, chunk_id}`. It never writes a publisher, year or URL; the backend fills those in from the database. Suppose the call that was given WHO's passages cites a Dietary Guidelines chunk. The id is real, but that call was never given it, so the whole response fails with a **502**. Nothing is repaired. Dropping that claim would leave prose whose support has quietly vanished, and rewriting the citation would mean the backend choosing a source for a sentence it did not write.

It also records a defect found by reading one rendered prompt rather than by running tests: every prompt carried its passages **twice** and was 58% over size, because the template's own comment named the placeholders (§5).

### 7.4 prompt-inversion-regression.md — the prompt rewrite, regression-tested

v1 of the system prompt forbade naming any source, which was right for Phase 1 and wrong once every claim must cite one. This document records the change to v2 and two regression runs, separated so that each changed outcome has exactly one possible cause: the Phase 1 path first (isolating the prompt edit: 0 flips), then the retrieval path (isolating retrieval: 12 flips, each explained).

> **Example.** *"What internal temperature should ground beef be cooked to?"* retrieved the FSANZ food-safety document at **0.743**, well above the floor, because FSANZ is full of temperatures: 5 °C, 60 °C, the 2-hour/4-hour rule. But none of them is a cooking temperature for beef. The model read the passages and answered `answers_question: false`. Without gate 2 this would have been a confident, correctly cited, **wrong** answer.

It also found an `over_refusal` in the corpus's own safest sentence. The Dietary Guidelines' *"If you have a chronic disease, talk with your health care professional…"* tripped the medical-advice guard, which was then fixed to tell a hypothetical apart from an assertion (§6.2).

---

## 8. Decisions worth knowing, and why

Consolidated from the Phase 2 working notes ([context-handoff.md](docs/features/rag-sourced-claims/temp/context-handoff.md)). These are the decisions that look odd, or easy to "simplify", until you know the reason.

### Storage and the corpus

| Decision | Why |
|---|---|
| **pgvector in the existing Postgres**, not Chroma or Pinecone | Railway Postgres already exists. A separate vector store would add a service, a secret and an outage surface to hold 103 rows. |
| **Chunk ids are `uuid5("slug:ordinal")`**, not random | `claims.chunk_id` is a foreign key. Random ids would orphan every past citation on each re-seed, and re-seeding is routine because guidance documents are revised in place. Hashing the chunk *text* was rejected: it would mint new ids on any whitespace change. |
| **The corpus snapshot is gzipped JSONL, committed to git** | A corpus change becomes a reviewable diff. Production loads the snapshot and never parses a PDF or contacts a publisher's site. |
| **The seed fingerprints the chunking, not only the source file** | After the heading fix, re-seeding printed `skipped=7, chunks written=0`. The PDFs had not changed, only how we read them, so a byte hash called the stale chunks up to date. A tool that reports success while being out of date is worse than the bug it hid. |
| **WHO's 2018 PDF was replaced by WHO's current 2026 page** | The PDF still said free sugars "less than 5%" and fat "30% of total energy", guidance WHO has since revised. The corpus was shipping stale advice. |

### Parsing and retrieval

| Decision | Why |
|---|---|
| **Queries and passages are embedded by two separate methods** (`embed_query`, `embed_passages`) | BGE is asymmetric: queries need a fixed instruction prefix and passages must have none. `fastembed`'s own `query_embed()` turned out *not* to add the prefix (its output is byte-identical to `embed()`, verified), so it is not used. Getting this wrong would quietly degrade retrieval and look like over-refusal. |
| **PDF text is re-ordered by layout bands and columns** before chunking | A PDF stores positioned text boxes in no guaranteed reading order. Read in stored order, every Dietary Guidelines heading landed on the wrong section. Two simpler fixes failed first: sorting by position spliced two columns mid-sentence, and splitting at the page midpoint broke the single-column FSANZ document. The final shape is in `parse._reading_order`. |
| **The floor was not nudged for the one over-refusal** | The sweeteners question missed the floor by 0.002. Moving a measured value to fix one anecdote would invalidate the whole calibration. The right fix is a document that actually covers sweeteners. |

### Answering and the API

| Decision | Why |
|---|---|
| **`chunk_id` is validated in our code, not constrained by the model's decoder** | Listing the allowed ids as a JSON Schema `enum` in Groq strict mode would be a stronger guarantee in principle. But support for it is unverified, and it moves the guarantee into a vendor's decoder, where a regression would be invisible. The validator is tested and fails loudly. |
| **`chunk_id` is typed `str`, not `UUID`, in the model's schema** | A fabricated id then fails *citation* validation ("cited something it was not given") rather than *schema* validation ("broke the contract"). Both are a 502; only one tells you what happened. |
| **503 for "model unavailable", 502 for "model answered badly"** | A rate limit or timeout is transient and worth retrying; a schema or citation failure is not. Nothing is persisted in either case, so a retry starts clean. |
| **`POST /chat/stream` streams progress, never the answer** | Progress is sent as Server-Sent Events: `searching` (the corpus), then `reading` (the documents that cleared the floor). Then the final `result` event carries exactly the body `POST /chat` returns. The answer is not streamed token by token, because the post-model scope guard needs the *whole* answer, and a refusal cannot protect a user who has already read the prohibited sentence. `POST /chat` is unchanged, because the eval harness and the tests depend on its status codes. Once a stream has started it has already sent 200, so the stream endpoint reports later failures as `event: error`. |

### Deployment

| Decision | Why |
|---|---|
| **The embedding model is downloaded at build time and loaded at startup** | Lazily, a container without the weights would boot green and then download 64 MiB on a real user's first question. Loading at startup turns that into a failed deploy, with the path it looked in printed next to the error. One constant, `CACHE_PATH`, serves both the build step and the runtime, so the two cannot disagree. |
| **The build config is `backend/railway.json`**, not `nixpacks.toml` | Railway builds with Railpack, which ignores `nixpacks.toml`, and a skipped build step does not fail the deploy. The original config would have silently never run. |

**The pattern behind most of these:** the worst defects produced plausible output and no error. The wrong-section headings, the doubled prompts, the identical timestamps and the "up to date" seed were each found by reading the data, not by running the code.

---

## Running it

- **Local dev**: `docker-compose up` for Postgres, then `backend/` (`uvicorn main:app --reload`) and `frontend/` (`npm run dev`) — see `docs/deployment.md` for env vars.
- **Corpus**: ingestion is offline and the result is committed as `backend/corpus/corpus_snapshot.jsonl.gz`. Production never parses a PDF or reaches a publisher. Load it with `python -m corpus.seed` (idempotent); re-ingest only with `pip install -r requirements-ingest.txt` first.
- **Eval suite**, all costing real Groq calls except the retrieval one:
  - `eval/run_retrieval_eval.py` — recall@k and the floor sweep. **No generation calls, free.**
  - `eval/run_regression.py` (Phase 1 path) and `eval/run_rag_regression.py` (retrieval path) — run both after any prompt edit, so a flipped outcome has one candidate cause rather than two.
  - `eval/run_failure_log.py` / `eval/run_rag_failure_log.py` — the 10-question failure log.
- **Tests**: `cd backend && .venv/bin/python -m pytest`.
