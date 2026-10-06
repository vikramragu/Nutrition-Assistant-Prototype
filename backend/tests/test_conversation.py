"""Conversation persistence and replay.

Phase 2.6 changed the shape of a turn: one user message plus one assistant message per
document, written in a single commit. Two of those words carry the tests in this file --
*per document* (so grouping has to survive a reload) and *single commit* (so ordering
cannot come from `created_at`).
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from db.models import Chunk, Conversation, Document, Message, Retrieval
from db.schemas import (
    Citation,
    CitedClaim,
    ClaimSchema,
    DocumentAnswerOut,
    DocumentRef,
)
from db.session import SessionLocal
from services.conversation import (
    RetrievalEvidence,
    append_message,
    create_conversation,
    load_history,
    model_history,
    next_ordinal,
    persist_coverage_refusal,
    persist_turn,
    to_conversation_read,
)
from services.retriever import ScoredChunk


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def conversation(db):
    conversation = create_conversation(db)
    yield conversation
    db.query(Conversation).filter_by(id=conversation.id).delete()
    db.commit()


@pytest.fixture(scope="module")
def corpus_chunks():
    """Two chunks from two different real documents.

    Real rows, because `claims.chunk_id` and `retrieval_hits.chunk_id` are foreign keys --
    a test with invented ids would prove nothing about whether a citation can be stored.
    """
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Chunk, Document).join(Document, Document.id == Chunk.document_id)
        ).all()
        if not rows:
            pytest.skip("corpus is not seeded; run `python -m corpus.seed`")
        by_document = {}
        for chunk, document in rows:
            by_document.setdefault(document.id, (chunk, document))
        picked = list(by_document.values())[:2]
        if len(picked) < 2:
            pytest.skip("corpus has fewer than two documents")
        for chunk, document in picked:
            session.expunge(chunk)
            session.expunge(document)
        return picked
    finally:
        session.close()


def _document_answer(chunk, document, answer_text, claim_text):
    ref = DocumentRef(
        id=document.id,
        name=document.name,
        publisher=document.publisher,
        year=document.year,
        url=document.source_url,
    )
    return DocumentAnswerOut(
        document=ref,
        answer=answer_text,
        claims=[
            CitedClaim(
                claim=claim_text,
                source=Citation(
                    chunk_id=chunk.id,
                    document=ref,
                    section_heading=chunk.section_heading,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    quote=chunk.text,
                ),
            )
        ],
    )


def _scored(chunk, document, score):
    return ScoredChunk(
        chunk_id=chunk.id,
        chunk_key=chunk.chunk_key,
        document_id=document.id,
        document_slug=document.slug,
        document_name=document.name,
        publisher=document.publisher,
        year=document.year,
        source_url=document.source_url,
        section_heading=chunk.section_heading,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        text=chunk.text,
        score=score,
    )


def _evidence(hits, used=frozenset(), query="A question"):
    return RetrievalEvidence(
        query_text=query, k=8, floor=0.69, document_filter=None, hits=hits, used_chunk_ids=used
    )


def _cleanup_retrievals(db, query):
    for retrieval in db.scalars(select(Retrieval).where(Retrieval.query_text == query)).all():
        db.delete(retrieval)
    db.commit()


# --- Ordinals ---------------------------------------------------------------------------


def test_next_ordinal_starts_at_zero_and_increments(db, conversation):
    assert next_ordinal(db, conversation.id) == 0

    append_message(db, conversation.id, "user", "First")
    assert next_ordinal(db, conversation.id) == 1

    append_message(db, conversation.id, "assistant", "Second")
    assert next_ordinal(db, conversation.id) == 2


def test_ordinals_are_per_conversation_not_global(db):
    first = create_conversation(db)
    second = create_conversation(db)
    try:
        append_message(db, first.id, "user", "In the first")
        append_message(db, second.id, "user", "In the second")

        assert next_ordinal(db, second.id) == 1  # not 2
    finally:
        for conversation in (first, second):
            db.query(Conversation).filter_by(id=conversation.id).delete()
        db.commit()


def test_history_is_ordered_by_ordinal(db, conversation):
    append_message(db, conversation.id, "user", "First")
    append_message(db, conversation.id, "assistant", "Second")
    append_message(db, conversation.id, "user", "Third")

    loaded = load_history(db, conversation.id)

    assert [m.content for m in loaded.messages] == ["First", "Second", "Third"]
    assert [m.ordinal for m in loaded.messages] == [0, 1, 2]


# --- persist_turn: one commit, one message per document --------------------------------


def test_persist_turn_writes_one_assistant_message_per_document(db, conversation, corpus_chunks):
    (chunk_a, doc_a), (chunk_b, doc_b) = corpus_chunks
    query = "A multi-document question"

    persist_turn(
        db,
        conversation.id,
        user_message=query,
        document_answers=[
            _document_answer(chunk_a, doc_a, "A says this.", "A's claim."),
            _document_answer(chunk_b, doc_b, "B says this.", "B's claim."),
        ],
        evidence=_evidence([_scored(chunk_a, doc_a, 0.85)], {chunk_a.id}, query),
    )
    try:
        loaded = load_history(db, conversation.id)

        assert [m.role for m in loaded.messages] == ["user", "assistant", "assistant"]
        assert [m.ordinal for m in loaded.messages] == [0, 1, 2]
        assert loaded.messages[1].content == "A says this."
        assert loaded.messages[1].claims[0].chunk_id == chunk_a.id
        # The Phase 1 text column stays NULL -- the citation is the foreign key.
        assert loaded.messages[1].claims[0].source is None
    finally:
        _cleanup_retrievals(db, query)


def test_persist_turn_shares_one_timestamp_which_is_why_ordinal_exists(
    db, conversation, corpus_chunks
):
    """Documents the constraint rather than a behaviour: Postgres `now()` is the
    transaction timestamp, so a single-commit turn cannot be ordered by `created_at`.
    If this assertion ever fails, `messages.ordinal` has become optional."""
    (chunk_a, doc_a), (chunk_b, doc_b) = corpus_chunks
    query = "A timestamp question"

    persist_turn(
        db,
        conversation.id,
        user_message=query,
        document_answers=[
            _document_answer(chunk_a, doc_a, "A says this.", "A's claim."),
            _document_answer(chunk_b, doc_b, "B says this.", "B's claim."),
        ],
        evidence=_evidence([_scored(chunk_a, doc_a, 0.85)], {chunk_a.id}, query),
    )
    try:
        messages = db.scalars(
            select(Message).where(Message.conversation_id == conversation.id)
        ).all()
        assert len({m.created_at for m in messages}) == 1
    finally:
        _cleanup_retrievals(db, query)


def test_persist_turn_links_the_retrieval_to_the_first_assistant_message(
    db, conversation, corpus_chunks
):
    (chunk_a, doc_a), _ = corpus_chunks
    query = "An evidence question"

    assistant_rows = persist_turn(
        db,
        conversation.id,
        user_message=query,
        document_answers=[_document_answer(chunk_a, doc_a, "A says this.", "A's claim.")],
        evidence=_evidence([_scored(chunk_a, doc_a, 0.85)], {chunk_a.id}, query),
    )
    try:
        retrieval = db.scalars(select(Retrieval).where(Retrieval.query_text == query)).one()
        assert retrieval.message_id == assistant_rows[0].id
        assert [h.used for h in retrieval.hits] == [True]
    finally:
        _cleanup_retrievals(db, query)


def test_persist_turn_rolls_back_entirely_on_a_bad_citation(db, conversation, corpus_chunks):
    """The single-commit guarantee, tested by breaking it. `claims.chunk_id` is a foreign
    key, so a citation to a nonexistent chunk fails at flush -- and the user message must
    not survive that. A question persisted without its answer replays as a question the
    assistant ignored."""
    (chunk_a, doc_a), _ = corpus_chunks
    orphan = _document_answer(chunk_a, doc_a, "An answer.", "A claim.")
    orphan.claims[0].source.chunk_id = uuid.uuid4()  # no such chunk
    query = "A doomed question"

    with pytest.raises(Exception):
        persist_turn(
            db,
            conversation.id,
            user_message=query,
            document_answers=[orphan],
            evidence=_evidence([_scored(chunk_a, doc_a, 0.85)], {chunk_a.id}, query),
        )
    db.rollback()

    assert db.scalars(select(Message).where(Message.conversation_id == conversation.id)).all() == []


# --- Coverage refusals ------------------------------------------------------------------


def test_coverage_refusal_records_hits_but_writes_no_messages(db, conversation, corpus_chunks):
    (chunk_a, doc_a), _ = corpus_chunks
    query = "An uncovered question"

    persist_coverage_refusal(
        db, conversation.id, _evidence([_scored(chunk_a, doc_a, 0.60)], frozenset(), query)
    )
    try:
        retrieval = db.scalars(select(Retrieval).where(Retrieval.query_text == query)).one()
        assert retrieval.message_id is None
        assert [h.used for h in retrieval.hits] == [False]
        assert retrieval.hits[0].score == pytest.approx(0.60)
        assert (
            db.scalars(select(Message).where(Message.conversation_id == conversation.id)).all() == []
        )
    finally:
        _cleanup_retrievals(db, query)


# --- Replay -----------------------------------------------------------------------------


def test_model_history_folds_a_turns_documents_into_one_assistant_turn(db, conversation):
    """Storage keeps one row per document because that is what was answered. The model
    should see one reply per turn, because that is what the conversation sounded like."""
    append_message(db, conversation.id, "user", "What does the guidance say?")
    append_message(db, conversation.id, "assistant", "A says this.")
    append_message(db, conversation.id, "assistant", "B says this.")
    append_message(db, conversation.id, "user", "And the next question?")

    history = model_history(load_history(db, conversation.id))

    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[1]["content"] == "A says this.\n\nB says this."


def test_model_history_keeps_each_document_in_its_own_paragraph(db, conversation):
    """Separated by a blank line, never merged into one sentence -- that would be the §7.2
    blending, reintroduced through the back door of conversation history."""
    append_message(db, conversation.id, "user", "A question")
    append_message(db, conversation.id, "assistant", "First document.")
    append_message(db, conversation.id, "assistant", "Second document.")

    history = model_history(load_history(db, conversation.id))

    assert history[1]["content"].count("\n\n") == 1


def test_model_history_of_an_empty_conversation_is_empty(db, conversation):
    assert model_history(load_history(db, conversation.id)) == []


def test_to_conversation_read_derives_the_document_from_the_claims(
    db, conversation, corpus_chunks
):
    """`messages.document_id` is deliberately not stored. The document is derived from the
    citation, which cannot drift from the corpus -- and citation_validator rule 2
    guarantees there is always a claim to derive it from."""
    (chunk_a, doc_a), _ = corpus_chunks
    query = "A derivation question"

    persist_turn(
        db,
        conversation.id,
        user_message=query,
        document_answers=[_document_answer(chunk_a, doc_a, "A says this.", "A's claim.")],
        evidence=_evidence([_scored(chunk_a, doc_a, 0.85)], {chunk_a.id}, query),
    )
    try:
        read = to_conversation_read(load_history(db, conversation.id))

        user, assistant = read.messages
        assert user.document is None
        assert assistant.document.id == doc_a.id
        assert assistant.document.publisher == doc_a.publisher
        assert assistant.claims[0].source.chunk_id == chunk_a.id
        assert assistant.claims[0].source.quote == chunk_a.text
    finally:
        _cleanup_retrievals(db, query)


def test_phase_one_style_row_reads_back_with_a_null_citation(db, conversation):
    """The Phase 2.3 criterion that was only vacuously verified, made real: a row with no
    `chunk_id` must still read, with `source` null rather than raising."""
    append_message(
        db,
        conversation.id,
        "assistant",
        "An uncited Phase 1 answer.",
        claims=[ClaimSchema(claim="An uncited claim.")],
    )

    read = to_conversation_read(load_history(db, conversation.id))

    assert read.messages[0].document is None
    assert read.messages[0].claims[0].source is None
    assert read.messages[0].claims[0].legacy_source is None


def test_load_history_returns_none_for_unknown_conversation(db):
    assert load_history(db, uuid.uuid4()) is None


# --- The Phase 1 claim schema (unchanged) -----------------------------------------------


def test_claim_schema_rejects_non_null_source():
    with pytest.raises(ValidationError):
        ClaimSchema(claim="Vitamin C prevents colds.", source="https://example.com/study")


def test_claim_schema_accepts_null_source():
    assert ClaimSchema(claim="Bananas contain potassium.").source is None
