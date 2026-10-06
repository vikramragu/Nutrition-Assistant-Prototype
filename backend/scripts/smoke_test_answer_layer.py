"""Manual smoke test for Phase 2.5 — the answer layer end to end.

Runs the real pipeline against the real corpus: embed the question, retrieve, group by
document, one Groq call per document, validate every citation mechanically. **Costs real
API credits** -- two or three generation calls per question -- so it is a manual script,
not part of `pytest`.

The automated tests prove the layer's *structure* with a fake model: that no call sees two
documents, that a fabricated chunk id fails, that an uncited answer fails. What they cannot
prove is whether a real model, given a real prompt, actually cites the passage its claim
came from. That is what this script is for, and what the Phase 2.10 failure log measures
properly over the ten fixed questions.

What to read in the output, in order of how much it matters:

1. **Does every claim's citation make sense?** Open the quote under each claim. A claim
   that the quote does not support is `citation_mismatch` -- the failure this whole
   milestone exists to prevent, and the one no amount of validation code can catch.
2. **Did any document decline?** Gate 2 doing its job. Expect the Irish food pyramid to
   decline the cooking-oil question: it scores just above the floor on it and has nothing
   to say about reusing oil.
3. **Warnings in the log.** `low_lexical_overlap_with_cited_chunk` and
   `claim_quantity_absent_from_cited_chunk` are non-blocking, and they are the cheap
   pointer at which answers to read first.

Usage (from backend/, with GROQ_API_KEY set and the corpus seeded):
    .venv/bin/python scripts/smoke_test_answer_layer.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.session import SessionLocal  # noqa: E402
from services.answer_synthesiser import group_by_document, synthesise  # noqa: E402
from services.citation_validator import CitationError  # noqa: E402
from services.corpus_catalog import NOT_IN_CORPUS_MESSAGE, document_refs  # noqa: E402
from services.model_client import GroqModelClient, ModelResponseError  # noqa: E402
from services.retriever import DEFAULT_FLOOR, DEFAULT_K, retrieve  # noqa: E402

QUESTIONS = [
    # One document should carry this: WHO states the free-sugars limit outright.
    "How much free sugar should an adult eat per day?",
    # Two documents clear the floor. Expect FSSAI to answer and Ireland to decline.
    "Can I reuse oil that I have already fried food in?",
    # Statutory phrasing in FSANZ; the test of whether the model will quote a regulation.
    "What temperature should a fridge keep food at?",
    # Gate 1: nothing should clear the floor, and no generation call should be made.
    "What is the best creatine dose for muscle gain?",
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    system_prompt = (Path(__file__).resolve().parents[1] / "prompts" / "system_prompt.md").read_text()
    client = GroqModelClient()
    session = SessionLocal()
    corpus = document_refs(session)

    print(f"corpus: {len(corpus)} documents, k={DEFAULT_K}, floor={DEFAULT_FLOOR}")

    failures = 0
    try:
        for question in QUESTIONS:
            print(f"\n{'=' * 78}\nQ: {question}")

            hits = retrieve(session, question, k=DEFAULT_K, floor=DEFAULT_FLOOR)
            contexts = group_by_document(hits)
            print(f"   {len(hits)} chunk(s) above the floor across {len(contexts)} document(s)")

            if not contexts:
                # Gate 1. No generation call at all -- this costs nothing.
                print(f"\n   NOT IN CORPUS (gate 1, no model call)\n   {NOT_IN_CORPUS_MESSAGE}")
                print(f"   searched: {len(corpus)} documents")
                continue

            try:
                result = synthesise(
                    contexts,
                    model_client=client,
                    system_prompt=system_prompt,
                    history=[],
                    question=question,
                )
            except (ModelResponseError, CitationError) as exc:
                # Both are the 502 path. Printed rather than raised so one bad question
                # does not cost the credits already spent on the rest of the run.
                failures += 1
                print(f"\n   HARD FAILURE ({type(exc).__name__}) -> would be HTTP 502\n   {exc}")
                continue

            if not result.answered:
                # Gate 2: every document read its own passages and declined.
                print(f"\n   NOT IN CORPUS (gate 2, {len(result.declined)} document(s) declined)")
                print(f"   {NOT_IN_CORPUS_MESSAGE}")
                print(f"   searched: {len(corpus)} documents")
                continue

            for answer in result.document_answers:
                print(f"\n   --- {answer.document.name}")
                print(f"       {answer.document.publisher}, {answer.document.year}")
                print(f"       {answer.answer}")
                for claim in answer.claims:
                    source = claim.source
                    print(f"\n       claim: {claim.claim}")
                    print(f"         section: {source.section_heading}")
                    print(f"         quote:   {source.quote[:220]}...")

            for declined in result.declined:
                print(f"\n   --- {declined.name}: declined (answers_question=false)")
    finally:
        session.close()

    print(f"\n{'=' * 78}\n{len(QUESTIONS) - failures}/{len(QUESTIONS)} questions completed without a hard failure.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
