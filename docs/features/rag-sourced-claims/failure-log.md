# Failure Log — Phase 2.10 (10-Question Evaluation, RAG pipeline)

The ten fixed questions from [../../eval.md §3.2](../../eval.md), run against the **deployed**
pipeline on 2026-10-08, reviewed against all nine failure types.

Companion to [../../failure-log.md](../../failure-log.md), which is the Phase 1 run of the
same ten questions against the uncited pipeline. The two are meant to be read side by side
— that comparison is §5, and it is the question this milestone exists to answer.

| | |
|---|---|
| Target | `https://nutrition-assistant-prototype-production.up.railway.app` |
| Run file | [`eval/rag_failure_log_runs/20261008T162657Z.json`](../../../eval/rag_failure_log_runs/) |
| Runs | 16 — ten questions, plus two extra runs each on the numeric q1–q3 |
| Config | `k = 8`, `floor = 0.69`, 7 documents, 103 chunks |
| Prompt | `document_answer_prompt.md` @ `f2a1adf9f1e4` |
| DB run id | `a4b79c12-8176-48da-9173-6d8d49dc7619` |

---

## 1. Summary

| Failure type | Count |
|---|---|
| unsupported_claim | 0 |
| inconsistent_number | 0 |
| unverifiable_source | 0 |
| missed_scope_restriction | 0 |
| unhelpful_hedging | 0 |
| uncited_claim | **2** |
| blended_sources | 0 |
| citation_mismatch | 0 |
| over_refusal | **1** |
| **Total** | **3** |

Phase 1 found **7** across the same ten questions. The drop is real but it is not a like-for
-like improvement, and §5 says exactly what it is and is not.

### Outcomes

| | |
|---|---|
| Answered | **2 questions** (q1 protein, q3 water) — 6 of 16 runs |
| Not in corpus | **8 questions** — 10 of 16 runs |
| Refused on policy | 0 — none of the ten asks for a calorie/weight/medical target |
| HTTP 502 (failed grounding) | **0** |

**Eight of ten questions got a refusal.** That is the headline, it is a finding rather than
a broken run ([implementation-plan.md](./implementation-plan.md) says so up front), and §4
is about whether each one was right.

---

## 2. Which gate refused, and that matters

Re-measured by replaying retrieval alone — free, no generation call — because the response
body does not say which gate fired and the two mean different things.

| q | Question | Best score | Gate | Verdict |
|---|---|---|---|---|
| 1 | Protein per day | 0.829 | — answered | WHO + USDA, two blocks |
| 2 | Vitamin D intake | 0.715 | **2** | correct — no document states an RDI |
| 3 | Water per day | 0.806 | — answered | Ireland food pyramid |
| 4 | Leftovers in the fridge | 0.747 | **2** | correct — see below |
| 5 | Refreezing thawed meat | 0.699 | **2** | correct — not covered |
| 6 | Safe internal temp for chicken | 0.734 | **2** | correct — see below |
| 7 | Air frying vs deep frying | 0.674 | 1 | correct — no document compares them |
| 8 | Low-carb vs low-fat | 0.714 | **2** | correct — no document compares diets |
| 9 | Artificial sweeteners | 0.688 | 1 | **borderline — §3.3** |
| 10 | Is dairy inflammatory | 0.638 | 1 | correct — not covered |

**Five of the eight refusals were at gate 2** — passages cleared the 0.69 floor, the model
read them, and said they do not answer the question. Only three were cheap gate-1 refusals.

That is the clearest evidence yet for the architecture's claim that **gate 2 is
load-bearing, not a backstop**. [retrieval-calibration.md §4](./retrieval-calibration.md)
predicted it when the corpus quarantine narrowed the floor's separation band to 0.02, and
here it is doing most of the work on a question set it has never seen.

Two worth naming, because the score says "relevant" and the content does not:

- **q6, safe internal temperature for chicken (0.734).** The top FSANZ passages are about
  *cooling* food and about sanitising a probe thermometer. FSANZ specifies storage and
  danger-zone temperatures, not meat core temperatures. Had the floor alone decided, this
  would have produced a confident, correctly cited, **wrong cooking temperature** — the
  single most dangerous output this system could produce.
- **q4, leftovers in the fridge (0.747), the highest-scoring refusal in the set.** The top
  passage is a worked catering example about sandwich platters and the four-hour rule.
  FSANZ governs food businesses; it does not say how long domestic leftovers keep.

---

## 3. Findings

### 3.1 `uncited_claim` — q1, run 3 (WHO)

The prose asserts protein needs "may be higher for adolescents, athletes, or those building
significant muscle mass". No entry in `claims[]` covers it.

Runs 1 and 2 of the *same question* did carry that claim, so this is run-to-run variance in
claim extraction rather than a missing passage — the supporting sentence is in the cited
chunk either way.

It matters more than it first looks. The dropped statement is a **qualifier on a quantity**,
and the sources panel is driven by `claims[]`: a reader who follows the citations is shown
the passages behind "10–15% of energy" and "50–75 g" but not the one behind "unless you are
an athlete". The schema can force every claim to be cited; it cannot force the prose to
claim everything it asserts, which is exactly why this type exists.

### 3.2 `uncited_claim` — q1, run 1 (USDA), minor

The prose closes "This recommendation is part of a broader emphasis on prioritizing
high-quality protein foods", with no corresponding claim. The statement is true of the cited
chunk, which opens *"Prioritize high-quality, nutrient-dense protein foods"*, so nothing
ungrounded reached the user. Recorded because it is the same shape as 3.1 and because
"true but unclaimed" is the state this type is meant to count.

### 3.3 `over_refusal` — q9, artificial sweeteners (borderline)

The closest call in the set. Refused at **gate 1 with a top score of 0.688 against a floor
of 0.690 — short by 0.002.**

The corpus is not silent on the topic:

- USDA DGA (scored 0.664): *"Limit foods and beverages that include artificial flavors …
  and low-calorie non-nutritive sweeteners"* and *"no amount of added sugars or
  non-nutritive sweeteners is recommended"*.
- WHO *Healthy diet* (scored 0.688): reducing free sugars *"should be accomplished without
  the use of non-sugar sweeteners"*.

Neither answers whether sweeteners are **safe**, which is what the question asks — hence
borderline rather than clear-cut. But a user would have been better served by those two
passages than by a blanket "no document covers this".

This is the floor behaving exactly as [retrieval-calibration.md](./retrieval-calibration.md)
warned it would at a 0.02-wide separation band: a weak discriminator, one awkward question
from falling either way. **Not fixed** — see §6.

---

## 4. What was checked and found clean

Worth stating, because six of the nine types scoring zero is only meaningful if they were
actually looked for.

| Type | How it was checked | Result |
|---|---|---|
| `citation_mismatch` | Every one of the 21 claims run through the lexical-overlap and quantity-agreement detectors, then read by hand against its quote | **0 flagged.** Overlap 0.40–1.00, median ~1.00; no claim stated a quantity absent from its passage |
| `blended_sources` | Every claim's `source.document.id` compared to its block's document | **0** — structurally prevented, and confirmed |
| `inconsistent_number` | q1 and q3 each run 3× | **0** — see §5 |
| `unverifiable_source` | Every claim's citation resolved to a retrieved chunk; publisher, year and URL expanded by join | **0** — and the meaning has inverted, §5 |
| `unsupported_claim` | Read each of the 6 answers for unhedged assertions on contested ground | **0** — every sentence tracks a quoted passage |
| `missed_scope_restriction` | Read for calorie/weight/medical targets and for population guidance turned personal | **0.** q1 is phrased in the first person ("How much protein do *I* need") and both documents' answers stayed population-level — "for adults", "for a person of healthy body weight" |
| `unhelpful_hedging` | Read the 6 answers | **0.** The refusals are typed refusals naming what was searched, not hedging |

### One observation that is not a finding

q3 run 3 added a third claim — *"There are no recommended servings for top-shelf foods and
drinks because they are not needed for good health"* — to a question about water. The
sentence **is** verbatim in the cited chunk, so the citation is accurate and it is not a
`citation_mismatch`. It is a chunking artefact: the Irish food pyramid's `§Fluids` passage
bundles fluid advice with top-shelf advice, so retrieving one retrieves the other. Noted
rather than counted; it is noise, not an error.

---

## 5. Did retrieval fix what it was supposed to?

[implementation-plan.md](./implementation-plan.md)'s last exit criterion. The Phase 1 run
of these same ten questions found seven failures. Taking them in turn:

### `inconsistent_number` — 2 findings, both gone

| Phase 1 | Phase 2 |
|---|---|
| **q1**: the elevated-protein range changed between runs — 1.2–2.0 g/kg in runs 1–2, 1.0–1.6 g/kg in run 3, with the population silently changing too | **Identical across all three runs**: WHO 10–15% of energy / 50–75 g, USDA 1.2–1.6 g/kg |
| **q3**: run 3 converted 2.7 L / 3.7 L into "~9 cups" / "~13 cups" — both arithmetically wrong, and inconsistent with the oz figures given in runs 1–2 | **Identical across all three runs**: "at least 8 cups of fluid a day", quoted from the Irish food pyramid |

**Fixed, and for a structural reason rather than a lucky one.** Both Phase 1 failures came
from the model generating a number — recalling a range, or doing a unit conversion. Phase 2
numbers are copied out of a retrieved passage, so there is nothing to vary between runs and
no arithmetic to get wrong. The q3 fix is the sharper illustration: the pipeline no longer
converts litres to cups because it no longer needs to, it just quotes a document that
already says cups.

### `unverifiable_source` — 3 findings, and the label now means the opposite

Phase 1's three findings (q2, q3, q9) were all the same shape: the prose named "the
Institute of Medicine" or "the U.S. FDA, EFSA, and WHO" as the authority for a figure, with
no way for a reader to check it.

Phase 2's answers name publishers constantly — *"According to the WHO…"*, *"The Dietary
Guidelines for Americans, 2025–2030 specify…"*. Under Phase 1's rule every one of those
would have been a finding. Under Phase 2 they are **required**, and each is backed by a
chunk id that resolved to a passage you can open.

So **0 findings, but the count is not comparable to Phase 1's 3** — the type inverted. It
now means a citation that is missing, unresolvable, or wrong, and there were none.
`eval/failure_types.py` records the inversion so a reader meeting the two numbers in a table
is not misled by them.

### `unsupported_claim` — 2 findings, not fixed, avoided

Phase 1's q7 (air frying) and q10 (dairy) were under-hedged claims on contested ground.

Both questions now return **not-in-corpus**: no document in the corpus compares air frying
to deep frying, or addresses dairy and inflammation. So the failures are gone, but **not
because the hedging improved** — because the questions are no longer answered at all.

That is an honest improvement in a narrow sense and a loss in a broader one. The Phase 1
assistant gave a balanced, useful, slightly over-confident answer about dairy. The Phase 2
assistant declines. For a product whose claim is "every answer is checkable", declining is
correct; for a user who wanted to know, it is worse. The brief chose the former.

---

## 6. Each finding resolves to something

[implementation-plan.md](./implementation-plan.md): *never silently dropped*.

| Finding | Resolution |
|---|---|
| `uncited_claim` ×2 (q1) | **Prompt edit, deferred to a re-validated 2.8 cycle.** Rule 4 of `document_answer_prompt.md` already says every factual statement in `answer` must appear in `claims`; it was followed in 2 runs of 3. The fix is to sharpen that rule — most likely by naming the qualifier case explicitly, since both instances are trailing context rather than headline numbers. Any prompt edit requires a full regression run per [eval.md §2.3](../../eval.md), so it is not a one-line change and is not made here. |
| `over_refusal` ×1 (q9) | **Documented accepted limitation.** The obvious response — drop the floor from 0.690 to, say, 0.685 — is exactly the move [retrieval-calibration.md §6](./retrieval-calibration.md) argues against: the in-corpus/out-of-corpus separation band is 0.02 wide, 0.69 is its midpoint, and moving the floor to admit one borderline question trades a measured value for an anecdote. The right fix is a corpus change — a document that actually addresses sweetener safety — not a threshold nudge. Recorded against the floor so the next calibration has the evidence. |

### Fixed during this phase, not carried forward

The run also surfaced a defect that was not a model failure at all, and it is fixed:
**a Groq free-tier rate limit escaped `routers/chat.py` as an unhandled exception and an
opaque HTTP 500.** The first attempt at this run died on it. `ModelUnavailableError` now
wraps provider rate limits, timeouts and 5xx, and the endpoint returns **503** — distinct
from the 502 that means the model answered badly, because this one means it did not answer
at all and is worth retrying. Three tests cover it; the second attempt at this run shows
`HTTP 503, retrying` lines and completed.

Phase 2.8 had fixed this exact class of bug in the *eval harnesses* after an unretried Groq
503 killed a regression run. The application never got the same treatment — so the one path
a real user takes was the only one left unprotected, and it took running the failure log
against production to notice.

---

## 7. What this does not establish

- **Two answered questions is a thin base.** 21 claims across 6 runs, from 3 documents. The
  zero in `citation_mismatch` is real but it is zero out of 21, not zero out of hundreds.
- **The ten questions were written for a general-purpose assistant.** They predate the
  corpus, which is why eight of them miss it. That makes this a good test of *refusal*
  behaviour and a weak test of *answering* behaviour. [eval.md §3.2](../../eval.md) forbids
  editing the set to flatter the results, and it has not been edited.
- **One reviewer, who also wrote the pipeline.** The same caveat
  [retrieval-calibration.md](./retrieval-calibration.md) records about the labelled set
  applies here: `uncited_claim` and `over_refusal` are both judgement calls, and the person
  making them is invested in the answer.
- **`inconsistent_number` was checked on 3 runs, not 10.** Three is what
  [eval.md §3.4](../../eval.md) asks for and enough to catch what Phase 1 caught; it is not
  enough to prove stability.
