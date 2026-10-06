"""Phase 2.5 citation-validation tests (architecture.md §8.3).

These pin the boundary between blocking and non-blocking, which is the design: rule 1 and
rule 2 must fail the whole response, rule 3 must never fail anything. A change that made
the lexical-overlap check blocking would reject legitimate paraphrase; a change that made
the context check non-blocking would make "answers only from retrieved chunks" a
suggestion. Both are one-line edits, so both get a test.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from db.schemas import DocumentAnswer
from services.citation_validator import (
    LEXICAL_OVERLAP_FLOOR,
    CitationError,
    lexical_overlap,
    quantities_not_in,
    validate_document_answer,
)


def _answer(*, answers_question=True, answer="An answer.", claims=()):
    return DocumentAnswer(
        answers_question=answers_question,
        answer=answer,
        claims=[{"claim": c, "chunk_id": i} for c, i in claims],
    )


# --- Rule 1: every cited id was in this call's context -------------------------------


def test_claim_citing_a_chunk_in_context_resolves_to_that_chunk(chunk):
    context = [chunk(ordinal=0), chunk(ordinal=1)]
    answer = _answer(claims=[("Free sugars should stay under 10%.", str(context[1].chunk_id))])

    cited = validate_document_answer(answer, context)

    assert [c.chunk_key for c in cited] == ["doc-a:1"]


def test_fabricated_chunk_id_is_a_hard_failure(chunk):
    """An invented id is the failure this module exists for: the model answered from
    something it was not given. Not a dropped claim, not a repaired citation."""
    context = [chunk(ordinal=0)]
    answer = _answer(claims=[("A claim.", str(uuid.uuid4()))])

    with pytest.raises(CitationError):
        validate_document_answer(answer, context)


def test_chunk_id_from_another_document_is_a_hard_failure(chunk):
    """The §7.2 guarantee in its negative form. A call given document A's chunks citing
    document B is blending, which per-document answering is supposed to make impossible --
    so if it ever happens it must stop the response, not be tidied away."""
    context = [chunk(slug="doc-a", ordinal=0)]
    other_document_chunk = chunk(slug="doc-b", ordinal=0)
    answer = _answer(claims=[("A claim.", str(other_document_chunk.chunk_id))])

    with pytest.raises(CitationError):
        validate_document_answer(answer, context)


def test_one_bad_citation_fails_the_whole_answer_not_just_that_claim(chunk):
    """No partial salvage. Keeping the good claim and dropping the bad one would leave
    prose asserting something whose supporting claim had silently vanished."""
    context = [chunk(ordinal=0)]
    answer = _answer(
        claims=[
            ("A supported claim.", str(context[0].chunk_id)),
            ("An unsupported claim.", str(uuid.uuid4())),
        ]
    )

    with pytest.raises(CitationError):
        validate_document_answer(answer, context)


def test_surrounding_whitespace_in_a_chunk_id_still_resolves(chunk):
    """A stray space is a transcription artefact, not a fabricated citation. Failing on it
    would spend a 502 on something that is unambiguously the right chunk."""
    context = [chunk(ordinal=0)]
    answer = _answer(claims=[("A claim.", f"  {context[0].chunk_id}\n")])

    assert validate_document_answer(answer, context)[0].chunk_key == "doc-a:0"


def test_two_claims_may_cite_the_same_chunk(chunk):
    """One passage legitimately supports several statements."""
    context = [chunk(ordinal=0)]
    chunk_id = str(context[0].chunk_id)
    answer = _answer(claims=[("First claim.", chunk_id), ("Second claim.", chunk_id)])

    assert len(validate_document_answer(answer, context)) == 2


# --- Rule 2: a non-empty answer has claims ------------------------------------------


def test_non_empty_answer_with_no_claims_is_a_hard_failure(chunk):
    """The schema can force every claim to carry a citation. It cannot force the answer to
    have claims -- so an uncited answer has to be caught here or it ships."""
    answer = _answer(answer="Free sugars should be limited.", claims=[])

    with pytest.raises(CitationError):
        validate_document_answer(answer, [chunk()])


def test_answers_question_true_with_an_empty_answer_is_a_hard_failure(chunk):
    """`answers_question=false` is how a document declines. A `true` verdict with nothing
    behind it means the model is not using the field the second gate depends on, and that
    gate is load-bearing since the floor's separation band narrowed to 0.02."""
    answer = _answer(answer="   ", claims=[])

    with pytest.raises(CitationError):
        validate_document_answer(answer, [chunk()])


# --- The decline path ----------------------------------------------------------------


def test_declining_document_is_not_validated_and_cites_nothing(chunk):
    """Gate 2. A document that read its passages and found no answer is a correct,
    expected outcome -- not an error, and not something to extract an answer from."""
    answer = _answer(answers_question=False, answer="", claims=[])

    assert validate_document_answer(answer, [chunk()]) == []


def test_decline_carrying_content_is_logged_and_still_discarded(chunk, caplog):
    """A contradiction, so it is worth seeing -- but the content is dropped either way, so
    nothing ungrounded reaches the user and a 502 would cost the user an answer another
    document may have given them."""
    answer = _answer(
        answers_question=False,
        answer="Actually the document says this.",
        claims=[("A claim.", str(uuid.uuid4()))],
    )

    with caplog.at_level(logging.WARNING):
        assert validate_document_answer(answer, [chunk()]) == []

    assert "declined_document_answer_carried_content" in caplog.text


# --- Rule 3: non-blocking signals ----------------------------------------------------


def test_low_lexical_overlap_warns_without_blocking(chunk, caplog):
    """Rule 3 is a detector for `citation_mismatch`, not a gate. Paraphrase is legitimate,
    so this can only ever log."""
    context = [chunk(text="Store cooked food below 5 degrees Celsius within two hours.")]
    answer = _answer(claims=[("Vitamin D supports calcium absorption.", str(context[0].chunk_id))])

    with caplog.at_level(logging.WARNING):
        cited = validate_document_answer(answer, context)

    assert len(cited) == 1  # non-blocking: the answer still ships
    assert "low_lexical_overlap_with_cited_chunk" in caplog.text


def test_faithful_paraphrase_does_not_warn(chunk, caplog):
    context = [chunk(text="Adults should limit free sugars to less than 10% of total energy.")]
    answer = _answer(
        claims=[("Adults should limit free sugars to under 10% of energy.", str(context[0].chunk_id))]
    )

    with caplog.at_level(logging.WARNING):
        validate_document_answer(answer, context)

    assert "low_lexical_overlap_with_cited_chunk" not in caplog.text
    assert "claim_quantity_absent_from_cited_chunk" not in caplog.text


def test_quantity_absent_from_the_cited_chunk_warns_without_blocking(chunk, caplog):
    """The sharpest signal available for `inconsistent_number`: the claim states 50 g and
    the cited passage never mentions 50. Non-blocking, because a document can state a
    percentage that a claim legitimately restates in grams -- which is exactly the case a
    human needs to look at."""
    context = [chunk(text="Adults should limit free sugars to less than 10% of total energy.")]
    answer = _answer(claims=[("Adults should eat under 50 g of free sugars a day.", str(context[0].chunk_id))])

    with caplog.at_level(logging.WARNING):
        cited = validate_document_answer(answer, context)

    assert len(cited) == 1
    assert "claim_quantity_absent_from_cited_chunk" in caplog.text


def test_quantity_present_in_the_cited_chunk_does_not_warn(chunk, caplog):
    context = [chunk(text="Keep the fridge at 5 degrees Celsius or below.")]
    answer = _answer(claims=[("A fridge should run at 5 degrees Celsius or below.", str(context[0].chunk_id))])

    with caplog.at_level(logging.WARNING):
        validate_document_answer(answer, context)

    assert "claim_quantity_absent_from_cited_chunk" not in caplog.text


# --- The measures themselves ---------------------------------------------------------


def test_lexical_overlap_is_asymmetric_in_the_claim_s_favour():
    """A chunk is far longer than a claim, so a symmetric measure would score every honest
    citation low. The question is whether the claim is covered, not the reverse."""
    claim = "Free sugars should be limited."
    chunk_text = "Free sugars should be limited. " + "Unrelated guidance follows. " * 50

    assert lexical_overlap(claim, chunk_text) == 1.0


def test_lexical_overlap_of_a_claim_with_no_content_words_is_not_a_signal():
    assert lexical_overlap("It is so.", "Anything at all.") == 1.0


def test_quantity_comparison_normalises_separators_and_trailing_zeros():
    """`1,500` in a claim and `1500` in a chunk are the same number; flagging them would
    make the warning noise and the warning would stop being read."""
    assert quantities_not_in("Limit to 1,500 mg of sodium.", "no more than 1500 mg sodium") == set()
    assert quantities_not_in("Keep below 5.0 degrees.", "keep below 5 degrees") == set()
    assert quantities_not_in("Limit to 2,300 mg.", "no more than 1500 mg") == {"2300"}


def test_overlap_floor_is_a_reporting_threshold_not_a_gate():
    """Guards the intent: if this ever becomes a comparison that blocks, the number stops
    being arbitrary and needs calibrating against a labelled set first."""
    assert 0.0 < LEXICAL_OVERLAP_FLOOR < 1.0
