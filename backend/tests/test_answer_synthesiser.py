"""Phase 2.5 per-document answering tests (architecture.md §7.2, §8.2).

The central assertion in this file is not that answers come back. It is that
**no generation call ever sees two documents' passages.** That is the property which makes
blending unrepresentable rather than discouraged, and it is invisible in the response: a
single call holding three publishers' chunks would return a fluent, well-formed, cited
answer and look fine. So the fake model client records every prompt it is handed, and the
tests read the prompts rather than only the results.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from db.schemas import DocumentAnswer
from services.answer_synthesiser import (
    DOCUMENT_ANSWER_PROMPT,
    DocumentContext,
    group_by_document,
    render_document_prompt,
    synthesise,
)
from services.citation_validator import CitationError
from services.model_client import ModelResponseError


class RecordingModelClient:
    """Answers per document from a canned map, and keeps every prompt it was given.

    Keyed by document name so a test can give two documents different answers without
    depending on call order -- the calls are concurrent, and a test that depended on their
    order would pass or fail by timing.
    """

    def __init__(self, answers: dict[str, DocumentAnswer], error: Exception | None = None):
        self._answers = answers
        self._error = error
        self.document_prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.questions: list[str] = []

    def answer_from_document(self, system_prompt, document_prompt, history, user_message):
        self.document_prompts.append(document_prompt)
        self.system_prompts.append(system_prompt)
        self.questions.append(user_message)
        if self._error is not None:
            raise self._error
        for name, answer in self._answers.items():
            if name in document_prompt:
                return answer
        raise AssertionError(f"no canned answer matched this prompt:\n{document_prompt[:400]}")


def _answers(answer: str, claims, *, answers_question=True) -> DocumentAnswer:
    return DocumentAnswer(
        answers_question=answers_question,
        answer=answer,
        claims=[{"claim": c, "chunk_id": str(i)} for c, i in claims],
    )


def _declines() -> DocumentAnswer:
    return DocumentAnswer(answers_question=False, answer="", claims=[])


def _run(contexts, client, question="How much free sugar per day?"):
    return synthesise(
        contexts,
        model_client=client,
        system_prompt="SYSTEM PROMPT",
        history=[],
        question=question,
    )


# --- Grouping ------------------------------------------------------------------------


def test_hits_are_grouped_into_one_context_per_document(chunk):
    hits = [
        chunk(slug="doc-a", ordinal=0, score=0.81),
        chunk(slug="doc-b", ordinal=0, score=0.79),
        chunk(slug="doc-a", ordinal=1, score=0.75),
    ]

    contexts = group_by_document(hits)

    assert [c.document.name for c in contexts] == ["Document doc-a", "Document doc-b"]
    assert [len(c.chunks) for c in contexts] == [2, 1]


def test_documents_are_ordered_by_their_best_chunk_not_by_chunk_count(chunk):
    """A document with one strong passage answers better than one with four weak ones.
    Ranking by count would put the Irish food pyramid -- 19 short fragments -- first for
    almost every question."""
    hits = [
        chunk(slug="fragmentary", ordinal=0, score=0.71),
        chunk(slug="fragmentary", ordinal=1, score=0.70),
        chunk(slug="fragmentary", ordinal=2, score=0.70),
        chunk(slug="precise", ordinal=0, score=0.88),
    ]

    contexts = group_by_document(hits)

    assert [c.document.name for c in contexts] == ["Document precise", "Document fragmentary"]


def test_chunks_within_a_document_are_ordered_by_score(chunk):
    hits = [
        chunk(slug="doc-a", ordinal=0, score=0.70),
        chunk(slug="doc-a", ordinal=1, score=0.85),
    ]

    assert [c.chunk_key for c in group_by_document(hits)[0].chunks] == ["doc-a:1", "doc-a:0"]


# --- Prompt rendering: the isolation guarantee ---------------------------------------


def test_rendered_prompt_contains_every_chunk_id_the_model_may_cite(chunk):
    context = group_by_document([chunk(ordinal=0), chunk(ordinal=1)])[0]

    rendered = render_document_prompt(context)

    for c in context.chunks:
        assert str(c.chunk_id) in rendered
        assert c.text in rendered


def test_rendered_prompt_leaves_no_substitution_token_behind(chunk):
    rendered = render_document_prompt(group_by_document([chunk()])[0])

    assert "{{DOCUMENT}}" not in rendered
    assert "{{PASSAGES}}" not in rendered


def test_passage_text_containing_braces_and_percents_survives_rendering(chunk):
    """Government prose is full of `%` and the odd `{`. Rendering with str.format would
    raise on one and silently eat the other."""
    text = "Limit free sugars to <10% of energy {see table 3}."
    rendered = render_document_prompt(group_by_document([chunk(text=text)])[0])

    assert text in rendered


def test_missing_year_is_stated_as_missing_not_omitted(chunk):
    """Three corpus documents state no publication year. The brief forbids inventing one,
    and a prompt that just leaves the year out invites the model to supply a plausible
    one."""
    rendered = render_document_prompt(group_by_document([chunk(year=None)])[0])

    assert "publication year not stated" in rendered


def test_unpaginated_html_chunk_is_not_described_as_page_one(chunk):
    """`page_from=0` means *not paginated*. Rendering it as "page 0" or "page 1" would put
    a false page number into a citation drawn from the WHO web page."""
    rendered = render_document_prompt(group_by_document([chunk(page_from=0, page_to=0)])[0])

    assert "not paginated" in rendered
    assert "page 0" not in rendered


def test_document_without_headings_says_so_rather_than_inventing_one(chunk):
    rendered = render_document_prompt(group_by_document([chunk(section_heading=None)])[0])

    assert "no section headings" in rendered


# --- One document --------------------------------------------------------------------


def test_single_document_question_returns_one_answer_with_cited_claims(chunk):
    hits = [chunk(slug="who", ordinal=3, name="Healthy diet", publisher="WHO", year=2026)]
    contexts = group_by_document(hits)
    client = RecordingModelClient(
        {
            "Healthy diet": _answers(
                "Free sugars should be under 10% of total energy intake.",
                [("Free sugars should be less than 10% of total energy intake.", hits[0].chunk_id)],
            )
        }
    )

    result = _run(contexts, client)

    assert result.answered is True
    assert len(result.document_answers) == 1
    answer = result.document_answers[0]
    assert answer.document.publisher == "WHO"
    assert len(answer.claims) == 1
    assert answer.claims[0].source.chunk_id == hits[0].chunk_id
    assert len(client.document_prompts) == 1


def test_citation_is_expanded_by_the_backend_not_taken_from_the_model(chunk):
    """The model emits a chunk id and nothing else about the source. Publisher, year, URL
    and the quoted passage are filled in from the retrieved row, so a fabricated citation
    is not something the model is in a position to write (architecture.md §8.2)."""
    hits = [
        chunk(
            slug="fsanz",
            ordinal=8,
            name="Food Safety: Temperature control",
            publisher="FSANZ",
            year=2002,
            section_heading="Cold storage",
            page_from=12,
            page_to=13,
            text="Refrigerated food must be kept at or below 5 degrees Celsius.",
        )
    ]
    client = RecordingModelClient(
        {
            "Temperature control": _answers(
                "Keep refrigerated food at or below 5 degrees Celsius.",
                [("Refrigerated food must be kept at or below 5 degrees Celsius.", hits[0].chunk_id)],
            )
        }
    )

    citation = _run(group_by_document(hits), client).document_answers[0].claims[0].source

    assert citation.document.publisher == "FSANZ"
    assert citation.document.year == 2002
    assert citation.document.url == "https://example.org/fsanz.pdf"
    assert citation.section_heading == "Cold storage"
    assert (citation.page_from, citation.page_to) == (12, 13)
    assert citation.quote == hits[0].text


def test_a_single_document_needs_no_thread_pool(chunk):
    """Not a performance assertion -- a traceback one. A failure in the common case should
    not arrive wrapped in a worker thread's stack."""
    client = RecordingModelClient({}, error=ModelResponseError("bad"))

    with pytest.raises(ModelResponseError):
        _run(group_by_document([chunk()]), client)


# --- Two documents: the structural guarantee ------------------------------------------


def _two_document_setup(chunk):
    fssai = chunk(
        slug="fssai",
        ordinal=2,
        name="Repurposing Used Cooking Oil",
        publisher="FSSAI",
        year=2018,
        text="Used cooking oil should not be reused more than three times.",
    )
    who = chunk(
        slug="who-five-keys",
        ordinal=1,
        name="Five Keys to Safer Food",
        publisher="WHO",
        year=2013,
        score=0.74,
        text="Use safe water and raw materials, and avoid reusing oil that has darkened.",
    )
    client = RecordingModelClient(
        {
            "Repurposing Used Cooking Oil": _answers(
                "Used cooking oil should not be reused more than three times.",
                [("Used cooking oil should not be reused more than three times.", fssai.chunk_id)],
            ),
            "Five Keys to Safer Food": _answers(
                "Avoid reusing oil that has darkened.",
                [("Oil that has darkened should not be reused.", who.chunk_id)],
            ),
        }
    )
    return [fssai, who], client


def test_cross_document_question_returns_separate_answers(chunk):
    """Two documents must come back as two answers. Merging them here would make the
    frontend's job impossible and would pick a winner by accident."""
    hits, client = _two_document_setup(chunk)

    result = _run(group_by_document(hits), client, question="Can I reuse cooking oil?")

    assert [a.document.publisher for a in result.document_answers] == ["FSSAI", "WHO"]
    assert len(client.document_prompts) == 2


def test_no_generation_call_ever_sees_two_documents_passages(chunk):
    """The §7.2 guarantee, asserted where it actually lives: in the prompts. Every other
    test in this file would still pass if one call received all the chunks."""
    hits, client = _two_document_setup(chunk)

    _run(group_by_document(hits), client, question="Can I reuse cooking oil?")

    assert len(client.document_prompts) == 2
    for prompt in client.document_prompts:
        present = [str(hit.chunk_id) for hit in hits if str(hit.chunk_id) in prompt]
        assert len(present) == 1, "a prompt carried more than one document's passages"
        assert sum(hit.text in prompt for hit in hits) == 1


def test_each_claim_cites_only_its_own_documents_chunks(chunk):
    """"Never blended", checked from the response side as well: every citation in a
    document's block must resolve to a chunk of that document."""
    hits, client = _two_document_setup(chunk)

    result = _run(group_by_document(hits), client, question="Can I reuse cooking oil?")

    for answer in result.document_answers:
        for claim in answer.claims:
            assert claim.source.document.id == answer.document.id
            assert claim.source.document.publisher == answer.document.publisher


def test_results_follow_context_order_not_completion_order(chunk):
    """The calls are concurrent. If the response took completion order, the same question
    against the same corpus would answer in a different order on a slow day."""
    hits, client = _two_document_setup(chunk)
    contexts = group_by_document(hits)

    result = _run(contexts, client, question="Can I reuse cooking oil?")

    assert [a.document.id for a in result.document_answers] == [c.document.id for c in contexts]


def test_history_and_question_reach_every_document_call(chunk):
    hits, client = _two_document_setup(chunk)

    _run(group_by_document(hits), client, question="Can I reuse cooking oil?")

    assert client.questions == ["Can I reuse cooking oil?"] * 2
    assert client.system_prompts == ["SYSTEM PROMPT"] * 2


# --- Gate 2: every document declines --------------------------------------------------


def test_all_documents_declining_is_not_an_answer(chunk):
    """Gate 2 of the not-in-corpus refusal. The chunks cleared the floor, the model read
    them and said they do not answer the question -- which since the floor's separation
    band narrowed to 0.02 is the gate doing most of the work."""
    hits = [chunk(slug="doc-a"), chunk(slug="doc-b", score=0.70)]
    client = RecordingModelClient({"doc-a": _declines(), "doc-b": _declines()})

    result = _run(group_by_document(hits), client)

    assert result.answered is False
    assert result.document_answers == []
    assert len(result.declined) == 2


def test_one_document_answering_is_an_answer(chunk):
    """A single document with something to say is an answer, not a partial failure."""
    hits = [chunk(slug="doc-a"), chunk(slug="doc-b", score=0.70)]
    client = RecordingModelClient(
        {
            "doc-a": _answers("An answer.", [("A claim.", hits[0].chunk_id)]),
            "doc-b": _declines(),
        }
    )

    result = _run(group_by_document(hits), client)

    assert result.answered is True
    assert [d.name for d in result.declined] == ["Document doc-b"]


def test_declining_documents_still_count_as_chunks_that_were_read(chunk):
    """`retrieval_hits.used` is "was this chunk passed into a generation call". A declining
    document's chunks were, and recording that is what distinguishes a model verdict from
    a retrieval miss when the failure log is read."""
    hits = [chunk(slug="doc-a"), chunk(slug="doc-b", score=0.70)]
    client = RecordingModelClient({"doc-a": _declines(), "doc-b": _declines()})

    result = _run(group_by_document(hits), client)

    assert result.used_chunk_ids == {hit.chunk_id for hit in hits}


def test_no_contexts_means_no_model_call_at_all(chunk):
    """Gate 1 already refused. Reaching the model here would spend a call on a question
    retrieval has said nothing matches."""
    client = RecordingModelClient({})

    result = _run([], client)

    assert result.answered is False
    assert client.document_prompts == []


# --- Failure propagation --------------------------------------------------------------


def test_citation_error_from_any_document_fails_the_whole_response(chunk):
    """Not "return the documents that validated". A response that failed grounding is not
    a degraded result to salvage -- the endpoint turns this into a 502."""
    hits = [chunk(slug="doc-a"), chunk(slug="doc-b", score=0.70)]
    client = RecordingModelClient(
        {
            "doc-a": _answers("An answer.", [("A claim.", hits[0].chunk_id)]),
            "doc-b": _answers("Another answer.", [("A claim.", uuid.uuid4())]),
        }
    )

    with pytest.raises(CitationError):
        _run(group_by_document(hits), client)


def test_uncited_answer_from_any_document_fails_the_whole_response(chunk):
    hits = [chunk(slug="doc-a")]
    client = RecordingModelClient({"doc-a": _answers("An uncited answer.", [])})

    with pytest.raises(CitationError):
        _run(group_by_document(hits), client)


def test_model_schema_failure_propagates_rather_than_being_skipped(chunk):
    hits = [chunk(slug="doc-a"), chunk(slug="doc-b", score=0.70)]
    client = RecordingModelClient({}, error=ModelResponseError("not conformant"))

    with pytest.raises(ModelResponseError):
        _run(group_by_document(hits), client)


def test_lexical_overlap_warning_does_not_stop_the_answer(chunk, caplog):
    """Exit criterion: non-blocking. A claim that paraphrases loosely is logged for the
    failure log and still answered."""
    hits = [chunk(slug="doc-a", text="Keep refrigerated food at or below 5 degrees Celsius.")]
    client = RecordingModelClient(
        {"doc-a": _answers("Chill it promptly.", [("Prompt chilling matters.", hits[0].chunk_id)])}
    )

    with caplog.at_level(logging.WARNING):
        result = _run(group_by_document(hits), client)

    assert result.answered is True
    assert "low_lexical_overlap_with_cited_chunk" in caplog.text


# --- The prompt artifact --------------------------------------------------------------


def test_prompt_template_carries_each_substitution_token_exactly_once():
    """Both halves matter. Zero occurrences produce a prompt with no passages in it, which
    reads as "the document doesn't answer this" rather than as a bug. Two occurrences are
    the defect this file shipped first: replacement is global, so the second copy -- in the
    maintainer comment -- was filled in as well and every prompt carried its passages
    twice, with nothing failing anywhere."""
    assert DOCUMENT_ANSWER_PROMPT.count("{{DOCUMENT}}") == 1
    assert DOCUMENT_ANSWER_PROMPT.count("{{PASSAGES}}") == 1


def test_maintainer_comments_are_not_sent_to_the_model():
    """The file's HTML comments explain how the file is rendered. That is a note to whoever
    edits it, not an instruction to something answering from it -- and it would be paid for
    once per document per turn."""
    assert "<!--" not in DOCUMENT_ANSWER_PROMPT
    assert "Maintainer note" not in DOCUMENT_ANSWER_PROMPT
    assert DOCUMENT_ANSWER_PROMPT.startswith("# Per-document answering")


def test_each_passage_appears_exactly_once_in_a_rendered_prompt(chunk):
    """The response-side half of the same guard: a doubled placeholder is invisible in the
    template if you only check that the tokens are gone afterwards."""
    context = group_by_document([chunk(ordinal=0), chunk(ordinal=1, text="A second passage.")])[0]

    rendered = render_document_prompt(context)

    assert rendered.count("### Passage 1") == 1
    assert rendered.count("### Passage 2") == 1
    for c in context.chunks:
        assert rendered.count(str(c.chunk_id)) == 1


def test_a_duplicated_placeholder_is_rejected_rather_than_doubled(chunk):
    """What the import-time guard protects. Asserted against the function so the rule holds
    for any future template, not only the one currently on disk."""
    from services.answer_synthesiser import _assert_single_placeholder

    with pytest.raises(ValueError, match="expected exactly 1"):
        _assert_single_placeholder("{{PASSAGES}} and again {{PASSAGES}}", "{{PASSAGES}}")

    with pytest.raises(ValueError, match="expected exactly 1"):
        _assert_single_placeholder("no placeholder here", "{{PASSAGES}}")


def test_document_context_exposes_exactly_the_ids_its_call_may_cite(chunk):
    context = DocumentContext(
        document=group_by_document([chunk()])[0].document,
        chunks=(chunk(ordinal=0), chunk(ordinal=1)),
    )

    assert context.chunk_ids == {context.chunks[0].chunk_id, context.chunks[1].chunk_id}
