"""Citation validation — code, not prompt (architecture.md §8.3).

This module is what makes *"model knowledge is not a source"* enforceable rather than
aspirational. The prompt asks the model to cite only the passages it was given; this
checks that it did, and a failure is a failed response rather than a warning.

Three rules, and the split between blocking and non-blocking is the whole design:

1. **Every cited chunk id was in that call's context.** Blocking. An id from another
   document, an id from an earlier turn, or an invented one is a `CitationError`.
2. **A non-empty answer has at least one claim.** Blocking. An uncited answer does not
   ship.
3. **Lexical overlap and quantity agreement.** Non-blocking, logged. Paraphrase is
   legitimate, so these cannot be gates -- but they are the signal that surfaces
   "cited the wrong chunk" when the Phase 2.10 failure log is read.

**There is no repair path, deliberately.** Dropping the offending claim and shipping the
rest would leave prose whose supporting claim had quietly vanished -- an uncited assertion,
which is the exact thing the schema was shaped to prevent. Rewriting the citation would
mean the backend choosing a source for a claim it did not write. Both are worse than an
error, so the whole response fails.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from db.schemas import DocumentAnswer
from services.retriever import ScoredChunk

logger = logging.getLogger(__name__)

# Rule 3 thresholds. Both are *reporting* thresholds, not gates -- moving them changes how
# much noise the log carries, never whether a response ships.
LEXICAL_OVERLAP_FLOOR = 0.30

# Deliberately short. This list exists to stop "the", "and" and "should" from inflating the
# overlap of an unrelated claim, not to do linguistics. A longer list would make the
# measure stricter in ways nobody has calibrated.
_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could do does for from had has have how in into
    is it its may more most must no not of on or should such than that the their them then
    there these they this those to was were what when which who will with within you your
    """.split()
)

_WORD = re.compile(r"[a-z][a-z'-]{2,}")
# Quantities only: a bare year or a list marker is noise. Captures 5, 5.5, 1,500, 60.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class CitationError(Exception):
    """A generated answer cited something it was not given, or nothing at all.

    Surfaced as HTTP 502 by the chat endpoint -- the same hard-failure path as a Phase 1
    schema violation (architecture.md §8.3). Never caught and worked around.
    """


def validate_document_answer(
    answer: DocumentAnswer, context: Sequence[ScoredChunk]
) -> list[ScoredChunk]:
    """Check one `DocumentAnswer` against the chunks that call was given.

    Returns the chunk backing each claim, in claim order, so the caller can expand the
    citations without a second lookup. Raises `CitationError` on any blocking violation.

    A `DocumentAnswer` with `answers_question=False` contributes nothing and is not
    validated: its answer and claims are discarded by the caller. A model that fills those
    fields anyway is contradicting its own verdict, which is logged and otherwise ignored
    -- nothing ungrounded can reach the user through that path, because the whole document
    answer is dropped.
    """
    by_id = {str(chunk.chunk_id): chunk for chunk in context}

    if not answer.answers_question:
        if answer.answer.strip() or answer.claims:
            logger.warning(
                "declined_document_answer_carried_content",
                extra={
                    "failure_type": "contradictory_verdict",
                    "answer_chars": len(answer.answer),
                    "claim_count": len(answer.claims),
                },
            )
        return []

    # answers_question=True with nothing to say is the model contradicting the one field
    # the schema forces it to commit to. Treated as a hard failure rather than quietly
    # re-read as a decline: a decline is what `answers_question=False` is for, and
    # inferring one here would hide a model that cannot use the field.
    if not answer.answer.strip():
        raise CitationError(
            "Model set answers_question=true but returned an empty answer. "
            "A decline is answers_question=false; these are not interchangeable."
        )

    # Rule 2. The schema can force every claim to carry a citation; it cannot force the
    # answer to have claims. This can.
    if not answer.claims:
        raise CitationError(
            "Model returned a non-empty answer with no claims. Every factual statement "
            "must be cited; an uncited answer does not ship (architecture.md §8.3)."
        )

    # Rule 1. The one check that makes grounding mechanical.
    cited: list[ScoredChunk] = []
    for position, claim in enumerate(answer.claims):
        chunk = by_id.get(claim.chunk_id.strip())
        if chunk is None:
            raise CitationError(
                f"Claim {position} cited chunk_id {claim.chunk_id!r}, which was not in "
                f"this call's context. Context held {len(by_id)} chunk(s): "
                f"{sorted(by_id)}."
            )
        cited.append(chunk)

    _report_weak_citations(answer, cited)
    return cited


def _report_weak_citations(answer: DocumentAnswer, cited: Sequence[ScoredChunk]) -> None:
    """Rule 3. Logs only -- these never block a response.

    Both measures are proxies. Low overlap can be honest paraphrase and a quantity can be
    legitimately derived, so neither can be a gate without rejecting correct answers. What
    they are good for is making `citation_mismatch` findable in a run of 10 questions
    instead of requiring all of them to be read by hand (architecture.md §13.2).
    """
    for claim, chunk in zip(answer.claims, cited, strict=True):
        overlap = lexical_overlap(claim.claim, chunk.text)
        if overlap < LEXICAL_OVERLAP_FLOOR:
            logger.warning(
                "low_lexical_overlap_with_cited_chunk",
                extra={
                    "failure_type": "citation_mismatch_suspected",
                    "overlap": round(overlap, 3),
                    "floor": LEXICAL_OVERLAP_FLOOR,
                    "chunk_key": chunk.chunk_key,
                    "claim": claim.claim,
                },
            )

        unsupported = quantities_not_in(claim.claim, chunk.text)
        if unsupported:
            logger.warning(
                "claim_quantity_absent_from_cited_chunk",
                extra={
                    "failure_type": "inconsistent_number_suspected",
                    "quantities": sorted(unsupported),
                    "chunk_key": chunk.chunk_key,
                    "claim": claim.claim,
                },
            )


def lexical_overlap(claim: str, chunk_text: str) -> float:
    """Fraction of the claim's content words that appear in the cited chunk.

    Asymmetric on purpose: the question is whether the *claim* is covered by the chunk, not
    whether the chunk is covered by the claim. A chunk is typically far longer, so a
    symmetric measure (Jaccard) would score every legitimate citation low.
    """
    claim_words = _content_words(claim)
    if not claim_words:
        return 1.0  # nothing to support; not a signal either way
    return len(claim_words & _content_words(chunk_text)) / len(claim_words)


def quantities_not_in(claim: str, chunk_text: str) -> set[str]:
    """Numbers the claim states that do not appear in the chunk it cites.

    The sharpest available signal for `inconsistent_number`: a claim naming 50 g where the
    cited passage says nothing resembling 50 is either arithmetic the document did not do
    or a number from somewhere else. Both are worth a human look.
    """
    return _numbers(claim) - _numbers(chunk_text)


def _content_words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    return {_normalise_number(match) for match in _NUMBER.findall(text)}


def _normalise_number(raw: str) -> str:
    """So that `1,500` in a claim matches `1500` in a chunk, and `5.0` matches `5`."""
    cleaned = raw.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else str(value)
