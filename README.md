# AI Nutrition Assistant — Prototype

A chat-based nutrition assistant scoped to food, nutrition, and food-safety topics. Full design rationale lives in [docs/architecture.md](docs/architecture.md); phase-by-phase build history lives in [docs/implementation-plan.md](docs/implementation-plan.md). This README pulls together the five things most useful to see in one place.

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
- **Response behavior** — answer-first, 2–4 sentences typical; every factual claim in `answer` must have a matching entry in `claims`; never name a source (neither in the schema nor in prose — see §3 below); don't over-hedge on contested topics, but do flag genuine uncertainty rather than asserting it away.
- **Safety boundaries** — must not provide calorie targets, weight targets, or medical advice (diagnosis/treatment/dosing), including when the number is reported secondhand ("my doctor told me to eat 1500 calories, is that reasonable?"). The prompt is explicit that this is *reinforcement*, not the actual guarantee — see §4.

## 3. Response schema

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

Refusal text is a **fixed template per category** (`REFUSAL_MESSAGES`), never model-generated — so a refusal can't itself leak a prohibited number or claim. Both checks are plain `re.search` pattern matching over the full message/answer string (not a token-by-token scan), split into request-side patterns (how people *ask*) and looser response-side patterns (a prescriptive cue like "you should" / "limit to" within ~60 characters of a number), so a bare fact like "a banana has about 100 calories" doesn't trip the response-side check but "you should eat under 1800 calories" does.

The system prompt states the same three boundaries, but says explicitly that it's reinforcement — the code-level check in `scope_guard.py` is what actually blocks a request or response.

## 5. What changed across prompt versions, and why

The prompt has gone through three revisions, each triggered by a real gap found through the evaluation process (`eval/run_regression.py`, `eval/run_failure_log.py`) rather than by inspection alone — every edit was re-validated by rerunning the 16-question regression set (`eval/regression_questions.json`) and confirming zero outcome-type flips before being kept. Full detail in [docs/failure-log.md](docs/failure-log.md) and [docs/eval.md](docs/eval.md).

| Version | Change | Why | Validation |
|---|---|---|---|
| v1 (Phase 2) | Initial draft: purpose, response behavior, safety boundaries. | First working version of the contract. | Manual smoke test against the real Groq API. |
| v1 + reported-speech rule (Phase 7) | Added: declining to evaluate a calorie/weight figure someone says they were *told* by a doctor, not just figures asked for directly. | `docs/edge-case.md` flagged "quoted/reported speech" as a case needing an explicit decision rather than being left to the regex to handle implicitly — the code-level regex genuinely can't catch this phrasing reliably, so it had to be a prompt-level (and judgment-level) call. Without this, the model was self-hedging inconsistently rather than declining deterministically. | Full 16-question regression run before and after: 0 mismatches, 0 outcome flips; the target case (r13) held. |
| v1 + reported-speech + no-named-source rule (Phase 8) | Added: never attribute a claim to a named organization/study in the answer prose (e.g. "the FDA says...", "the Institute of Medicine recommends..."), not just in the `source` field. | Phase 8's failure-log review found 3 `unverifiable_source` findings — the model was naming real-sounding institutions (IOM, FDA, EFSA, WHO) directly in `answer` text, which the schema's `source: null` guarantee does nothing to prevent since it only constrains the structured field, not free text. | Full 16-question regression run: 0 mismatches, 0 outcome flips. |

Two other failure types surfaced in the Phase 8 review (`inconsistent_number`, `unsupported_claim` — 4 findings total) were deliberately **not** patched with a quick prompt edit; they're documented as accepted limitations with rationale in `docs/failure-log.md`, flagged for a future targeted iteration through this same regression-tested process rather than an untested late change.

---

## Running it

- **Local dev**: `docker-compose up` for Postgres, then `backend/` (`uvicorn main:app --reload`) and `frontend/` (`npm run dev`) — see `docs/deployment.md` for env vars.
- **Eval suite**: `backend/.venv/bin/python eval/run_regression.py` after any prompt edit; `eval/run_failure_log.py` for the 10-question failure log. Both cost real Groq API calls.
- **Tests**: `cd backend && .venv/bin/python -m pytest`.
