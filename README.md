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

## Running it

- **Local dev**: `docker-compose up` for Postgres, then `backend/` (`uvicorn main:app --reload`) and `frontend/` (`npm run dev`) — see `docs/deployment.md` for env vars.
- **Corpus**: ingestion is offline and the result is committed as `backend/corpus/corpus_snapshot.jsonl.gz`. Production never parses a PDF or reaches a publisher. Load it with `python -m corpus.seed` (idempotent); re-ingest only with `pip install -r requirements-ingest.txt` first.
- **Eval suite**, all costing real Groq calls except the retrieval one:
  - `eval/run_retrieval_eval.py` — recall@k and the floor sweep. **No generation calls, free.**
  - `eval/run_regression.py` (Phase 1 path) and `eval/run_rag_regression.py` (retrieval path) — run both after any prompt edit, so a flipped outcome has one candidate cause rather than two.
  - `eval/run_failure_log.py` / `eval/run_rag_failure_log.py` — the 10-question failure log.
- **Tests**: `cd backend && .venv/bin/python -m pytest`.
