# Answer layer, endpoint and guards — Phases 2.5 and 2.6

Evidence for [implementation-plan.md](./implementation-plan.md) Phases 2.5 and 2.6, as
[retrieval-calibration.md](./retrieval-calibration.md) is for 2.4. §1–§8 are 2.5, the
answer layer; §9–§12 are 2.6, the endpoint and the new scope rule.

**What 2.5 delivered:** grounded, per-document, cited answers, with grounding enforced in
code rather than asked for in a prompt. One generation call per document, a schema the
model cannot use to fabricate a source, and a validator that fails the whole response when
a citation does not resolve.

**What 2.6 added:** the endpoint those services hang off — three distinct wire types, the
personalisation rule, single-commit persistence of a multi-document turn, and the three
HTTP-shaped criteria 2.5 had to defer because it owned no route.

---

## 1. What was built

| File | Role |
|---|---|
| [`prompts/document_answer_prompt.md`](../../../backend/prompts/document_answer_prompt.md) | The per-document generation prompt, rendered with one document's passages |
| [`services/model_client.py`](../../../backend/services/model_client.py) | `answer_from_document()` + the `document_answer` structured-output schema |
| [`services/answer_synthesiser.py`](../../../backend/services/answer_synthesiser.py) | Group by document, one concurrent call each, expand citations |
| [`services/citation_validator.py`](../../../backend/services/citation_validator.py) | The three §8.3 rules — two blocking, one logged |
| [`services/corpus_catalog.py`](../../../backend/services/corpus_catalog.py) | The "what was searched" list the coverage refusal must name |
| [`db/schemas.py`](../../../backend/db/schemas.py) | The §8.2 response contract |
| [`scripts/smoke_test_answer_layer.py`](../../../backend/scripts/smoke_test_answer_layer.py) | Manual live run against Groq — costs credits, not in `pytest` |

Tests: **121 passing**, up from 68. 53 are new and cover this phase.

---

## 2. The structural guarantee, and where it is actually asserted

Per-document answering is the reason this milestone can claim "never blended". The
guarantee is that **no generation call ever sees two documents' passages** — so blending
is unrepresentable rather than discouraged.

That property is invisible in the response. A single call holding three publishers' chunks
would return a fluent, well-formed, fully cited answer and look completely fine. Every
output-shape assertion in the test file would still pass.

So the test fake records every prompt it is handed, and
`test_no_generation_call_ever_sees_two_documents_passages` reads the prompts rather than
the results: for each prompt, exactly one document's chunk ids are present and every other
document's are absent. The same check runs against the real corpus in §3.

**This is the one invariant in the phase that must not be "simplified" later.** A future
change wanting cross-document synthesis has to argue with
[architecture.md §7.2](./architecture.md) first, not slip past it.

## 2.1 The model cannot write a citation

The model emits `{claim, chunk_id}` and nothing else about the source. Publisher, year,
URL, section heading, page span and the quoted passage are all filled in by the backend
from the row retrieval already joined. A model that never writes a publisher cannot
fabricate one.

`chunk_id` is typed as a plain `str` in `ModelCitedClaim`, not `uuid.UUID`, on purpose.
Typing it as a UUID would turn a fabricated id into a *Pydantic* error, which reports as
"the model broke the contract" when the finding is "the model cited something it was never
given". Both end in a 502; only one of them is diagnostic.

### Considered and rejected: constraining `chunk_id` to an enum

Groq's strict mode uses constrained decoding, so listing the call's actual chunk ids as a
JSON Schema `enum` would make a fabricated id *undecodable* — exactly this project's
preferred kind of guarantee, and strictly stronger than checking afterwards.

Rejected for now, for two reasons that pull the same way:

- It needs a per-call response schema whose support in Groq strict mode is **unverified**,
  and verifying it costs credits to learn something that may simply not hold.
- It moves the guarantee into the vendor's decoder, where a regression is invisible to us.
  [`services/citation_validator.py`](../../../backend/services/citation_validator.py) runs
  in our code, is tested, and fails loudly.

Worth revisiting if strict-mode enums are confirmed — as a second layer, not a replacement.

---

## 3. Measured against the real corpus

Retrieval → grouping → prompt rendering, run against the seeded 103-chunk corpus. **No
generation call, so this cost nothing.** `k = 8`, `floor = 0.69`.

| Question | Above floor | Documents | Largest prompt |
|---|---|---|---|
| How much free sugar should I eat per day? | 5 | **3** — WHO 0.833, Ireland 0.713, DGA 0.696 | 6,319 chars (~1,580 tok) |
| Can I reuse oil I have already fried in? | 7 | **2** — FSSAI 0.796, Ireland 0.699 | 11,249 chars (~2,810 tok) |
| What should I do with used cooking oil? | 7 | **2** — FSSAI 0.834, Ireland 0.711 | 11,249 chars (~2,810 tok) |
| Best creatine dose for muscle gain? | **0** | 0 — gate 1, no model call | — |

Three things this establishes, none of which was certain beforehand:

1. **Fan-out is 2–3 calls**, matching [architecture.md §7.2](./architecture.md)'s estimate.
   The worst case is bounded by `k = 8`, and in practice one document supplies most of the
   hits — FSSAI contributes 6 of 7 on the oil question.
2. **Prompt size is not a problem.** The largest rendered prompt is ~2,810 tokens against
   a 16,000-token completion budget.
3. **The cross-document question is only nominally cross-document.** Both oil questions
   put the Irish food pyramid barely over the floor at 0.699/0.711, on a document with
   nothing to say about reusing oil. It should decline. That is gate 2 earning its keep,
   and it is precisely the behaviour the 0.02-wide separation band predicted would start
   mattering ([retrieval-calibration.md §4](./retrieval-calibration.md)).

The §2 isolation property was re-checked on every prompt rendered above, against real
chunk ids rather than fixtures: holds for all of them.

---

## 4. Blocking and non-blocking — the line that is the design

[architecture.md §8.3](./architecture.md) lists three rules. Which ones stop a response is
the whole point, and both directions of getting it wrong are one-line edits, so both have
a test.

| Rule | Blocking? | Why that way |
|---|---|---|
| Cited id was in this call's context | **Yes** | This is what makes "model knowledge is not a source" enforceable instead of aspirational |
| Non-empty answer has ≥ 1 claim | **Yes** | The schema forces every claim to be cited; it cannot force the answer to have claims |
| Low lexical overlap with the cited chunk | No — logged | Paraphrase is legitimate. A gate here would reject correct answers |
| Claim states a quantity the cited chunk does not | No — logged | A document can state a percentage a claim legitimately restates in grams — which is exactly the case a human should look at |

**There is no repair path, deliberately.** Dropping the offending claim and shipping the
rest would leave prose whose supporting claim had quietly vanished — an uncited assertion,
the exact thing the schema was shaped to prevent. Rewriting the citation would mean the
backend choosing a source for a claim it did not write. Both are worse than an error.

### Two judgement calls inside that line

**`answers_question: true` with an empty answer is a hard failure.** It could have been
re-read as a decline. It is not, because a decline is what `answers_question: false` is
for, and silently reinterpreting the field would hide a model that cannot use the one field
gate 2 depends on. Since the quarantine narrowed the floor's band to 0.02, that gate is
carrying most of the weight — a model misusing it has to be visible.

**`answers_question: false` carrying an answer anyway is logged, not fatal.** The opposite
call, and the reason is the user: the content is discarded either way, so nothing
ungrounded can reach them, and a 502 here would cost them an answer that another document
in the same turn may well have given. The contradiction is worth seeing; it is not worth
the response.

### The quantity check

Not in the plan, added because it is ten lines and it is the sharpest signal available for
`inconsistent_number` — one of the three Phase 1 findings retrieval was supposed to fix
([../../failure-log.md](../../failure-log.md)). A claim naming 50 g whose cited passage
never mentions 50 is either arithmetic the document did not do or a number from somewhere
else. Separators and trailing zeros are normalised, so `1,500` and `1500` do not flag —
a warning that cries wolf stops being read.

---

## 5. The defect found by reading a rendered prompt

**Every prompt carried its passages twice.** Found the same way as the Phase 2.1 heading
bug and the Phase 2.4 block-order bug: not by running the tests, which passed, but by
printing one rendered prompt and reading it.

The prompt file opened with a maintainer comment explaining that `{{DOCUMENT}}` and
`{{PASSAGES}}` are the substitution placeholders. `str.replace` is global. So the
placeholders *named in the comment* were substituted too: the document description and the
entire passages block were spliced into the middle of the comment, and then again at their
intended positions.

**Nothing failed.** No placeholder was left over, so the test asserting that
(`test_rendered_prompt_leaves_no_substitution_token_behind`) passed. The prompt was
well-formed, every chunk id the model needed was present, and it read plausibly — it just
said everything twice.

| Largest rendered prompt | chars |
|---|---|
| As first written — passages doubled, comment sent | 19,260 |
| Duplication fixed | 12,195 |
| Maintainer comment also stripped | **11,249** |

The doubling alone was costing 58% more prompt on every call, two or three times a turn.

Two fixes, because they protect against different mistakes:

- **HTML comments are stripped before the prompt is sent.** They are notes to whoever edits
  the file, not instructions to something answering from it — and this prompt goes out two
  or three times per turn, so a 900-character comment is paid for repeatedly. This also
  makes the original mistake harmless.
- **`_assert_single_placeholder` raises at import** if either placeholder appears any
  number of times other than exactly once. Zero occurrences would produce a prompt with no
  passages in it, which reads as "the document doesn't answer this" rather than as a bug.
  Two occurrences are what happened. Both now fail at startup rather than in a milestone.

The generalisable part is the one this project keeps relearning: **the defect was invisible
in the code and visible in the data.** Three phases, three defects of exactly this shape.

---

## 6. The prompt's precedence problem, and why it is not fixed here

`answer_from_document` sends two system messages: `system_prompt.md`, then the rendered
per-document prompt.

`system_prompt.md` still carries Phase 1's rule *"Leave `source` as `null` for every
claim"* and *"Don't name a specific organization… as the source of a claim"*. **Phase 2
inverts exactly that rule**, and the response schema this phase sends requires a
`chunk_id` on every claim. The two prompts contradict each other.

That rule is removed in Phase 2.8, which cannot happen earlier: it was added to fix three
`unverifiable_source` findings, and removing it requires a full regression run of its own
([../../eval.md §2.3](../../eval.md)). Until then the conflict is handled by stating
precedence explicitly — the document prompt declares that it wins on conflict, and it is
positioned second — and by scoping that override as narrowly as possible:
`system_prompt.md`'s safety boundaries are explicitly **not** superseded.

This is a prompt papering over a prompt, which is the kind of thing
[architecture.md §1](./architecture.md) exists to avoid. It is temporary by design, and
2.8 is where it is paid off. Recorded here so it is not discovered as a surprise then.

---

## 7. Exit criteria

| Criterion | |
|---|---|
| A question answerable by one document returns one `DocumentAnswerOut` with cited claims | **[x]** `test_single_document_question_returns_one_answer_with_cited_claims`, `test_citation_is_expanded_by_the_backend_not_taken_from_the_model` |
| A cross-document question returns separate answers with separate citations, no claim mixing two documents | **[x]** `test_cross_document_question_returns_separate_answers`, `test_each_claim_cites_only_its_own_documents_chunks`, `test_no_generation_call_ever_sees_two_documents_passages` |
| A model response citing a chunk id not in its context → HTTP 502 | **[x]** `CitationError` raised, verified five ways at the service layer; mapped to 502 at the endpoint in 2.6 (§11) |
| A response with a non-empty answer and empty `claims` → HTTP 502 | **[x]** `test_non_empty_answer_with_no_claims_is_a_hard_failure` + `test_non_empty_answer_with_no_claims_returns_502` |
| Every document returning `answers_question: false` → not-in-corpus refusal naming the **full** corpus | **[x]** `test_all_documents_declining_is_not_an_answer`, and at the endpoint `test_coverage_refusal_names_the_full_corpus_not_just_what_scored_well` |
| Lexical-overlap warnings logged and non-blocking | **[x]** `test_low_lexical_overlap_warning_does_not_stop_the_answer`, `test_lexical_overlap_warns_without_blocking`, plus the quantity check |

### 7.1 Three of these were deferred when 2.5 shipped

They are phrased as HTTP outcomes, and 2.5 owns no HTTP route. The plan's own deliverable
lists draw that line: 2.5 lists four service files, 2.6 lists `routers/chat.py` with "three
response types… persistence in a single step", and the dependency summary says 2.7 needs
the response *shape* rather than a working backend.

So the behaviour was built and tested at the layer that produces it, and the status-code
mapping landed in the commit that wrote the endpoint. All three are closed in §11. The same
is true of the persistence question 2.5 raised and did not answer — how a multi-document
turn is stored — which §9 settles.

---

## 8. What this does not establish

**No real generation call was made.** Every assertion here is either structural (verified
with a recording fake) or measured on retrieval and prompt rendering (verified against the
real corpus, free). What a live model actually does with this prompt — whether it cites the
passage its claim came from, whether it uses `answers_question` honestly, whether it copies
a UUID without transcribing it wrong — is **unverified**.

That was a deliberate choice rather than an omission: it costs credits, and Phase 2.10
measures it properly over the ten fixed questions with the whole nine-type failure taxonomy
applied. [`scripts/smoke_test_answer_layer.py`](../../../backend/scripts/smoke_test_answer_layer.py)
is there to do it in one command whenever it is worth the spend, and its docstring says
what to read in the output.

Three specific things to watch for when it is run:

- **UUID transcription.** The model must copy a 36-character chunk id back exactly. A
  mis-typed id is indistinguishable from a fabricated one and produces a 502. If this turns
  out to be common, the fix is a short readable label in the prompt rather than a weaker
  check — `chunk_key` (`fsanz-temperature-control-2002:15`) already exists for this.
- **Whether `answers_question: false` is used at all.** A model that answers everything
  turns gate 2 into decoration, and gate 2 is the stronger gate by design.
- **Over-refusal** on the Irish pyramid's borderline hits. Declining the oil question is
  right; declining the free-sugars question it genuinely contributes to would be
  `over_refusal`.

---

**Everything below is Phase 2.6 — the endpoint, the personalisation rule, and the three
criteria above that needed a route to close.**

## 9. `messages.ordinal`, and the bug that was waiting

2.5 left one question open: a turn now produces one answer **per document**, and
`messages.content` is a single text column. Three options were on the table.

| Option | Why not / why |
|---|---|
| Concatenate the prose into one assistant message | Loses which sentences came from which publisher. That is §7.2's blending, reintroduced at the persistence layer, and it breaks 2.7's "two documents render as two blocks" on reload |
| A `document_answers` JSONB column | Replays trivially, but puts publisher, year and URL back into a blob — the denormalised citation architecture.md §6.1 specifically ruled out, because it can drift from the corpus |
| **One assistant message per document answer** | **Chosen.** `messages.content` keeps meaning exactly what §6 says it means, and the claim→chunk→document join already gives the grouping |

Choosing the third exposed a defect that would have shipped silently.

**`created_at` cannot order a turn.** `Message.created_at` is `server_default=func.now()`,
and in PostgreSQL `now()` is the *transaction* timestamp — constant for every row in a
transaction. architecture.md §10 requires the whole turn in one commit, so **every message
in a turn gets an identical timestamp**, the user's included. The relationship ordered by
`created_at`, so on reload a turn's messages would come back in arbitrary order: the
strongest-document-first ordering lost, and nothing even guaranteeing the user's question
came before its answer.

Nothing about this would have failed. It would have presented as "the history sometimes
renders out of order", months later, on a multi-document turn.

The fix is migration `c3d9a51e7b42`: `messages.ordinal` with
`UNIQUE (conversation_id, ordinal)` — the same pattern `chunks` already uses for the same
reason. Ordering is data now; `created_at` is only ever "when".

`test_ordinals_survive_a_single_transaction_where_timestamps_cannot` asserts the premise
(`len({m.created_at for m in messages}) == 1`) as well as the behaviour, so if Postgres or
the commit boundary ever changes, the test says the premise moved rather than quietly
passing for a new reason.

### 9.1 What is deliberately not stored

**`messages.document_id`.** The document is derived from the message's claims via
`chunk_id`. Storing it as well would create a second path to the same fact that can
disagree with the citation — exactly the drift §6.1 avoided by making the citation a
foreign key rather than a string. It is *always* derivable because citation-validator rule
2 forbids a non-empty answer with no claims, so rule 2 turns out to buy the read model as
well as the grounding guarantee.

### 9.2 The backfill was verified against real rows

The local `messages` table is empty, so the backfill would have been vacuously verified —
the same gap Phase 2.3 recorded against its legacy-row criterion. Instead: six Phase-1-shaped
messages were inserted across two conversations, **out of chronological order on purpose**,
the migration was rolled back and re-applied, and the result read back.

```text
conversation 1: [(0,'user','U0'), (1,'assistant','A1'), (2,'user','U2'), (3,'assistant','A3')]
conversation 2: [(0,'user','X0'), (1,'assistant','X1')]
```

Ordinals follow `created_at`, not insertion order, and partition per conversation. The
backfill is correct for Phase 1 data because Phase 1 committed each message separately and
therefore gave them distinct timestamps — which is precisely the property Phase 2 gives up.

## 10. The personalisation rule, and the half that matters more

architecture.md §9.1's new category: *population-level guidance stays population-level.*
Retrieval is what makes this live rather than theoretical — the corpus is full of sentences
like "adults should limit free sugars to less than 10% of total energy intake". Restating
that is **correct**. The violation is converting it: *"so you should keep your sugar under
50 g."*

**The discriminator is second person, not the quantity.** This is the whole design, and the
obvious rule gets it wrong: "prescriptive cue + a number" would refuse the corpus's own
phrasing, because population guidance is itself written prescriptively. Every pattern
therefore requires an explicit second-person marker — the one thing the document never says
and a personalised restatement always does.

Two narrowing decisions, both to avoid `over_refusal`:

- **Intake units only** (`%`, `g`, `mg`, `kcal`, portions, servings…). Times, temperatures
  and durations are excluded, because second-person food-safety instruction is normal
  guidance phrasing: *"refrigerate leftovers within two hours"* is how the guidance is
  written. Including `hours` would have turned most of the 23-page FSANZ document into a
  refusal — on the half of the corpus that exists to answer storage questions.
- **`calorie_target` is matched first.** "You should eat 1800 calories" trips both rule
  sets; the calorie category is the more specific finding and the one Phase 1's eval history
  is recorded against, so the label must not change under it.

**`check_response()` deliberately did not gain the new rule.** It is now used only by
`eval/run_regression.py` and `eval/run_failure_log.py`, which are the recorded baseline
Phase 2.8 reads its outcome-type flips against. Widening what that baseline blocks would
make 2.8's comparison a moving target. The grounded path uses `check_document_answer()`.

Eight paired non-blocking cases are tested alongside the six blocking ones, because a
regression in the non-blocking direction is the expensive one: it would refuse correct
answers and read as a corpus-coverage problem.

## 11. Verified end to end against the real corpus

`POST /chat` with **real retrieval** — the embedding model loads, the query is embedded, the
corpus is scanned, the floor applied, documents grouped, citations validated against real
chunk ids, the turn persisted and reloaded. Only the model is faked, so this cost nothing.

| Question | Result | Model calls |
|---|---|---|
| How much free sugar should an adult eat per day? | `answer`, **3 documents** (WHO, Ireland, DGA) | 3 |
| Can I reuse oil I have already fried food in? | `answer`, **2 documents** (FSSAI, Ireland) | 2 |
| What is the best creatine dose for muscle gain? | `not_in_corpus`, searched **7** | **0** — gate 1 |
| How many calories should I eat to lose weight? | `refused`, `calorie_target` | **0** — pre-model gate |

Reload of that conversation:

```text
[0] user       -                                claims=0
[1] assistant  World Health Organization        claims=1
[2] assistant  Department of Health (Ireland)   claims=1
[3] assistant  USDA & HHS (United States)       claims=1
[4] user       -                                claims=0
[5] assistant  FSSAI (India)                    claims=1
[6] assistant  Department of Health (Ireland)   claims=1
```

Contiguous ordinals across two turns, every assistant block keeping its own document, every
claim keeping its citation. The WHO block's citation reports `page_from=0` — *not
paginated*, the HTML source — rather than claiming page one.

Two things this run also confirmed incidentally: the lexical-overlap warning fired on every
claim (the fake model's generic claim text shares no vocabulary with its chunk) and **every
response was still HTTP 200** — non-blocking, as specified. And both zero-model-call rows
are the gates saving credits, not an absence of behaviour.

### 11.1 The three criteria 2.5 deferred

| 2.5 criterion | Closed by |
|---|---|
| Citing a chunk id not in context → 502 | `test_citing_a_chunk_not_in_context_returns_502_and_persists_nothing` — cites a **real** chunk from a document that call was never given, which is the failure mode an invented-id test misses |
| Non-empty answer with empty `claims` → 502 | `test_non_empty_answer_with_no_claims_returns_502` |
| All documents decline → refusal naming the full corpus | `test_coverage_refusal_names_the_full_corpus_not_just_what_scored_well`, which also asserts the corpus has more than one document so it cannot pass vacuously |

`CitationError` and `ModelResponseError` are both 502 with **different `detail` strings**, so
an operator can tell "cited something it wasn't given" from "broke the schema" without
reading the log.

## 12. Exit criteria — Phase 2.6

| Criterion | |
|---|---|
| Out-of-scope refuses **before** any embedding or retrieval call, asserted via a spy | **[x]** `get_searcher` is an injected seam; `_SpySearcher.called is False`. Also asserted with the searcher primed with a 0.95 hit it must never consult |
| A calorie figure in the corpus does not let a calorie-target question through | **[x]** `test_a_calorie_figure_in_the_corpus_does_not_let_a_calorie_question_through` |
| An answer converting population guidance into "you should…" is blocked and never persisted | **[x]** Blocked, `50 g` absent from the response body, zero `Message` rows, one `post_model` `scope_refusals` row |
| The two refusals are distinct wire types | **[x]** `not_in_corpus` carries `searched` and no `reason`; `refused` carries `reason` and no `searched` |
| No partial writes on any failure path | **[x]** Tested by breaking it — a citation to a nonexistent chunk fails the flush and the user message does not survive |
| `GET /conversations/{id}` returns history with grouped, cited claims | **[x]** `test_history_reloads_as_grouped_cited_blocks`, plus the live reload in §11 |

Tests: **168 passing**, up from 121. `eval/run_retrieval_eval.py` re-run after the
`apply_floor` refactor: floor band 0.68–0.70, midpoint 0.690, latency median 8.9 ms —
unchanged, so the refactor was behaviour-neutral.

### 12.1 Still not done, and still not this phase's job

- **No live Groq call.** Unchanged from §8. `scripts/smoke_test_answer_layer.py` is the
  one-command live run.
- **`system_prompt.md` still contradicts the response schema** (§6). Phase 2.8.
- **`RETRIEVAL_K` / `RETRIEVAL_FLOOR` are not yet environment variables.** The endpoint uses
  the measured constants. Phase 2.9 lists them as Railway variables; until then the
  measurement *is* the configuration, which is the safer of the two defaults.
- **A coverage refusal cannot be traced to its conversation.** `retrievals` reaches a
  conversation only through `message_id`, which is null for a coverage refusal. Recorded in
  `persist_coverage_refusal`'s docstring rather than fixed: it is another migration, and
  `query_text` plus `created_at` is enough to find one in practice. Worth closing if the
  Phase 2.10 failure log needs to count over-refusals per conversation.
