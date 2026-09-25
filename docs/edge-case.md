# Edge Cases — AI Nutrition Assistant Prototype

Corner scenarios to account for during implementation of [implementation-plan.md](./implementation-plan.md). Organized by phase. Each item names the scenario and the expected behavior; items without an obvious answer are flagged for a decision during that phase rather than left implicit.

---

## Phase 0 — Project Scaffolding & Environments

- **Missing/empty env vars at boot.** `GROQ_API_KEY` or `DATABASE_URL` unset → backend should fail fast at startup with a clear error, not fail lazily on the first request.
- **CORS misconfiguration.** Frontend origin not yet in the allowlist (e.g., after a Vercel preview URL changes) → requests fail with an opaque CORS error in the browser; document how to diagnose this vs. a real backend error.
- **Local Postgres not running.** `docker-compose up` not started before backend boot → connection error should be distinguishable from an application bug.
- **Port collisions.** Default FastAPI/Next.js ports already in use locally.

---

## Phase 1 — Data Model & Persistence

- **Orphaned claims.** A `claims` row whose `message_id` points at a deleted/nonexistent message — prevent via FK constraint with cascade delete, not app-level checking alone.
- **Empty `claims` list.** An assistant answer with zero claims is valid (not every answer makes a factual claim) — schema must not require `claims` to be non-empty.
- **Very long message content.** No explicit length cap in the problem statement — decide a max `content` length (DB column + request validation) to avoid unbounded storage/token costs from a single message.
- **Concurrent writes to the same conversation.** Two `POST /chat` calls on the same `conversation_id` in flight at once (double-submit from a slow UI, or a second tab) — decide whether to serialize per-conversation or accept interleaved history.
- **Non-existent `conversation_id`.** `GET /conversations/{id}` or `POST /chat` with a `conversation_id` that was never created (bad client state, stale `localStorage`, forged ID) → 404, not a 500 from a failed join.
- **Malformed UUID.** `conversation_id` that isn't a valid UUID at all → 400, not an unhandled parse exception.
- **`source` widened later.** Migration path in a future milestone (`Literal[None]` → `Optional[SourceRef]`) — confirm the Phase 1 schema doesn't bake in assumptions (e.g., a `NOT NULL` DB constraint on `source`) that would make that widen a breaking migration.

---

## Phase 2 — Model Client & Structured Output Contract

- **Model output isn't valid JSON at all.** Groq returns `response.choices[0].message.content` as non-JSON or truncated text (rare in strict mode, but possible on `max_tokens` truncation or a model that doesn't support strict mode) → must be treated as a validation/parse failure, same code path as a schema mismatch, not a silent empty answer.
- **Output is valid JSON but wrong shape.** E.g., `claims` is an object instead of a list, or `source` is `""` instead of `null` — Pydantic validation should reject all of these uniformly; don't special-case "close enough" values.
- **Model fabricates a non-null source anyway.** Directly relevant to the Core Rule — must be a hard schema validation failure (`Literal[None]` violation), not silently coerced to `null`.
- **Groq API timeout / 5xx / rate limit (429).** Distinct failure mode from a validation failure — needs its own handling (retry policy or immediate error) and must not be logged as if it were a schema failure.
- **Extremely long conversation history.** History exceeds the model's context window — decide a truncation/summarization strategy (e.g., keep last N turns) before this becomes a hard failure in production.
- **Empty or whitespace-only user message.** Reaches the model client at all? Should probably be rejected earlier (Phase 4 request validation) rather than sent to the model.
- **Non-English or mixed-language input.** Not explicitly in scope, but decide whether this is silently answered, degraded, or out of scope for the prototype.
- **Answer field is empty string but claims are non-empty (or vice versa).** Schema-valid but semantically odd — worth a regression-set case (Phase 7) rather than a code-level block.
- **Partial/streamed tool-use input.** If using streaming, ensure the full tool input is buffered and only validated once complete — don't validate a partial JSON fragment.

---

## Phase 3 — Scope Guard (Code-Level Safety)

- **Indirect/oblique phrasing.** "How many almonds equal 200 calories" or "I'm 5'6, what's a healthy number on the scale" — tests whether keyword/regex rules generalize past the exact example phrasings in the problem statement. Expect false negatives here; this is precisely what Phase 8's failure log should surface.
- **Negated or hypothetical framing.** "I'm NOT asking for a calorie target, just explain what a calorie is" — must not be blocked; a naive keyword match on "calorie target" would over-block.
- **Quoted/reported speech.** "My doctor told me to eat 1500 calories a day, is that reasonable?" — arguably in-scope (evaluating advice already given) vs. out-of-scope (still calorie-target territory) — needs an explicit team decision, not left to the regex to decide implicitly.
- **Multi-intent single message.** One message combining an in-scope food-safety question and an out-of-scope weight-target question — decide whether the whole message is blocked or only the offending part (the architecture only supports whole-message block/allow).
- **Unit variety.** "kcal" vs "calories" vs "cal", lbs vs kg vs stone, for both request and response checks — rule sets must cover all locales/units the target audience might use.
- **Post-check false positive on a legitimate in-scope number.** E.g., "a banana has about 100 calories" is informational, not a prescription — `check_response` must distinguish informational nutrition facts from a personalized target/prescription; an over-broad post-check would block most nutrient-fact answers entirely.
- **Post-check trips after a valid, already-persisted answer.** Per architecture.md §8.1 the response is discarded and replaced — confirm the discarded (blocked) answer and its claims are never written to `messages`/`claims`, only the refusal is.
- **Case sensitivity and Unicode.** "CALORIE TARGET", "calorie-target", accented characters — normalize before matching.
- **Empty message reaching the guard.** Should be rejected by request validation before `check_request` runs at all (see Phase 2 note).
- **Medical-advice keyword collateral damage.** Words like "diagnose," "prescribe," "dosage" appearing in a legitimate food-safety context (e.g., "what's a safe dosage of vitamin C from food") — decide how literal the keyword list is before it starts blocking benign questions.

---

## Phase 4 — Chat Endpoint (Wiring It Together)

- **Scope guard blocks, but persistence fails.** `scope_refusals` insert fails (DB down) after the guard already decided to block — decide whether to still return the refusal to the user (fail open on logging) or fail the request (fail closed). Given the Core Rule's "failures must be recorded, not silently patched around," logging failure should probably be visible (e.g., 500) rather than silently dropped.
- **Model call succeeds and validates, but persistence fails afterward.** The user already "has" an answer conceptually (it validated) but it's not saved — decide whether to still return it to the client (best-effort UX) or treat the whole request as failed (consistency-first). This directly affects whether a reload will show the message.
- **Request body missing `conversation_id`.** Per architecture.md §7.2 the client is expected to call `POST /conversations` first — decide whether `POST /chat` auto-creates a conversation if `conversation_id` is omitted, or hard-rejects with 400. The architecture doesn't specify this.
- **`check_response` fires after a successful DB write of the user message but before the assistant message is persisted.** Confirm the persistence step only happens once, after both scope and schema checks pass — no partial writes (user message saved, assistant message never appears) that would corrupt the conversation view.
- **Duplicate/replayed request.** Client retries a `POST /chat` after a timeout, but the first call actually succeeded server-side → conversation ends up with the same user question asked twice. No idempotency key in the current design — worth flagging even if out of scope for the prototype.
- **Very slow model call.** Client-side timeout fires before the backend responds — backend should still complete and persist the exchange so history is consistent on the next load, even though that particular response never reached the original caller.
- **502 on validation failure still needs a persisted trace.** Confirm the failed raw model output is captured somewhere for Phase 7/8 failure analysis, even though nothing goes into `messages`/`claims`.

---

## Phase 5 — Chat Frontend

- **Double-submit.** User presses Enter/click Send twice quickly — `MessageInput` must disable during the in-flight request; verify this also covers Enter-key submission, not just the button.
- **Refresh mid-request.** User reloads the page while a `POST /chat` is in flight — on reload, the optimistic user message should not reappear duplicated once history loads from `GET /conversations/{id}` (the backend's copy is authoritative).
- **`localStorage` unavailable or cleared.** Private browsing, blocked storage, or a different device — conversation ID is lost; decide the UX (start a fresh conversation silently vs. show an explicit "no history found" state).
- **Backend unreachable entirely (network error, not 4xx/5xx).** Distinct from the documented `4xx/5xx → generic error` case in architecture.md §4.3 — a fetch/network exception needs its own handling path so it doesn't crash the render.
- **Refusal vs. error rendering ambiguity.** Confirm `RefusalNotice` (policy boundary) and the generic error state (actual failure) are visually distinct enough that users don't mistake a safety refusal for a broken app, and vice versa.
- **Very long assistant answers or many claims.** `MessageList`/the claims disclosure should not break layout or become unusable with long text — decide on scroll/truncation behavior.
- **Stale `conversation_id` in `localStorage` pointing at a conversation that no longer exists** (e.g., DB reset between deploys) — `GET /conversations/{id}` 404 should be handled by starting a new conversation, not by crashing the page load.
- **Rapid successive messages before a response returns.** Confirm the UI genuinely blocks further sends rather than just visually appearing to, given `MessageInput` is "disabled while a request is in flight."

---

## Phase 6 — Deployment

- **Environment variable drift between local `.env` and Railway/Vercel dashboards.** A var renamed locally but not updated in the dashboard (or vice versa) — silent misconfiguration rather than a startup crash, since these platforms often don't validate at deploy time.
- **CORS origin mismatch after a Vercel redeploy.** Vercel preview deployments get new URLs; only the production origin is in the CORS allowlist by design — confirm preview URLs are expected to fail CORS (and that this is intentional, not a bug to chase).
- **Migrations not run against the managed Railway Postgres before the backend starts serving traffic.** First deploy (or a deploy with a new migration) needs migrations applied before/with the release, not after — a race here causes 500s on the very first requests.
- **Cold start latency.** Railway free/low tiers can sleep or cold-start — first request after idle may time out client-side even though the backend eventually responds; decide if this needs a loading-state affordance.
- **Secrets accidentally exposed via `NEXT_PUBLIC_*` prefix.** A typo'd env var name on Vercel (e.g., `NEXT_PUBLIC_GROQ_API_KEY`) would ship a secret to the browser bundle — explicit deploy-checklist item, not just a one-time spot check.
- **Database connection pool exhaustion under concurrent users.** Not covered by a single smoke test — worth noting as a known gap for a prototype, not something Phase 6 needs to fully solve.

---

## Phase 7 — Regression Suite & System Prompt Iteration

- **Non-determinism across runs.** Same question, same prompt version, different answer wording or claim phrasing between runs (LLM sampling) — the diff-style summary needs to distinguish "meaningfully changed" from "cosmetically reworded," or every run will look like a regression.
- **A prompt change that only manifests in scope-guard interaction.** E.g., a prompt tweak makes the model phrase an in-scope answer in a way that now trips `check_response` — the regression harness runs the full pipeline (ScopeGuard → ModelClient → validation) per the plan, so confirm refusals-where-none-were-expected are surfaced clearly in the diff, not just answer-text diffs.
- **Regression run partially fails.** One question in the set hits a validation failure (Phase 2) or an API error mid-run — decide whether the harness aborts the whole run or records the failure for that question and continues with the rest.
- **Comparing runs across schema changes, not just prompt changes.** If `NutritionAnswer` itself changes shape between two runs, a naive diff against the "previous run" file could be comparing incompatible structures — worth a version tag per run file.
- **Regression set silently growing stale.** Questions that stop being interesting (e.g., always trivially pass) — no automatic detection of this; a process note for periodic manual review, not a Phase 7 code requirement.

---

## Phase 8 — Failure Log (10-Question Evaluation)

- **Reviewer disagreement / subjectivity.** "Unhelpful hedging" and "unsupported claim" both require human judgment calls — two reviewers could categorize the same response differently. Since this is manual review by design (not a classifier), document the review criteria briefly so counts are reproducible-ish across reviewers.
- **A single response exhibiting multiple failure types at once.** E.g., an answer with both an unsupported claim and an inconsistent number — confirm `eval_findings` supports multiple rows per `(run_id, question_id)` rather than forcing one failure type per response.
- **Non-determinism affecting "inconsistent numbers."** This failure type is specifically about values changing across runs without a meaningful reason — needs re-running the same question multiple times to actually detect it, not just a single pass through the 10 questions. The single-pass process in Phase 8 as currently planned may not surface this failure type at all; flag for a decision (e.g., run each question 2–3× when checking specifically for this category).
- **A question that gets scope-refused instead of answered.** For the "no clear or universally accepted answer" category in particular, a refusal isn't necessarily a failure — but if a question intended to test hedging/uncertainty instead gets blocked by an overly broad scope-guard rule, that's a "missed scope restriction" in the wrong direction (over-blocking) that the current failure taxonomy (all five types) doesn't explicitly name. Decide whether over-blocking gets logged under an existing type or just noted separately.
- **`eval_runs`/`eval_findings` review happening against a moving target.** If the system prompt or scope-guard rules change between when the 10 questions are run and when a reviewer annotates the output, findings may reflect a version that's already been superseded — confirm `prompt_version` on `eval_runs` is actually captured and checked before annotating.

---

## Cross-Cutting Concerns (span multiple phases)

- **PII in stored messages.** Users may type identifying details ("I'm a 34-year-old with diabetes...") into a free-text field that gets persisted indefinitely — no retention/redaction policy specified anywhere in the problem statement or architecture; worth flagging as an open question rather than assuming it's out of scope.
- **Rate limiting / abuse.** No mention of per-user or per-IP rate limiting on `POST /chat` — a public URL (Phase 6) with an unmetered path to a paid model API is a cost-control gap worth at least naming, even if not fixed in this milestone.
- **Timezone/timestamp consistency.** `created_at` fields across `messages`, `scope_refusals`, `eval_runs` should be consistently UTC; a naive local-time default in one table but not another would silently break any cross-table ordering later.
- **Schema version skew between frontend and backend.** architecture.md §10 notes the frontend TypeScript type is "kept in sync manually or via an OpenAPI-generated client" — a manual-sync drift (backend adds a field, frontend type doesn't know about it) is a standing risk across every phase that touches the `NutritionAnswer` shape.
