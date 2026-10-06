"""Per-document answering (architecture.md §7.2) — the core structural decision of Phase 2.

Retrieval returns chunks from several documents. This module groups them by document and
makes **one generation call per document**, each seeing only that document's passages.

**Why not one call with everything.** The brief forbids blending two sources into one
claim and forbids picking a winner when documents disagree. A single call holding chunks
from three publishers is one fluent sentence away from violating both, and fluency is a
language model's default behaviour. Splitting the calls makes blending
*unrepresentable* rather than merely discouraged: a call can only cite chunks it was
given, and it was given one document's.

That property is worth stating as a rule, because it is easy to undo by accident:
**nothing in this module may ever put two documents' chunks in one prompt.** If a future
change wants cross-document synthesis, it has to argue with §7.2 first, not slip past it.

Cost: typically two or three calls instead of one, on a corpus where most questions touch
one or two documents. The calls are independent, so they run concurrently and the wall
clock is roughly one call deep.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from db.schemas import Citation, CitedClaim, DocumentAnswer, DocumentAnswerOut, DocumentRef
from services.citation_validator import validate_document_answer
from services.model_client import ChatMessage, ModelClient
from services.retriever import ScoredChunk

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "document_answer_prompt.md"

# HTML comments in the prompt file are notes to whoever edits it, not instructions to the
# model, so they are stripped rather than sent. Two reasons, one of each kind: a note that
# describes how the file is rendered is confusing to a reader who is supposed to be
# answering from it, and this prompt goes out once per document per turn, so a 900-character
# comment is paid for two or three times on every question.
_MAINTAINER_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Read once at import. The prompt is a deployed artifact, not configuration: re-reading it
# per request would let a half-saved file reach a user mid-edit.
DOCUMENT_ANSWER_PROMPT = _MAINTAINER_COMMENT.sub("", _PROMPT_PATH.read_text()).strip() + "\n"

# Substituted by literal replacement rather than str.format, because passage text is
# arbitrary government prose and routinely contains `%` and occasionally `{`.
_DOCUMENT_TOKEN = "{{DOCUMENT}}"
_PASSAGES_TOKEN = "{{PASSAGES}}"


def _assert_single_placeholder(template: str, token: str) -> None:
    """Each placeholder must occur exactly once, checked at import rather than at runtime.

    `str.replace` is global. A second copy of a placeholder -- in a comment, in an example,
    in a half-finished edit -- gets filled in too, and the first version of this prompt did
    exactly that: the passages block landed inside the maintainer comment *and* in its
    intended position, doubling every prompt. Nothing failed. No placeholder was left over,
    the prompt still parsed, and the model still answered. It was found by reading a
    rendered prompt, which is the same way the Phase 2.1 heading bug and the Phase 2.4
    block-order bug were found.

    So: a loud failure at import, where it costs a startup rather than a milestone.
    """
    count = template.count(token)
    if count != 1:
        raise ValueError(
            f"{_PROMPT_PATH.name} contains {token} {count} time(s), expected exactly 1. "
            "Replacement is global, so a duplicate is substituted too and silently "
            "doubles the prompt."
        )


_assert_single_placeholder(DOCUMENT_ANSWER_PROMPT, _DOCUMENT_TOKEN)
_assert_single_placeholder(DOCUMENT_ANSWER_PROMPT, _PASSAGES_TOKEN)

# At k=8 there are at most 8 documents, and realistically two or three. Four threads make
# the common case single-call-deep without opening a pool per request that nothing uses.
MAX_CONCURRENT_CALLS = 4


@dataclass(frozen=True)
class DocumentContext:
    """One document and the chunks of it that survived the floor.

    This is the unit of generation. Its `chunks` are exactly what one model call may see,
    which is why the type exists rather than passing a bare list around: the boundary it
    draws is the §7.2 guarantee.
    """

    document: DocumentRef
    chunks: tuple[ScoredChunk, ...]

    @property
    def best_score(self) -> float:
        return self.chunks[0].score

    @property
    def chunk_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(chunk.chunk_id for chunk in self.chunks)


@dataclass(frozen=True)
class SynthesisResult:
    document_answers: list[DocumentAnswerOut]
    # Documents that read their own passages and said they do not answer the question --
    # gate 2 of the not-in-corpus refusal. Kept separate from `document_answers` rather
    # than filtered away, because "three documents looked and declined" is a different
    # event from "nothing scored above the floor" and the Phase 2.10 failure log needs to
    # tell `over_refusal` from a genuine coverage gap.
    declined: list[DocumentRef]
    # Every chunk that reached a generation call -- `retrieval_hits.used` in §6. Includes
    # the chunks of declining documents: they were read, and that they were read is the
    # evidence that the decline was a verdict rather than a miss.
    used_chunk_ids: frozenset[uuid.UUID]

    @property
    def answered(self) -> bool:
        """False means every document declined: a not-in-corpus refusal, not an answer."""
        return bool(self.document_answers)


def group_by_document(hits: Sequence[ScoredChunk]) -> list[DocumentContext]:
    """Group retrieval hits into one context per document, strongest document first.

    Ordering is by the document's single best-scoring chunk, not by how many chunks it
    contributed. A document with one strongly matching passage answers better than one with
    four weak ones, and count would rank a fragmentary leaflet above it -- the Irish food
    pyramid contributes 19 short chunks and would win on volume for almost any question.
    """
    grouped: dict[uuid.UUID, list[ScoredChunk]] = {}
    for hit in hits:
        grouped.setdefault(hit.document_id, []).append(hit)

    contexts = [
        DocumentContext(
            document=DocumentRef(
                id=chunks[0].document_id,
                name=chunks[0].document_name,
                publisher=chunks[0].publisher,
                year=chunks[0].year,
                url=chunks[0].source_url,
            ),
            chunks=tuple(sorted(chunks, key=lambda c: c.score, reverse=True)),
        )
        for chunks in grouped.values()
    ]
    contexts.sort(key=lambda c: c.best_score, reverse=True)
    return contexts


def render_document_prompt(context: DocumentContext, template: str = DOCUMENT_ANSWER_PROMPT) -> str:
    """Render the per-document prompt with one document's passages and their ids."""
    return template.replace(_DOCUMENT_TOKEN, _describe_document(context.document)).replace(
        _PASSAGES_TOKEN, _format_passages(context.chunks)
    )


def synthesise(
    contexts: Sequence[DocumentContext],
    *,
    model_client: ModelClient,
    system_prompt: str,
    history: list[ChatMessage],
    question: str,
) -> SynthesisResult:
    """One validated answer per document that has something to say.

    Raises `ModelResponseError` if any call returns a non-conformant response, and
    `CitationError` if any answer cites a chunk it was not given or ships prose with no
    claims. Both propagate: a partially valid set of document answers is not a degraded
    result to be salvaged, it is a response that failed grounding, and the endpoint turns
    either into a 502.
    """
    if not contexts:
        return SynthesisResult(document_answers=[], declined=[], used_chunk_ids=frozenset())

    answers = _generate_all(
        contexts,
        model_client=model_client,
        system_prompt=system_prompt,
        history=history,
        question=question,
    )

    document_answers: list[DocumentAnswerOut] = []
    declined: list[DocumentRef] = []

    for context, answer in zip(contexts, answers, strict=True):
        cited = validate_document_answer(answer, context.chunks)
        if not answer.answers_question:
            declined.append(context.document)
            continue
        document_answers.append(_expand_citations(context, answer, cited))

    logger.info(
        "synthesised: documents=%d answered=%d declined=%d claims=%d",
        len(contexts),
        len(document_answers),
        len(declined),
        sum(len(a.claims) for a in document_answers),
    )

    return SynthesisResult(
        document_answers=document_answers,
        declined=declined,
        used_chunk_ids=frozenset().union(*(c.chunk_ids for c in contexts)),
    )


def _generate_all(
    contexts: Sequence[DocumentContext],
    *,
    model_client: ModelClient,
    system_prompt: str,
    history: list[ChatMessage],
    question: str,
) -> list[DocumentAnswer]:
    """Run the per-document calls, returning results in `contexts` order.

    Order is restored regardless of completion order so that the same question against the
    same corpus produces the same response, and so that a failure is attributed to the
    document that caused it rather than to whichever call happened to finish first.
    """

    def generate(context: DocumentContext) -> DocumentAnswer:
        return model_client.answer_from_document(
            system_prompt,
            render_document_prompt(context),
            history,
            question,
        )

    # One document is the common case; a pool for it buys nothing and costs a thread's
    # worth of traceback depth when a call fails.
    if len(contexts) == 1:
        return [generate(contexts[0])]

    with ThreadPoolExecutor(max_workers=min(len(contexts), MAX_CONCURRENT_CALLS)) as pool:
        # `map` re-raises in input order, so the first failure reported is the first
        # document's, not the first thread's.
        return list(pool.map(generate, contexts))


def _expand_citations(
    context: DocumentContext, answer: DocumentAnswer, cited: Sequence[ScoredChunk]
) -> DocumentAnswerOut:
    """Turn the model's `{claim, chunk_id}` pairs into full citations.

    The publisher, year and URL come from `context.document` -- the row retrieval joined,
    never anything the model wrote. `cited` is already validated to come from this
    document's chunks, so a claim cannot carry another document's citation.
    """
    return DocumentAnswerOut(
        document=context.document,
        answer=answer.answer,
        claims=[
            CitedClaim(
                claim=claim.claim,
                source=Citation(
                    chunk_id=chunk.chunk_id,
                    document=context.document,
                    section_heading=chunk.section_heading,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    quote=chunk.text,
                ),
            )
            for claim, chunk in zip(answer.claims, cited, strict=True)
        ],
    )


def _describe_document(document: DocumentRef) -> str:
    """`year` is nullable and the brief forbids inventing one, so say so in words."""
    year = str(document.year) if document.year is not None else "publication year not stated"
    return f"{document.name} — {document.publisher}, {year}"


def _format_passages(chunks: Sequence[ScoredChunk]) -> str:
    return "\n\n".join(_format_passage(index, chunk) for index, chunk in enumerate(chunks, 1))


def _format_passage(index: int, chunk: ScoredChunk) -> str:
    """One passage block. `chunk_id` is on its own line, labelled, and comes first.

    It is what the model has to copy back verbatim, so it is the easiest thing in the block
    to find and the hardest to confuse with prose.
    """
    heading = chunk.section_heading or "(this document has no section headings)"
    return (
        f"### Passage {index}\n"
        f"chunk_id: {chunk.chunk_id}\n"
        f"section: {heading}\n"
        f"location: {_describe_location(chunk)}\n\n"
        f"{chunk.text}"
    )


def _describe_location(chunk: ScoredChunk) -> str:
    """Page 0 means *not paginated*, not page one -- the HTML source has no pages."""
    if not chunk.page_from:
        return "web page, not paginated"
    if chunk.page_to and chunk.page_to != chunk.page_from:
        return f"pages {chunk.page_from}-{chunk.page_to}"
    return f"page {chunk.page_from}"
