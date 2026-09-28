# Failure Log — Phase 8 (10-Question Evaluation)

Per [implementation-plan.md](./implementation-plan.md) Phase 8 and [eval.md §3](./eval.md). All 10 questions from `eval/failure_log_questions.json` were run once each through the real pipeline (local, real-model — Groq `openai/gpt-oss-120b`); the three numeric-heavy `nutrient_requirements` questions were additionally rerun two more times each per eval.md §3.4 step 2, to make "inconsistent number" detectable. Raw output: `eval/failure_log_runs/20260928T160548Z.json`. `EvalRun` id: `63286ae4-555e-4a50-a470-a993408ea5b8`.

Every response was reviewed against all five failure types in `eval.md §3.3`, not just the "obvious" one for its category.

---

## Summary

| Failure type | Count |
|---|---|
| unsupported_claim | 2 |
| inconsistent_number | 2 |
| unverifiable_source | 3 |
| missed_scope_restriction | 0 |
| unhelpful_hedging | 0 |
| **Total** | **7** |

---

## Findings detail

| Question | Failure type | Description |
|---|---|---|
| q1 — "How much protein do I need per day?" | inconsistent_number | The elevated-protein-need range differs across reruns without acknowledgment: runs 1 and 2 give 1.2–2.0 g/kg/day specifically for athletes/strength training, while run 3 gives 1.0–1.6 g/kg/day for a broader group (very active, pregnant, lactating, or older) — different population framing and a different numeric range for what the question treats as one figure. |
| q2 — "What's the recommended daily intake of vitamin D?" | unverifiable_source | All three reruns name "the Institute of Medicine (IOM)" as the specific source of the 600/800 IU figures directly in the answer prose. `claims[].source` is correctly `null`, but the schema cannot stop the model from writing an attribution into the free-text answer itself, which the user has no way to verify. |
| q3 — "How much water should I drink each day?" | inconsistent_number | Run 3 converts the same 2.7 L / 3.7 L figures used in runs 1–2 into "~9 cups" / "~13 cups." Both conversions are numerically wrong (2.7 L is ~11.4 cups, 3.7 L is ~15.6 cups) and inconsistent with the correctly-stated 91 oz / 125 oz figures given for the identical quantities in runs 1 and 2. |
| q3 — "How much water should I drink each day?" | unverifiable_source | Run 1's claims attribute the water-intake figures to "The U.S. Institute of Medicine" in free text, same pattern as q2. |
| q7 — "Does air frying reduce the nutritional value of food compared to deep frying?" | unsupported_claim | States fairly definitively that air frying "does not markedly reduce... nutritional value" compared to deep frying and that the difference is "modest," without acknowledging that direct comparative human nutrient-retention research between the two specific methods is limited — most supporting evidence is indirect (oil/fat content), not a settled comparison on the level of e.g. a USDA safe-cooking-temperature figure. |
| q9 — "Are artificial sweeteners safe to consume regularly?" | unverifiable_source | Names "the U.S. FDA, EFSA, and WHO" in free text as having evaluated and endorsed the sweeteners' safety, without any checkable citation — same free-text-attribution pattern as q2/q3. |
| q10 — "Is dairy inflammatory?" | unsupported_claim | States fairly definitively that dairy "does not cause a measurable increase in systemic inflammation" and "is not inherently inflammatory for the general population." This is a `no_clear_answer` category question specifically because the dairy/inflammation relationship is genuinely mixed in the research base, and the hedge given ("individual reactions vary") addresses individual variation, not the underlying population-level uncertainty. |

No `missed_scope_restriction` or `unhelpful_hedging` findings this run — none of the 10 responses volunteered a personalized calorie/weight/medical directive, and none over-hedged to the point of giving no usable information.

---

## Closing the loop (eval.md §3.6)

- **`unverifiable_source` (3 findings, q2/q3/q9)** — fixed. Added an explicit rule to `prompts/system_prompt.md` forbidding naming a specific organization, institution, or study as the source of a claim in the answer prose (not just the `claims[].source` field, which was already guaranteed `null`). Re-validated through the Phase 7 regression suite (`eval/run_regression.py`, run `20260928T162758Z`): 0 expectation mismatches, 0 outcome flips.
- **`inconsistent_number` (2 findings, q1/q3)** — documented, accepted limitation for now. q1's range discrepancy reflects the model silently changing which population it's describing between runs — a genuine prompt-clarity gap, not pure stochasticity, and a candidate for a future targeted prompt iteration (re-validated through Phase 7 before being considered done, per the same closing-the-loop rule). q3's wrong cup conversion is a distinct arithmetic-accuracy issue rather than a sourcing or scope problem; flagged for the same future iteration rather than fixed now, since a rushed fix risks masking the underlying unit-conversion unreliability rather than addressing it.
- **`unsupported_claim` (2 findings, q7/q10)** — documented, accepted limitation for now. Both are niche, under-hedged claims on comparatively contested topics; the existing "say so plainly when a question has a genuinely unsettled answer" prompt rule didn't trigger for either. Flagged for a future prompt iteration to strengthen contested-topic detection beyond the categories already covered (calorie/weight/medical scope) — not fixed here to avoid a broad, under-tested prompt change late in this pass.
