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

[`eval/retrieval_set.json`](../../../eval/retrieval_set.json) — 28 questions: **20 in-corpus** with
hand-labelled relevant `chunk_key`s, **8 out-of-corpus** with deliberately empty labels.

The out-of-corpus questions are not filler. They are the only way to measure where the floor has to
sit: the best score any of them reaches is the number the floor must clear. They are spread on
purpose from the obviously unrelated ("capital of France") to the adjacent-but-absent ("recommended
dose of metformin"), because a floor tuned only against easy negatives is not calibrated at all.

**q27 and q28 are the hardest negatives, and the most important.** Both ask for figures the corpus
*used to contain* — weekly alcohol limits, calories by age — in tables now quarantined because
extraction destroyed them (§4). The surrounding text is still there and still topically right, so
these are the two questions most likely to defeat the floor. They exist to make that visible rather
than to be passed.

**q10 was changed**, not deleted: it asked for the weekly alcohol limit, which the corpus no longer
states. It now asks who should avoid alcohol entirely, and the original wording moved to q27 as a
negative. The change is recorded in the file's `label_note`.

**Labelling rule:** a chunk is relevant if it *contains the guidance that answers the question* —
not if it is merely on the same topic. A chunk that mentions sugar is not relevant to "how much free
sugar" unless it states a limit.

### The labels were reviewed, and four were wrong

Reviewed end-to-end on 2026-10-05, after the first calibration. **Four of 44 labels did not survive
the review's own rule** and were removed; each removal is recorded in that question's `label_note`.

| question | removed | why |
|---|---|---|
| q05 — how should a restaurant dispose of used cooking oil? | `fssai:4` | Describes the *problem* ("disposed of in an environmentally hazardous manner") and gives no guidance. `fssai:5` has the procedures |
| q06 — how many servings of vegetables and fruit? | `ie-doh:11` | Ends *"Servings a day"* with no number — a column heading whose value was in the graphic and did not survive extraction |
| q08 — how much salt or sodium is too much? | `who:7` | States that sodium raises blood pressure; gives no quantity. The 5 g limit is in `who:8`, still labelled |
| q11 — what temperature do I need to reheat food to? | `fsanz:13` | Gives method and duration ("rolling boil", "15–20 minutes"), not a temperature. `fsanz:12` has the 60 °C requirement |

**One borderline case was kept**, with the reasoning recorded: q17 asks what is on the Food Pyramid's
top shelf, and the chunk's *body* never names the foods — the answer is only in its section heading,
*"Foods and drinks high in fat, sugar and salt"*. That counts, because the heading is embedded into
the chunk's vector by `Chunk.embedding_input()` and appears in the citation, so a reader following
it does get the answer. The four removals are different in kind: there the information is absent
from the chunk entirely.

Effect on the measurements: **recall@8 unchanged at 0.975**, recall@1 improved 0.575 → 0.633 (the
honest direction — the system had been penalised for not finding chunks with no answer in them), and
**the floor band did not move**. 32 labels remain across 20 questions.

> **Still one person's judgement.** The review caught four errors, but the reviewer was also the
> author of both the labels and the retriever. A second reader would still be worth having.

## 2. recall@k

| k | recall | questions with every label found |
|---|---|---|
| 1 | 0.633 | 8/20 |
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
| in-corpus top-1 | **0.7087** | 0.823 | 0.873 |
| out-of-corpus top-1 | 0.440 | 0.527 | **0.6796** |

**Gap: +0.029.** The classes still do not overlap, but the margin is **a third of what it was**
before the corpus quarantine — see §4.

The out-of-corpus questions, worst first:

| score | question | drawn to |
|---|---|---|
| **0.680** | standard drinks a week, lower risk limit for women | Irish pyramid § Fluids |
| **0.658** | calories for an inactive adult over 51 | Irish pyramid § Serving guide |
| 0.627 | recommended dose of metformin | WHO § Sugars |
| 0.565 | how should I train for my first marathon | Irish pyramid § Get active |
| 0.490 | which programming language should I learn first | DGA § Introducing Food to Infants |
| 0.484 | how do I fix a puncture in a bicycle tyre | FSSAI § Procedures for handling |
| 0.457 | electric car sales in Norway | ICMR § Outcomes |
| 0.440 | what is the capital of France | FSSAI § Regulations |

The top two are the interesting ones, and they are new.

## 4. The corpus quarantine, and what it cost the floor

Three passages were removed from the index on 2026-10-05 (`corpus.yaml` → `quarantine`). All three
are tables flattened into unusable text: the pipeline renders everything to plain text and a chunk
is a string, so a table's rows and columns are lost and its labels end up next to numbers they may
not belong to.

Not a PDF-specific fault, despite appearances — a perfect HTML table flattens the same way through
this pipeline (see [ingestion-report.md §9.8](./ingestion-report.md)). A PDF makes it
*unrecoverable* rather than causing it, because there the grid exists only as coordinates.

The excluded passages and why:

| passage | what went wrong |
|---|---|
| Irish § calorie-by-age, **label row** | Labels at y=265/356, values at y=529, in separate boxes. Four labels, four values, no way to pair them |
| Irish § calorie-by-age, **value row** | The other half of the same table |
| Irish § weekly alcohol limits | Men 17 drinks/170 g, Women 11/110 g. Pairing is positional only; a model has even odds of swapping the sexes on a health figure |

The ICMR "My Plate" grams-per-day table was reviewed and **kept**: its values did not survive
extraction at all, so there is no number to misattribute. A model asked for grams finds none and
declines — the safe failure.

### Excluding one half of a table made it worse

Quarantining only the label row left the **values** to re-chunk under the heading *"Average daily
calorie needs for all foods and drinks for adults"* — which reads as authoritative — and its score
for the calorie question went **up**, to 0.719. Both halves had to go. A partial exclusion is not a
partial fix.

### Why this is a manifest list and not a rule

Three text statistics were measured across all 105 chunks looking for an automatic detector:

| signal | result |
|---|---|
| repetition ratio | No separation — FSANZ prose repeats "temperature/food" as heavily as a table repeats its headers |
| prose density | Catches the tables, but also flags legitimate bullet lists (WHO Five Keys at 0.00) |
| function-word density | No separation — dense DGA bullet prose sits in the same 0.20–0.29 range |

None distinguishes a destroyed table from ordinary bulleted guidance. A detector aggressive enough
to catch these would drop real advice, which is the worse error. So the judgement is recorded as
data, in the manifest, with a reason per entry — reviewable and diffable, not hidden in a threshold.
`verify_quarantine_rules` raises if a rule stops matching, because a stale rule means the passage is
back in the index while the manifest still claims it is excluded.

### The cost

Removing the figures did not remove the *topics*. Questions about them now land on neighbouring
chunks from the same pages, which score 0.66–0.68 — far higher than any other negative. The
separation band narrowed from **0.63–0.70 (0.07 wide)** to **0.68–0.70 (0.02 wide)**.

That is the honest trade: the corpus can no longer produce a wrong calorie or alcohol figure, and in
exchange the floor became a much weaker discriminator.

## 5. The floor sweep

| floor | in-corpus answered | out-of-corpus refused | label recall @8 |
|---|---|---|---|
| 0.50 | 20/20 | 4/8 | 0.975 |
| 0.60 | 20/20 | 5/8 | 0.975 |
| 0.65 | 20/20 | 6/8 | 0.958 |
| 0.68 | 20/20 | **8/8** | 0.958 |
| **0.69** | **20/20** | **8/8** | **0.942** |
| 0.70 | 20/20 | 8/8 | 0.917 |
| 0.75 | 16/20 | 8/8 | 0.717 |

## 6. The chosen floor: 0.69

**Not 0.68**, the bottom of the band, even though the usual rule — architecture.md §7.3 makes the
model's `answers_question` the stronger gate, so a mis-set floor should fail toward a wasted
generation call rather than a wrong refusal — argues for sitting low. Its margin over the worst
negative is **0.0004**. That is a coincidence, not a margin, and the same objection that ruled out
0.63 in the previous calibration.

**0.69**, the midpoint, gives the only two real margins available:

- **0.010** over the worst out-of-corpus score (0.6796)
- **0.019** under the worst in-corpus score (0.7087)

Both are small. There is no comfortable choice inside a 0.02-wide band, and pretending otherwise
would be the dishonest part.

### What this means for the design

**Gate 2 is now load-bearing, not a backstop.** The floor was comfortable when the band was 0.07
wide; at 0.02 it is one awkward question away from failing in either direction. The two questions
that nearly defeat it — q27 and q28 — are in the labelled set precisely so that the next change to
this corpus has to confront them.

The design anticipated this: §7.3 always made the model's `answers_question` verdict the stronger
gate. It is now carrying the weight it was designed for rather than the weight it was expected to.

## 7. Latency

Median **7.8 ms**, over 28 queries — embedding plus exact cosine scan, model loaded
once outside the loop.

This retires the ANN question for now: at ~103 chunks an exact scan is well inside budget, and
IVFFlat or HNSW would trade *approximate* recall for time this phase does not need. Revisit above
roughly 50k chunks ([architecture.md §2](./architecture.md)).

## 8. Exit criteria

- [x] Retrieval across all documents works — 28 queries, §2.
- [x] Retrieval filtered to one document works — `search(..., document_id=...)`,
      `test_retriever.py::test_document_filter_scopes_results`.
- [x] Labelled retrieval set exists, including out-of-corpus questions — 20 + 8, §1.
- [x] recall@k reported for at least three values of k — five values, §2.
- [x] **Floor chosen from measured data, with the number and its justification written down** —
      0.69, §5–6.
- [x] A question known to be outside the corpus returns zero chunks above the floor — 8/8 at 0.69.
- [x] Query latency measured — median 8.7 ms, §7.

## 9. What this does not establish

- **The labels were reviewed once, by their author.** Four errors were found and fixed (§1); a
  second reader would still be worth having, since the same person wrote the retriever.
- **Labels are keyed by `slug:ordinal`, which shifts when a document is re-chunked.** This already
  bit once: quarantining two passages renumbered every later Irish chunk and six labels silently
  pointed at the wrong text, dropping recall 0.975 → 0.800 in a way that looked exactly like a real
  regression. `run_retrieval_eval.py` now refuses to run if a labelled key no longer exists.
- **The margin is 0.010.** Twenty-eight questions is a calibration, not a guarantee, and this one is
  tighter than the last. Expect the floor to need re-measuring after any corpus change.
- **Nothing here tests answering.** recall@8 = 0.975 says the right passage reaches the model.
  Whether the model then cites it correctly is Phase 2.5 — and gate 2 now matters more than this
  phase's number does.
- **The floor is tuned to this corpus and this embedding model.** Changing either invalidates it.
