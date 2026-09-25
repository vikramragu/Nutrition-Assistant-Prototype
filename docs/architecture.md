# Architecture — AI Nutrition Assistant Prototype

This document defines the technical architecture for building the prototype described in [problemStatement.md](./problemStatement.md). It translates each requirement into concrete components, data flows, and interfaces so implementation can proceed without re-deriving design decisions.

---

## 1. Guiding Principle

Per the problem statement's Core Rule, the architecture optimizes for a **reliable contract**, not model cleverness:

- Every model response is validated against a fixed schema before it reaches storage or the client.
- Safety boundaries are enforced by application code that runs independently of the model.
- Model access is never exposed to the browser.
- Failures are recorded, not silently repaired.

Everything below is designed so that a later milestone can populate `claims[].source` and wire up a retrieval/citation pipeline **without changing the request/response contract**.

---

## 2. Chosen Stack

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Next.js (App Router, TypeScript) | Single deployable to Vercel, server-side route handlers double as a thin proxy if ever needed, good fit for a chat UI with streaming. |
| Backend | FastAPI (Python) | Native Pydantic models double as the structured-output schema and the request/response validation layer — one schema, no drift. |
| Model provider | Groq API, model `openai/gpt-oss-120b`, via JSON Schema structured outputs (`response_format: {type: "json_schema", strict: true}`) | Fast inference on an open-weight model; Groq's strict mode uses constrained decoding to guarantee schema-valid JSON. Abstracted behind a `ModelClient` interface (Section 6) so swapping providers or models is a single-file change. |
| Storage | Postgres (Railway-managed) | Survives redeploys/restarts (unlike SQLite on an ephemeral container filesystem), works identically in local dev via Docker and in production via Railway's managed Postgres plugin. |
| Frontend hosting | Vercel | Native Next.js support, zero-config. |
| Backend hosting | Railway | Long-running FastAPI process + managed Postgres in the same project. |

These are opinionated choices made from the problem statement's allowed options. The abstractions in Sections 6 and 8 keep each choice swappable.

---

## 3. High-Level Component Diagram

```
┌─────────────────────────────┐
│         Browser              │
│  Next.js Chat UI              │
│  - Message list                │
│  - Input box                    │
│  - Sources panel (empty stub)     │
└───────────────┬──────────────────┘
                │ HTTPS (fetch)
                │ POST /api/chat
                ▼
┌───────────────────────────────────────────────────────┐
│                    FastAPI Backend                       │
│                                                           │
│  ┌─────────────┐   ┌──────────────────┐                  │
│  │ Chat Router  │──▶│ Scope Guard        │  (Section 8)   │
│  │ /chat        │   │ (pre-model check)  │                │
│  └─────────────┘   └────────┬───────────┘                 │
│                              │ allowed                     │
│                              ▼                             │
│                     ┌──────────────────┐                   │
│                     │ Conversation Svc  │  loads history    │
│                     └────────┬─────────┘                   │
│                              ▼                             │
│                     ┌──────────────────┐                   │
│                     │ ModelClient        │  (Section 6)     │
│                     │ (Groq, JSON Schema │                  │
│                     │  structured output)│                  │
│                     └────────┬─────────┘                   │
│                              ▼                             │
│                     ┌──────────────────┐                   │
│                     │ Schema Validator   │  Pydantic         │
│                     │ (Section 5)        │  → reject on fail │
│                     └────────┬─────────┘                   │
│                              ▼                             │
│                     ┌──────────────────┐                   │
│                     │ Persistence Layer  │  (Section 7)     │
│                     └────────┬─────────┘                   │
│                              ▼                             │
│                     JSON { answer, claims[] }               │
└───────────────┬───────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐        ┌───────────────────────────┐
│   Postgres (Railway)            │        │  Eval Harness (offline)      │
│  conversations / messages /      │◀──────│  10-question failure log     │
│  claims / scope_refusals /        │       │  (Section 9)                 │
│  eval_runs                          │       └───────────────────────────┘
└───────────────────────────────┘
```

---

## 4. Frontend Architecture

### 4.1 Layout

Single page (`app/page.tsx`) with a two-pane layout:

```
┌───────────────────────────────┬──────────────────┐
│  Conversation Pane               │  Sources Panel      │
│  ┌───────────────────────────┐   │                      │
│  │ MessageList                 │   │  (empty state:       │
│  │  - user bubble                │   │   "No sources yet — │
│  │  - assistant bubble            │   │   coming in a       │
│  │    (renders `answer`)           │   │   future milestone")│
│  └───────────────────────────┘   │                      │
│  ┌───────────────────────────┐   │                      │
│  │ MessageInput (textarea+send) │   │                      │
│  └───────────────────────────┘   │                      │
└───────────────────────────────┴──────────────────┘
```

### 4.2 Components

- `ChatWindow` — owns conversation state (`conversationId`, `messages[]`), calls the backend, handles loading/error/refusal states.
- `MessageList` — renders messages. Assistant messages render `answer` as text; `claims[]` are stored but **not rendered as clickable citations yet** since `source` is always `null` — render as a collapsed "Claims (unsourced)" disclosure for transparency during development, not required by acceptance criteria but useful for manual QA.
- `MessageInput` — controlled textarea + submit button, disabled while a request is in flight.
- `SourcesPanel` — pure presentational stub component. Takes `sources: Source[]` prop, always renders an empty state for this milestone. Kept as its own component (not inlined) so the future milestone only touches this one file.
- `RefusalNotice` — renders when the backend returns a scope-refusal response (Section 8), styled distinctly from normal answers so users understand it's a policy boundary, not an error.

### 4.3 Data flow

1. User submits text → `ChatWindow` optimistically appends a user message to local state.
2. `POST /api/chat` with `{ conversation_id, message }` (frontend calls the FastAPI backend directly, or via a Next.js route handler proxy if CORS/env-separation makes that preferable — see Section 10).
3. Response is either:
   - `{ type: "answer", answer, claims[] }` → append assistant message.
   - `{ type: "refused", reason }` → render `RefusalNotice`.
   - `4xx/5xx` → render generic error, do not fabricate an assistant message.
4. `conversation_id` is persisted in local component state (and optionally `localStorage`) so a page refresh can reload history via `GET /api/conversations/{id}`.

The frontend never imports or calls the Groq SDK — no API key is ever shipped to the browser bundle.

---

## 5. Structured Response Schema

Defined once, in Pydantic, and reused for: (a) the JSON Schema sent to Groq, (b) response validation, (c) the DB row shape, (d) the frontend TypeScript type (kept in sync manually or via an OpenAPI-generated client — see Section 10).

```python
class Claim(BaseModel):
    claim: str
    source: Literal[None] = None  # forced null this milestone; future milestone widens this type

class NutritionAnswer(BaseModel):
    answer: str
    claims: list[Claim]
```

Key implementation details:

- The schema is sent to Groq's chat completions API via `response_format={"type": "json_schema", "json_schema": {"name": "nutrition_answer", "strict": True, "schema": {...}}}`, on `openai/gpt-oss-120b` — one of the models Groq's strict mode supports. Strict mode uses constrained decoding to guarantee the output matches the schema exactly (every property `required`, `additionalProperties: false`). This is Groq's structured-output capability referenced in the problem statement.
- The backend parses `response.choices[0].message.content` (a JSON string) through `json.loads(...)` then `NutritionAnswer.model_validate(...)`.
- **On `ValidationError`** (or invalid JSON): the request is treated as a **hard failure** — logged (Section 9), a `502`-class error returned to the client, and *no* attempt is made to regex/extract an answer from raw text. This directly satisfies the "parsing/validation failure, not prose repair" requirement.
- `source` is typed `Literal[None]` rather than `Optional[str]` deliberately for this milestone — it makes "must be null" a schema-level guarantee, not a convention. Widening it to `Optional[SourceRef]` is the only schema change needed for the future milestone; the top-level `answer`/`claims` contract is untouched.

---

## 6. Model Client Abstraction

```python
class ModelClient(Protocol):
    def get_structured_answer(
        self, system_prompt: str, history: list[Message], user_message: str
    ) -> NutritionAnswer: ...
```

- `GroqModelClient` is the concrete implementation for this milestone (JSON Schema structured output, as above), using model `openai/gpt-oss-120b` (configurable via an env var, since the same strict-mode contract holds for any Groq model that supports it).
- Isolating this behind a Protocol means: (a) the safety/validation/persistence layers are provider-agnostic, (b) swapping to Anthropic's forced tool-use or OpenAI's `response_format={"type": "json_schema", ...}` is a new class, not a rewrite, (c) the eval harness (Section 9) can inject a fake client for deterministic tests.
- The client is instantiated once per process with the API key read from environment variables (`GROQ_API_KEY`), never from request input.

---

## 7. Backend Architecture

### 7.1 Layers

```
routers/chat.py         → HTTP layer: request parsing, response codes
services/scope_guard.py → Section 8: pre-model safety check
services/conversation.py→ loads/saves conversation + message history
services/model_client.py→ Section 6
services/eval.py        → Section 9: failure logging
db/models.py             → SQLAlchemy models
db/schemas.py             → Pydantic request/response + NutritionAnswer
```

Router functions stay thin: parse request → call scope guard → call conversation service → call model client → validate → persist → return. No business logic lives in the router.

### 7.2 API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | Submit a user message, get back a structured answer or a scope refusal. |
| `GET` | `/conversations/{id}` | Fetch full message history for a conversation (used on page reload). |
| `POST` | `/conversations` | Create a new empty conversation, returns `id`. |
| `GET` | `/health` | Liveness check for Railway. |

`POST /chat` request/response:

```jsonc
// Request
{ "conversation_id": "uuid", "message": "How much protein do I need daily?" }

// Response (success)
{ "type": "answer", "answer": "...", "claims": [{ "claim": "...", "source": null }] }

// Response (scope refusal — Section 8)
{ "type": "refused", "reason": "calorie_or_weight_target", "message": "..." }

// Response (validation failure — Section 5) → HTTP 502
{ "detail": "Model response failed schema validation" }
```

### 7.3 Request sequence

```
Client → POST /chat
  → ScopeGuard.check(message)
      ├─ blocked → persist refusal record → return {type: "refused", ...}
      └─ allowed → continue
  → ConversationService.load_history(conversation_id)
  → ModelClient.get_structured_answer(system_prompt, history, message)
  → NutritionAnswer.model_validate(raw_tool_input)
      ├─ fails → EvalLogger.record_validation_failure(...) → HTTP 502
      └─ succeeds → continue
  → ConversationService.append(user_msg, assistant_msg, claims)
  → return {type: "answer", answer, claims}
```

---

## 8. Scope Restrictions — Enforced in Code

This is a dedicated module, not prompt text, and it runs **before** the model call whenever practical (per the problem statement).

### 8.1 Design

`services/scope_guard.py` exposes:

```python
class ScopeVerdict(BaseModel):
    blocked: bool
    reason: Literal["calorie_target", "weight_target", "medical_advice", None]

def check_request(message: str) -> ScopeVerdict: ...
def check_response(answer: NutritionAnswer) -> ScopeVerdict: ...
```

- **Pre-model check (`check_request`)**: pattern/keyword + lightweight classifier over the incoming message, catching explicit asks ("how many calories should I eat", "what should I weigh", "diagnose my..."). This is the primary, cheap gate and satisfies "occur before the request is allowed to proceed to the model when practical."
- **Post-model check (`check_response`)**: the same rule set re-applied to the model's `answer` text before it's returned to the user. This exists because a pre-check on the *question* cannot catch a model that volunteers a calorie number unprompted in an otherwise-in-scope answer. If the post-check trips, the response is discarded and replaced with a refusal — the assistant's actual output is never a bypass path.
- Both checks are **deterministic code**, independent of the system prompt. The system prompt (Section 9) is written to *reduce* how often the model tries, but the guard is what actually enforces the boundary, per the problem statement's explicit requirement that the prompt alone must not be considered sufficient.
- Rule implementation starts as curated regex/keyword lists per category (numbers followed by "calories"/"kcal", weight units + "should weigh"/"target weight", a medical-advice keyword list for diagnosis/prescription/dosage language) grouped by the three required categories. This is intentionally simple and auditable; false negatives are expected to surface via the failure log (Section 9) and get folded back into the rule set.
- Every refusal is persisted (`scope_refusals` table, Section 7) with the triggering message, matched category, and whether it was caught pre- or post-model — this data feeds directly into "missed scope restrictions" tracking in the failure log.

### 8.2 Refusal response

On a block, the backend returns a fixed, non-model-generated message directing the user to a qualified professional (e.g., a registered dietitian or physician), tagged with the matched category. This message is **not** generated by the LLM — it's a static template per category, so it can never itself violate the boundary it's enforcing.

---

## 9. System Prompt & Regression Testing

### 9.1 System prompt

Lives in a single versioned file, `prompts/system_prompt.md`, loaded at startup — not inlined in code, so changes are diffable and reviewable independently of application logic. It defines, per the problem statement: purpose (food/nutrition/food-safety Q&A), response behavior (answer length, how `claims` should be populated, how to phrase uncertainty), and the safety boundaries (stated for the model's benefit, understanding the guard in Section 8 is the actual enforcement).

### 9.2 Regression harness

`eval/regression_questions.json` — a fixed list (separate from, but overlapping in spirit with, the 10-question failure-log set in Section 10). `eval/run_regression.py`:

1. Loads the current system prompt.
2. Runs every regression question through the real pipeline (ScopeGuard → ModelClient → validation).
3. Writes outputs to a timestamped file under `eval/runs/<timestamp>.json`.
4. Prints a diff-style summary against the previous run (same question, prior vs. current answer) so a reviewer can eyeball "did this fix the target case without moving the others."

This is a script run manually after every system-prompt edit, per the workflow the problem statement specifies (run → review → check fix → check regressions) — it is not automated CI gating, since "did this fix the intended behavior" requires human judgment on a nutrition assistant's prose.

---

## 10. Failure Log

### 10.1 Question set

`eval/failure_log_questions.json` — 10 fixed questions, tagged by category:

```jsonc
[
  { "id": 1, "category": "nutrient_requirements", "question": "..." },
  { "id": 2, "category": "food_safety_storage", "question": "..." },
  { "id": 3, "category": "cooking_methods", "question": "..." },
  { "id": 4, "category": "no_clear_answer", "question": "..." },
  // ... total 10, covering all four categories at least once
]
```

### 10.2 Storage

`eval_runs` and `eval_findings` tables (Section 7's DB schema) persist every run so the log is a durable artifact, not a throwaway script output:

```
eval_runs(id, run_at, prompt_version, notes)
eval_findings(id, run_id, question_id, failure_type, description)
```

`failure_type` is an enum matching the five categories in the problem statement exactly: `unsupported_claim | inconsistent_number | unverifiable_source | missed_scope_restriction | unhelpful_hedging`.

### 10.3 Process

`eval/run_failure_log.py` runs all 10 questions, saves raw outputs, and a human reviewer annotates each response against the five failure types (this is manual review, not automated detection — the problem statement asks for inspection and recording, not a classifier). A companion `eval/summarize_failures.py` groups and counts findings by type and prints a table, which becomes the artifact referenced in the acceptance criteria and is checked into `docs/failure-log.md` (or exported from the DB) for review.

---

## 11. Data Model

```
conversations
  id            uuid PK
  created_at    timestamptz

messages
  id                uuid PK
  conversation_id   uuid FK → conversations.id
  role              text  ('user' | 'assistant')
  content           text        -- user text, or assistant `answer`
  created_at        timestamptz

claims
  id            uuid PK
  message_id    uuid FK → messages.id   -- only for assistant messages
  claim_text    text
  source        text NULL               -- always NULL this milestone

scope_refusals
  id            uuid PK
  conversation_id uuid FK → conversations.id
  message_text  text
  category      text   ('calorie_target' | 'weight_target' | 'medical_advice')
  stage         text   ('pre_model' | 'post_model')
  created_at    timestamptz

eval_runs
  id             uuid PK
  run_at         timestamptz
  prompt_version text
  notes          text

eval_findings
  id            uuid PK
  run_id        uuid FK → eval_runs.id
  question_id   int
  failure_type  text  (enum, Section 10.2)
  description   text
```

Storing `claims` as their own table (rather than a JSON column on `messages`) keeps the future source-population milestone to an `ALTER TABLE claims ADD COLUMN ...`-style change plus a schema widen, with no migration of historical shape.

---

## 12. Deployment

```
GitHub repo (single monorepo: /frontend, /backend, /docs, /eval)
        │
        ├── Vercel  ── watches /frontend  ── builds Next.js  ── public URL (app)
        │                    │
        │                    └─ env: NEXT_PUBLIC_API_BASE_URL → Railway backend URL
        │
        └── Railway ── watches /backend  ── builds FastAPI (Docker or Nixpacks)
                             │                ── public URL (api)
                             ├─ env: GROQ_API_KEY (secret)
                             └─ Postgres plugin (managed, same Railway project)
```

- CORS on the FastAPI app is restricted to the Vercel deployment's origin.
- The Groq API key exists only in Railway's environment — never in a `NEXT_PUBLIC_*` variable, never in client bundle.
- Local dev: `docker-compose.yml` runs Postgres; frontend and backend run via their normal dev servers pointed at `localhost`.

---

## 13. Directory Structure

```
nutrition-project/
├── docs/
│   ├── problemStatement.md
│   ├── architecture.md
│   └── failure-log.md              (produced by eval/summarize_failures.py)
├── frontend/
│   ├── app/
│   │   ├── page.tsx
│   │   └── components/
│   │       ├── ChatWindow.tsx
│   │       ├── MessageList.tsx
│   │       ├── MessageInput.tsx
│   │       ├── SourcesPanel.tsx
│   │       └── RefusalNotice.tsx
│   └── lib/api.ts                   (typed fetch wrapper to backend)
├── backend/
│   ├── main.py
│   ├── routers/chat.py
│   ├── services/
│   │   ├── scope_guard.py
│   │   ├── conversation.py
│   │   ├── model_client.py
│   │   └── eval.py
│   ├── db/
│   │   ├── models.py
│   │   └── schemas.py
│   └── prompts/system_prompt.md
├── eval/
│   ├── regression_questions.json
│   ├── failure_log_questions.json
│   ├── run_regression.py
│   ├── run_failure_log.py
│   └── summarize_failures.py
└── docker-compose.yml
```

---

## 14. Traceability to Acceptance Criteria

| Acceptance criterion (problemStatement.md §9) | Where enforced |
|---|---|
| Message list + input box | Section 4.2 |
| Empty sources panel | Section 4.2 (`SourcesPanel`) |
| Backend chat endpoint | Section 7.2 |
| Conversation storage | Section 11 |
| Model calls only server-side | Section 6, 10 (no key in frontend) |
| Structured schema w/ `answer` + `claims[].{claim,source}` | Section 5 |
| Every `source` is `null` | Section 5 (`Literal[None]`) |
| Invalid responses → hard failure | Section 5, 7.3 |
| Dedicated system prompt | Section 9.1 |
| Regression set rerun after prompt changes | Section 9.2 |
| Calorie/weight/medical advice blocked in code | Section 8 |
| Pre-model enforcement | Section 8.1 |
| 10-question failure log, 4 categories, grouped/counted | Section 10 |
| GitHub + public deployment | Section 12 |
