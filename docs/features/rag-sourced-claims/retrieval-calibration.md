# Retrieval calibration — Phase 2.4

Evidence for the Phase 2.4 exit criteria in [implementation-plan.md](./implementation-plan.md).
This phase exists to replace a guessed number with a measured one: **the relevance floor is the
not-in-corpus refusal** ([architecture.md §7.3](./architecture.md)), so leaving it at the `0.30`
placeholder would have meant every later wrong answer had two candidate causes.

Reproduce with:

```bash
backend/.venv/bin/python eval/run_retrieval_eval.py
```

No generation call, no Groq credits. One embedding per question, reused across the sweep.

---

## 1. The labelled set

[`eval/retrieval_set.json`](../../../eval/retrieval_set.json) — 26 questions: **20 in-corpus** with
hand-labelled relevant `chunk_key`s, **6 out-of-corpus** with deliberately empty labels.

The out-of-corpus questions are not filler. They are the only way to measure where the floor has to
sit: the best score any of them reaches is the number the floor must clear. They are spread on
purpose from the obviously unrelated ("capital of France") to the adjacent-but-absent ("recommended
dose of metformin"), because a floor tuned only against easy negatives is not calibrated at all.

**Labelling rule:** a chunk is relevant if it *contains the guidance that answers the question* —
not if it is merely on the same topic. A chunk that mentions sugar is not relevant to "how much free
sugar" unless it states a limit.

> **These labels are one person's judgement and should be reviewed.** Every number below inherits
> them. The honest caveat is that the labeller also wrote the retriever.

## 2. recall@k

| k | recall | questions with every label found |
|---|---|---|
| 1 | 0.500 | 4/20 |
| 3 | 0.825 | 14/20 |
| 5 | 0.917 | 16/20 |
| **8** | **0.975** | **19/20** |
| 10 | 0.975 | 19/20 |

Recall rather than precision, because the two errors are not symmetric: the answer layer reads every
chunk it is given and can decline via `answers_question`, so a spare chunk costs tokens. A *missing*
chunk is unrecoverable — nothing downstream can cite a passage retrieval never returned.

**`k = 8` is the knee.** Recall is identical at 10, so a larger `k` buys only context. This confirms
the value [architecture.md §7.1](./architecture.md) had assumed.

### The one genuine miss

`q02 "What temperature should a fridge keep food at?"` — labelled relevant: FSANZ `:6` and `:8`.
Retrieval returns `:8` at rank 1 (0.834) and **all ten hits are FSANZ temperature chunks**, but `:6`
is not among them.

`:6` is the statutory definition — *"(a) 5 C, or below if this is necessary to minimise the growth of
infectious or toxigenic micro-organisms…"*. It states the answer but shares almost no vocabulary
with the question: no "fridge", no "keep", no "food at". Nine other temperature-control chunks that
*do* read like the question outrank it.

This is a real limitation of dense retrieval over legal prose, not a labelling error — `:6` does
contain the guidance. It is also **not harmful here**: `:8` says the same thing in plain language and
ranks first, so the question is fully answered. Worth revisiting only if a question appears whose
answer exists *only* in statutory phrasing.

## 3. Separation — what the floor has to divide

| | min | median | max |
|---|---|---|---|
| in-corpus top-1 | **0.709** | 0.823 | 0.873 |
| out-of-corpus top-1 | 0.440 | 0.487 | **0.627** |

**Gap: +0.081.** The classes do not overlap, which is why a single scalar floor is a defensible
mechanism here at all.

The out-of-corpus questions, worst first:

| score | question | what it was drawn to |
|---|---|---|
| 0.627 | recommended dose of metformin for type 2 diabetes | WHO § Sugars |
| 0.565 | how should I train for my first marathon | Irish pyramid § Get active |
| 0.490 | which programming language should I learn first | DGA § Introducing Food to Infants |
| 0.484 | how do I fix a puncture in a bicycle tyre | FSSAI § Procedures for handling |
| 0.457 | electric car sales in Norway | ICMR § Outcomes |
| 0.440 | what is the capital of France | FSSAI § Regulations |

The metformin question at 0.627 is the one that sets the floor, and it is instructive: a diabetes
medication question lands near a chunk about sugar intake. It is also the case least likely to reach
retrieval in production — Phase 1's scope guard rejects medical-advice questions before this point —
so the floor is calibrated against a question that is already double-covered.

## 4. The floor sweep

| floor | in-corpus answered | out-of-corpus refused | label recall @8 |
|---|---|---|---|
| 0.30 | 20/20 | 0/6 | 0.975 |
| 0.50 | 20/20 | 4/6 | 0.975 |
| 0.60 | 20/20 | 5/6 | 0.975 |
| 0.63 | 20/20 | **6/6** | 0.975 |
| **0.65** | **20/20** | **6/6** | **0.958** |
| 0.68 | 20/20 | 6/6 | 0.958 |
| 0.70 | 20/20 | 6/6 | 0.892 |
| 0.71 | 19/20 | 6/6 | 0.842 |
| 0.75 | 15/20 | 6/6 | 0.667 |

**Every floor in 0.63 – 0.70 separates the set perfectly.** The decision is therefore not *whether*
to separate but *where inside that band to sit*, and the two edges fail differently.

## 5. The chosen floor: 0.65

**Not 0.63**, although it scores marginally better on label recall (0.975 vs 0.958). Its margin over
the highest out-of-corpus score is **0.003** — arithmetically perfect on this set and worth nothing
on the next question. A floor whose justification is "no counter-example in 26 samples" is a guess
wearing a measurement's clothes.

**Not 0.70**, the top of the band, and not 0.665, the exact midpoint. [architecture.md
§7.3](./architecture.md) makes the model's `answers_question` verdict the *stronger* of the two
gates, which fixes the direction a mis-set floor should fail in: toward a **wasted generation call**,
never toward a wrong refusal. A refusal is terminal — no model call happens, and the user is told the
corpus does not cover something it does cover. That asymmetry argues for sitting below the midpoint.

**0.65** gives:

- **+0.023** over the worst out-of-corpus score (0.627)
- **−0.059** under the worst in-corpus score (0.709) — the larger margin, on the side where being
  wrong is cheaper
- label recall **0.958**, costing one *supporting* chunk across twenty questions, no whole answer

## 6. Latency

Median **9.9 ms**, max 74.1 ms, over 26 queries — embedding plus exact cosine scan, with the model
loaded once outside the loop. The maximum is the first query, which pays ONNX warm-up.

This is the measurement that retires the ANN question for now: at ~105 chunks an exact scan is well
inside the budget, and IVFFlat or HNSW would trade *approximate* recall for time this phase does not
need. Revisit above roughly 50k chunks ([architecture.md §2](./architecture.md)).

## 7. Exit criteria

- [x] Retrieval across all documents works — 26 queries, §2.
- [x] Retrieval filtered to one document works — `search(..., document_id=...)`, covered by
      `test_retriever.py::test_document_filter_scopes_results`.
- [x] Labelled retrieval set exists, including out-of-corpus questions — 20 + 6, §1.
- [x] recall@k reported for at least three values of k — five values, §2.
- [x] **Floor chosen from measured data, with the number and its justification written down** —
      0.65, §4–5.
- [x] A question known to be outside the corpus returns zero chunks above the floor — 6/6 at 0.65.
- [x] Query latency measured — median 9.9 ms, §6.

## 8. What this does not establish

- **The labels are unreviewed.** Every number inherits them.
- **Twenty-six questions is a calibration, not a guarantee.** The floor has one real counter-example
  (0.627) and 0.023 of margin over it. A question nearer the corpus boundary will eventually beat it;
  that is what gate 2 is for.
- **Nothing here tests answering.** recall@8 = 0.975 says the right passage reaches the model. Whether
  the model then cites it correctly is Phase 2.5, and this phase exists precisely so that failure can
  be attributed cleanly when it happens.
- **The floor is tuned to this corpus and this embedding model.** Changing either invalidates it.
  `services/retriever.py` says so at the constant.
