# Implementation Plan — AI Nutrition Assistant Prototype

This plan sequences the work described in [problemStatement.md](./problemStatement.md) and [architecture.md](./architecture.md) into phases. Each phase produces a working, testable increment and lists its exit criteria (traced back to the acceptance criteria in problemStatement.md §9). Phases are intended to be done in order — later phases depend on earlier ones being stable.

---

## Phase 0 — Project Scaffolding & Environments

**Goal:** empty-but-running skeleton for every piece of the stack, deployable end-to-end before any real logic exists.

- Initialize git repo, monorepo layout per architecture.md §13 (`/frontend`, `/backend`, `/docs`, `/eval`).
- `backend/`: FastAPI app skeleton, `GET /health`, `requirements.txt`/`pyproject.toml`, `.env.example` (`GROQ_API_KEY`, `DATABASE_URL`).
- `frontend/`: Next.js (App Router, TypeScript) skeleton, `lib/api.ts` stub, `.env.example` (`NEXT_PUBLIC_API_BASE_URL`).
- `docker-compose.yml` for local Postgres.
- CORS configured on FastAPI restricted to the frontend origin (local first, Vercel origin added in Phase 6).
- Push to GitHub.

**Exit criteria**
- [ ] Repo pushed to GitHub.
- [ ] `GET /health` returns 200 locally.
- [ ] Next.js dev server renders a blank page and can reach `/health` through `lib/api.ts`.

---

## Phase 1 — Data Model & Persistence

**Goal:** durable storage for conversations, messages, claims, refusals, and eval runs, per architecture.md §11.

- SQLAlchemy models: `conversations`, `messages`, `claims`, `scope_refusals`, `eval_runs`, `eval_findings`.
- Migration tooling (Alembic) initialized against local Docker Postgres.
- `services/conversation.py`: create conversation, append message, load history.
- `db/schemas.py`: Pydantic request/response types, including the `NutritionAnswer`/`Claim` schema from architecture.md §5 (`source: Literal[None]`).

**Exit criteria**
- [ ] Tables created via migration.
- [ ] Conversation can be created and a message appended via a unit test or script, independent of the model call.
- [ ] `Claim.source` is typed so a non-null value fails validation.

---

## Phase 2 — Model Client & Structured Output Contract

**Goal:** a single, swappable path from (system prompt, history, message) → validated `NutritionAnswer`, with hard failure on non-conformance.

- `services/model_client.py`: `ModelClient` Protocol (architecture.md §6).
- `GroqModelClient`: structured-output call against Groq's chat completions API (`response_format={"type": "json_schema", "json_schema": {"strict": True, "schema": ...}}`) on model `openai/gpt-oss-120b`, built from the `NutritionAnswer` schema. Groq's strict mode uses constrained decoding to guarantee schema-valid JSON.
- Validation step: `json.loads(response.choices[0].message.content)` then `NutritionAnswer.model_validate(...)`; on invalid JSON or `ValidationError`, raise a typed exception — no prose fallback, no manual extraction.
- Draft `prompts/system_prompt.md` v1 (purpose, response behavior, claim representation, uncertainty language, safety-boundary text) per architecture.md §9.1. This is a first draft only — regression testing happens in Phase 5.
- Manual smoke test: call the real Groq API with a handful of nutrition questions, confirm structured output round-trips.

**Exit criteria**
- [ ] Model calls only happen from backend code; API key never referenced in `frontend/`.
- [ ] Valid model output parses into `NutritionAnswer`.
- [ ] A deliberately malformed/mocked model response is rejected as a validation failure, not repaired.

---

## Phase 3 — Scope Guard (Code-Level Safety)

**Goal:** deterministic, prompt-independent enforcement of the three prohibited categories, per architecture.md §8.

- `services/scope_guard.py`: `ScopeVerdict`, `check_request(message)`, `check_response(answer)`.
- Curated regex/keyword rule sets for: calorie targets, weight targets, medical advice — built and iterated against representative phrasings the team writes by hand (not yet the formal failure-log set, that's Phase 7).
- Static, non-model-generated refusal templates per category (architecture.md §8.2), directing to a qualified professional.
- Unit tests: rule set catches the explicit example phrasings from the problem statement (§5) for both pre-model and post-model checks.

**Exit criteria**
- [ ] `check_request` blocks obvious calorie/weight/medical-advice questions before any model call.
- [ ] `check_response` blocks a (mocked) model answer that volunteers a calorie number unprompted.
- [ ] Refusal responses are template text, never model output.
- [ ] Guard functions have unit tests independent of the live model API.

---

## Phase 4 — Chat Endpoint (Wiring It Together)

**Goal:** the full request sequence from architecture.md §7.3 working end-to-end against a real database and real model.

- `routers/chat.py`: `POST /chat`, `POST /conversations`, `GET /conversations/{id}`.
- Sequence: parse request → `ScopeGuard.check_request` → load history → `ModelClient.get_structured_answer` → validate → `ScopeGuard.check_response` → persist (user msg, assistant msg, claims) → return.
- On validation failure: log to `eval_findings`-compatible structure (full logging wired in Phase 7) and return HTTP 502, per architecture.md §5.
- On scope block (pre or post): persist to `scope_refusals` with `stage` (`pre_model`/`post_model`) and `category`; return `{type: "refused", ...}`.
- Router stays thin — no business logic beyond call sequencing, per architecture.md §7.1.

**Exit criteria**
- [ ] `POST /chat` returns `{type: "answer", answer, claims[]}` for an in-scope question, with every `claims[].source == null`.
- [ ] `POST /chat` returns `{type: "refused", reason, message}` for an out-of-scope question, matching the correct category.
- [ ] A forced validation failure returns HTTP 502 and does not persist a corrupted assistant message.
- [ ] `GET /conversations/{id}` returns full history after a page-reload-style reload.

---

## Phase 5 — Chat Frontend

**Goal:** the two-pane chat UI from architecture.md §4, talking only to the backend.

- `app/page.tsx` two-pane layout: conversation pane + sources panel.
- `ChatWindow`: owns `conversationId`/`messages[]`, calls `POST /chat`, handles loading/error/refusal states.
- `MessageList`: renders `answer`; claims shown as a collapsed "Claims (unsourced)" disclosure.
- `MessageInput`: controlled textarea + submit, disabled while in flight.
- `SourcesPanel`: presentational-only stub, always empty state, isolated as its own component.
- `RefusalNotice`: visually distinct rendering for `{type: "refused"}` responses.
- `conversation_id` persisted in local state (+ optional `localStorage`) so a refresh can reload via `GET /conversations/{id}`.
- No Groq SDK or API key anywhere in `frontend/`.

**Exit criteria**
- [ ] Full chat flow works locally against the Phase 4 backend: ask a question, see the answer, see a refusal styled differently from a normal answer, see a generic error on 5xx.
- [ ] Sources panel renders and stays empty regardless of conversation content.
- [ ] Page refresh reloads prior messages for the same conversation.

---

## Phase 6 — Deployment

**Goal:** the prototype accessible at a public URL, per architecture.md §12.

- Railway: deploy FastAPI backend + managed Postgres plugin in the same project; set `GROQ_API_KEY` and `DATABASE_URL` as secrets; run migrations against the managed DB.
- Vercel: deploy `frontend/`, set `NEXT_PUBLIC_API_BASE_URL` to the Railway backend URL.
- Update backend CORS to the live Vercel origin.
- Smoke test the full flow (answer, refusal, reload) against the deployed URLs, not just localhost.

**Exit criteria**
- [ ] Application is reachable at a public URL without a local dev environment.
- [ ] Chat flow works end-to-end in production (matches Phase 4/5 exit criteria, against Railway/Vercel).
- [ ] No secrets present in the frontend bundle (spot-check via browser devtools/network tab).

---

## Phase 7 — Regression Suite & System Prompt Iteration

**Goal:** the fixed regression workflow required by problemStatement.md §4, so future prompt edits are checked systematically rather than by feel.

- `eval/regression_questions.json`: fixed question set (broader coverage than the 10-question failure log; whatever the team judges useful for catching regressions).
- `eval/run_regression.py`: loads current prompt, runs every question through the real pipeline (ScopeGuard → ModelClient → validation), writes timestamped output to `eval/runs/<timestamp>.json`, prints a diff-style summary vs. the previous run.
- Use this harness to iterate on `prompts/system_prompt.md` from its Phase 2 draft toward a stable version: after each edit, run the full set, review outputs, confirm the intended fix landed and nothing else broke.

**Exit criteria**
- [ ] Regression question set exists and is checked in.
- [ ] `run_regression.py` runs unattended against the live pipeline and produces a reviewable diff.
- [ ] At least one real prompt iteration has been carried out through this workflow (edit → run → review → confirm no regressions), demonstrating the process works.

---

## Phase 8 — Failure Log (10-Question Evaluation)

**Goal:** the durable, categorized failure log required by problemStatement.md §6.

- `eval/failure_log_questions.json`: exactly 10 questions covering all four required categories (nutrient requirements, food safety/storage, cooking methods, no-clear-answer).
- `eval/run_failure_log.py`: runs all 10 through the real pipeline, saves raw outputs.
- Manual review pass: for each response, inspect for the five failure types (unsupported claims, inconsistent numbers, unverifiable sources, missed scope restrictions, unhelpful hedging) and record findings into `eval_findings` (`failure_type` enum matching the five types exactly).
- `eval/summarize_failures.py`: groups and counts findings by type, prints a table.
- Check the summarized table into `docs/failure-log.md`.

**Exit criteria**
- [ ] All 10 questions run against the deployed (or local, real-model) pipeline.
- [ ] Every response manually reviewed against all five failure types.
- [ ] Findings grouped and counted by type in `docs/failure-log.md`.
- [ ] Any findings that reveal gaps (e.g., a missed scope restriction) are fed back into Phase 3 rules or Phase 7 prompt iteration, closing the loop described in problemStatement.md's Core Rule.

---

## Phase Dependency Summary

```
0 (scaffold) → 1 (data model) → 2 (model client) → 3 (scope guard) → 4 (chat endpoint)
                                                                          │
                                                          ┌───────────────┴───────────────┐
                                                          ▼                               ▼
                                                  5 (frontend)                    7 (regression suite)
                                                          │                               │
                                                          ▼                               ▼
                                                  6 (deployment) ──────────────▶ 8 (failure log)
```

Phases 5 and 7 can proceed in parallel once Phase 4 is stable (frontend work doesn't block prompt iteration, and vice versa). Phase 6 (deployment) should happen before Phase 8 (failure log) so the failure log reflects the real deployed pipeline, not just local dev — though it can be run locally first as a dry run if deployment is delayed.

---

## Traceability to problemStatement.md §9 Acceptance Criteria

| Acceptance criterion | Phase |
|---|---|
| Chat frontend, message list, input box | 5 |
| Sources panel (empty) | 5 |
| Backend chat endpoint | 4 |
| Conversation storage | 1, 4 |
| Model calls server-side only | 2, 5 |
| Structured schema (`answer`, `claims[].{claim,source}`) | 1, 2 |
| Every `source` is `null` | 1, 2 |
| Responses validated; invalid → hard failure | 2, 4 |
| Dedicated system prompt (purpose, behavior, length, boundaries) | 2, 7 |
| Fixed regression set, rerun after prompt changes | 7 |
| Calorie/weight/medical advice blocked in code | 3, 4 |
| Code-level enforcement, pre-model when practical | 3 |
| 10 evaluation questions, 4 categories, run, recorded, grouped/counted | 8 |
| Pushed to GitHub | 0, ongoing |
| Deployed to Vercel/Railway, public URL | 6 |
