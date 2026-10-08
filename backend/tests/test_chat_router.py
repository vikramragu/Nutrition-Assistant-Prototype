"""Phase 2.6 endpoint tests — the three wire types and the gates that choose between them.

These run against the **real seeded corpus**, deliberately. `claims.chunk_id` and
`retrieval_hits.chunk_id` are foreign keys to `chunks`, so a test using invented ids would
pass while proving nothing about whether a citation can actually be stored. Retrieval
itself is stubbed through the `get_searcher` seam -- the question here is what the endpoint
does with hits, not whether retrieval finds them, which `eval/run_retrieval_eval.py`
measures against the corpus.

The model is always a fake. No test in this file spends a Groq credit.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from db.models import Chunk, Conversation, Document, Message, Retrieval, ScopeRefusal
from db.schemas import DocumentAnswer
from db.session import SessionLocal
from main import app
from routers.chat import get_model_client, get_searcher
from services.citation_validator import CitationError
from services.model_client import ModelResponseError, ModelUnavailableError
from services.retriever import DEFAULT_FLOOR, ScoredChunk

# Comfortably above and below the calibrated floor of 0.69, so these tests do not move
# when the floor is re-measured -- only when it moves by more than 0.08.
ABOVE_FLOOR = 0.80
BELOW_FLOOR = 0.60


class _FakeModelClient:
    """Answers per document from a canned map keyed by a substring of the document name."""

    def __init__(self, answers: dict[str, DocumentAnswer] | None = None, error: Exception | None = None):
        self._answers = answers or {}
        self._error = error
        self.calls = 0

    def answer_from_document(self, system_prompt, document_prompt, history, user_message):
        self.calls += 1
        if self._error is not None:
            raise self._error
        for key, answer in self._answers.items():
            if key in document_prompt:
                return answer
        raise AssertionError(f"no canned answer matched:\n{document_prompt[:300]}")


class _SpySearcher:
    """Records every call, so "did retrieval run" is observable rather than inferred."""

    def __init__(self, hits: list[ScoredChunk] | None = None):
        self._hits = hits or []
        self.calls: list[dict] = []

    def __call__(self, session, query, *, k, document_id=None):
        self.calls.append({"query": query, "k": k, "document_id": document_id})
        if document_id is not None:
            return [h for h in self._hits if h.document_id == document_id]
        return list(self._hits)

    @property
    def called(self) -> bool:
        return bool(self.calls)


def _scored(chunk: Chunk, document: Document, score: float) -> ScoredChunk:
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


def _answers(answer: str, claims: list[tuple[str, uuid.UUID]], *, answers_question=True):
    return DocumentAnswer(
        answers_question=answers_question,
        answer=answer,
        claims=[{"claim": c, "chunk_id": str(i)} for c, i in claims],
    )


def _declines():
    return DocumentAnswer(answers_question=False, answer="", claims=[])


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def conversation_id(db):
    """A conversation, cleaned up with everything the turn wrote.

    Retrievals from a *coverage refusal* have `message_id = NULL`, so deleting the
    conversation does not cascade to them. They are collected by diffing the table rather
    than by matching on query text, which would miss a question a test phrased differently.
    """
    before = set(db.scalars(select(Retrieval.id)).all())

    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    cid = conversation.id

    yield cid

    db.query(Conversation).filter_by(id=cid).delete()
    db.commit()
    for leftover in set(db.scalars(select(Retrieval.id)).all()) - before:
        db.query(Retrieval).filter_by(id=leftover).delete()
    db.commit()


@pytest.fixture(scope="module")
def two_documents():
    """One chunk from each of two different real corpus documents."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Chunk, Document).join(Document, Document.id == Chunk.document_id)
        ).all()
        if not rows:
            pytest.skip("corpus is not seeded; run `python -m corpus.seed`")

        by_document: dict[uuid.UUID, tuple[Chunk, Document]] = {}
        for chunk, document in rows:
            by_document.setdefault(document.id, (chunk, document))
        picked = list(by_document.values())[:2]
        if len(picked) < 2:
            pytest.skip("corpus has fewer than two documents")
        # Detach so the rows stay usable after the session closes.
        for chunk, document in picked:
            session.expunge(chunk)
            session.expunge(document)
        return picked
    finally:
        session.close()


@pytest.fixture()
def client():
    return TestClient(app)


def _post(client, conversation_id, message, **extra):
    return client.post(
        "/chat", json={"conversation_id": str(conversation_id), "message": message, **extra}
    )


def _corpus_size(db) -> int:
    return len(db.scalars(select(Document.id)).all())


# --- Routing basics -------------------------------------------------------------------


def test_post_conversations_creates_a_conversation(client, db):
    response = client.post("/conversations")
    assert response.status_code == 201
    conv_id = uuid.UUID(response.json()["id"])
    try:
        assert db.get(Conversation, conv_id) is not None
    finally:
        db.query(Conversation).filter_by(id=conv_id).delete()
        db.commit()


def test_unknown_conversation_404s_before_retrieval(client):
    searcher = _SpySearcher()
    app.dependency_overrides[get_searcher] = lambda: searcher
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient()

    response = _post(client, uuid.uuid4(), "How much protein do I need?")

    assert response.status_code == 404
    assert searcher.called is False


# --- The pre-model gate runs BEFORE retrieval ------------------------------------------


def test_out_of_scope_question_refuses_before_any_retrieval_call(client, conversation_id):
    """Exit criterion, asserted via a spy rather than by reading the code. The order of two
    lines in the endpoint is the whole guarantee, and nothing about the response reveals
    which came first."""
    searcher = _SpySearcher()
    model = _FakeModelClient()
    app.dependency_overrides[get_searcher] = lambda: searcher
    app.dependency_overrides[get_model_client] = lambda: model

    response = _post(client, conversation_id, "How many calories should I eat to lose weight?")

    assert response.status_code == 200
    assert response.json()["type"] == "refused"
    assert response.json()["reason"] == "calorie_target"
    assert searcher.called is False, "retrieval ran on an out-of-scope question"
    assert model.calls == 0


def test_a_calorie_figure_in_the_corpus_does_not_let_a_calorie_question_through(
    client, conversation_id, two_documents
):
    """The corpus genuinely contains calorie figures -- the DGA states them. If the gate
    ran after retrieval, those chunks would be sitting in context for exactly the question
    that must never be answered. The searcher here is primed with a strong hit to make the
    temptation real; it must still never be consulted."""
    chunk, document = two_documents[0]
    searcher = _SpySearcher([_scored(chunk, document, 0.95)])
    model = _FakeModelClient()
    app.dependency_overrides[get_searcher] = lambda: searcher
    app.dependency_overrides[get_model_client] = lambda: model

    response = _post(client, conversation_id, "What's my daily calorie target?")

    assert response.json()["type"] == "refused"
    assert response.json()["reason"] == "calorie_target"
    assert searcher.called is False
    assert model.calls == 0


def test_policy_refusal_is_persisted_as_a_scope_refusal(client, conversation_id, db):
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher()
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient()

    _post(client, conversation_id, "What should I weigh?")

    refusals = db.scalars(
        select(ScopeRefusal).where(ScopeRefusal.conversation_id == conversation_id)
    ).all()
    assert [r.stage for r in refusals] == ["pre_model"]
    assert refusals[0].category == "weight_target"
    # A refused turn is not part of the conversation.
    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []


# --- Answers ---------------------------------------------------------------------------


def test_single_document_question_returns_one_cited_block(client, conversation_id, two_documents):
    chunk, document = two_documents[0]
    searcher = _SpySearcher([_scored(chunk, document, ABOVE_FLOOR)])
    model = _FakeModelClient(
        {document.name: _answers("The document says so.", [("The document says so.", chunk.id)])}
    )
    app.dependency_overrides[get_searcher] = lambda: searcher
    app.dependency_overrides[get_model_client] = lambda: model

    body = _post(client, conversation_id, "What does the guidance say?").json()

    assert body["type"] == "answer"
    assert len(body["document_answers"]) == 1
    claim = body["document_answers"][0]["claims"][0]
    assert claim["source"]["chunk_id"] == str(chunk.id)
    assert claim["source"]["document"]["publisher"] == document.publisher
    assert claim["source"]["quote"] == chunk.text


def test_every_claim_carries_a_resolvable_citation_with_a_link(
    client, conversation_id, two_documents
):
    """No claim ships without a citation, and the citation must be checkable by hand:
    document, publisher, year and a URL (problemStatement.md §8)."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {document.name: _answers("An answer.", [("A claim.", chunk.id)])}
    )

    body = _post(client, conversation_id, "What does the guidance say?").json()

    source = body["document_answers"][0]["claims"][0]["source"]
    assert source["document"]["name"]
    assert source["document"]["publisher"]
    assert source["document"]["url"].startswith("http")
    assert "year" in source["document"]  # may be null; must not be absent


def test_cross_document_question_returns_two_separate_blocks(
    client, conversation_id, two_documents
):
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            doc_a.name: _answers("A says this.", [("A's claim.", chunk_a.id)]),
            doc_b.name: _answers("B says this.", [("B's claim.", chunk_b.id)]),
        }
    )

    body = _post(client, conversation_id, "What does the guidance say?").json()

    assert len(body["document_answers"]) == 2
    ids = [a["document"]["id"] for a in body["document_answers"]]
    assert ids == [str(doc_a.id), str(doc_b.id)]  # strongest document first
    # Never blended: every claim cites its own block's document.
    for answer in body["document_answers"]:
        for claim in answer["claims"]:
            assert claim["source"]["document"]["id"] == answer["document"]["id"]


def test_document_filter_scopes_retrieval_and_the_searched_list(
    client, conversation_id, two_documents
):
    """The brief's second retrieval mode. A filtered question that finds nothing has
    genuinely only searched that one document, so `searched` narrows with it."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    searcher = _SpySearcher([_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)])
    app.dependency_overrides[get_searcher] = lambda: searcher
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {doc_b.name: _answers("B says this.", [("B's claim.", chunk_b.id)])}
    )

    body = _post(
        client, conversation_id, "What does the guidance say?", document_filter=str(doc_b.id)
    ).json()

    assert searcher.calls[0]["document_id"] == doc_b.id
    assert [a["document"]["id"] for a in body["document_answers"]] == [str(doc_b.id)]
    assert [d["id"] for d in body["searched"]] == [str(doc_b.id)]


# --- Coverage refusal: both gates -------------------------------------------------------


def test_nothing_above_the_floor_refuses_without_a_model_call(
    client, conversation_id, two_documents, db
):
    """Gate 1. The cheap gate: no generation call, no credits."""
    chunk, document = two_documents[0]
    model = _FakeModelClient()
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, BELOW_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: model

    body = _post(client, conversation_id, "What is the best creatine dose?").json()

    assert body["type"] == "not_in_corpus"
    assert model.calls == 0
    assert len(body["searched"]) == _corpus_size(db)


def test_every_document_declining_refuses_as_not_in_corpus(
    client, conversation_id, two_documents, db
):
    """Gate 2, and the stronger of the two: these chunks cleared the floor, and the model
    read them and said they do not answer the question."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    model = _FakeModelClient({doc_a.name: _declines(), doc_b.name: _declines()})
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: model

    body = _post(client, conversation_id, "Something the corpus doesn't answer").json()

    assert body["type"] == "not_in_corpus"
    assert model.calls == 2  # both documents were genuinely asked
    assert len(body["searched"]) == _corpus_size(db)


def test_coverage_refusal_names_the_full_corpus_not_just_what_scored_well(
    client, conversation_id, two_documents, db
):
    """Exit criterion, and the reason `GET /corpus` exists. One document cleared the floor
    and declined; the refusal must still name every document, including the ones that
    scored nothing -- a user cannot judge the gap from a list of near-misses."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {document.name: _declines()}
    )

    body = _post(client, conversation_id, "Something the corpus doesn't answer").json()

    corpus_size = _corpus_size(db)
    assert len(body["searched"]) == corpus_size
    assert corpus_size > 1, "this test is vacuous on a one-document corpus"


def test_coverage_refusal_persists_the_retrieval_with_zero_used_hits(
    client, conversation_id, two_documents, db
):
    """architecture.md §9.2. The evidence of how close it came is what separates a correct
    refusal from `over_refusal` when the failure log is read."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, BELOW_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient()

    _post(client, conversation_id, "A question with no coverage")

    retrieval = db.scalars(
        select(Retrieval).where(Retrieval.query_text == "A question with no coverage")
    ).one()
    assert retrieval.floor == pytest.approx(DEFAULT_FLOOR)
    assert [hit.used for hit in retrieval.hits] == [False]
    assert retrieval.hits[0].score == pytest.approx(BELOW_FLOOR)


def test_the_two_refusals_are_distinguishable_without_parsing_prose(
    client, conversation_id, two_documents
):
    """Exit criterion. They mean opposite things -- one says the assistant will not, the
    other says the corpus does not -- so a client must not have to read the message to
    tell them apart."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, BELOW_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient()

    coverage = _post(client, conversation_id, "What is the best creatine dose?").json()
    policy = _post(client, conversation_id, "What should I weigh?").json()

    assert coverage["type"] == "not_in_corpus"
    assert policy["type"] == "refused"
    assert "searched" in coverage and "reason" not in coverage
    assert "reason" in policy and "searched" not in policy


# --- Personalisation (architecture.md §9.1) --------------------------------------------


def test_population_guidance_converted_into_personal_advice_is_blocked(
    client, conversation_id, two_documents, db
):
    """Exit criterion. The corpus says "adults should limit free sugars to less than 10% of
    total energy". Restating that is correct; turning it into "you should keep yours under
    50 g" is the violation retrieval made possible."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            document.name: _answers(
                "You should keep your free sugars under 50 g a day.",
                [("Free sugars should be limited.", chunk.id)],
            )
        }
    )

    response = _post(client, conversation_id, "How much sugar is too much?")
    body = response.json()

    assert body["type"] == "refused"
    assert body["reason"] == "personalised_guidance"
    assert "50 g" not in response.text, "the blocked answer leaked into the refusal"
    # Never persisted.
    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []
    refusal = db.scalars(
        select(ScopeRefusal).where(ScopeRefusal.conversation_id == conversation_id)
    ).one()
    assert refusal.stage == "post_model"


def test_population_level_restatement_is_not_blocked(client, conversation_id, two_documents):
    """The other half, and the one that matters more: the rule must not refuse the corpus's
    own phrasing. Population guidance is itself written prescriptively, so a rule keyed on
    "should + a number" would refuse almost every correct answer."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            document.name: _answers(
                "Adults should limit free sugars to less than 10% of total energy intake.",
                [("Adults should limit free sugars to under 10% of total energy.", chunk.id)],
            )
        }
    )

    body = _post(client, conversation_id, "How much sugar is too much?").json()

    assert body["type"] == "answer"


def test_one_blocked_answer_discards_the_whole_turn(client, conversation_id, two_documents, db):
    """Not "ship the clean document and drop the other". Shipping the rest of a turn that
    produced a prohibited answer leaks the context that made it prohibited."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            doc_a.name: _answers("A perfectly fine answer.", [("A claim.", chunk_a.id)]),
            doc_b.name: _answers(
                "In your case you should aim for 30 g of fibre.", [("A claim.", chunk_b.id)]
            ),
        }
    )

    body = _post(client, conversation_id, "How much fibre?").json()

    assert body["type"] == "refused"
    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []


# --- Hard failures: the 502 path (the three criteria deferred from Phase 2.5) ----------


def test_citing_a_chunk_not_in_context_returns_502_and_persists_nothing(
    client, conversation_id, two_documents, db
):
    """Phase 2.5 exit criterion, closed here. Not a dropped claim, not a repaired answer."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        # Cites a real chunk -- from the document this call was never given.
        {doc_a.name: _answers("An answer.", [("A claim.", chunk_b.id)])}
    )

    response = _post(client, conversation_id, "What does the guidance say?")

    assert response.status_code == 502
    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []


def test_fabricated_chunk_id_returns_502(client, conversation_id, two_documents):
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {document.name: _answers("An answer.", [("A claim.", uuid.uuid4())])}
    )

    assert _post(client, conversation_id, "What does the guidance say?").status_code == 502


def test_non_empty_answer_with_no_claims_returns_502(client, conversation_id, two_documents):
    """Phase 2.5 exit criterion, closed here. An uncited answer does not ship."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {document.name: _answers("An uncited answer.", [])}
    )

    assert _post(client, conversation_id, "What does the guidance say?").status_code == 502


def test_model_schema_failure_returns_502_and_persists_nothing(
    client, conversation_id, two_documents, db
):
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelResponseError("not conformant")
    )

    response = _post(client, conversation_id, "What does the guidance say?")

    assert response.status_code == 502
    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []


def test_citation_error_and_schema_error_are_both_502_but_distinct_details(
    client, conversation_id, two_documents
):
    """Same status, different cause. The detail is what tells an operator which of the two
    happened without reading the log."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=CitationError("cited something it was not given")
    )
    citation = _post(client, conversation_id, "What does the guidance say?")

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelResponseError("not conformant")
    )
    schema = _post(client, conversation_id, "What does the guidance say?")

    assert citation.status_code == schema.status_code == 502
    assert citation.json()["detail"] != schema.json()["detail"]


# --- Persistence and replay -------------------------------------------------------------


def test_a_two_document_turn_persists_as_one_user_and_two_assistant_messages(
    client, conversation_id, two_documents, db
):
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            doc_a.name: _answers("A says this.", [("A's claim.", chunk_a.id)]),
            doc_b.name: _answers("B says this.", [("B's claim.", chunk_b.id)]),
        }
    )

    _post(client, conversation_id, "What does the guidance say?")

    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.ordinal)
    ).all()
    assert [m.role for m in messages] == ["user", "assistant", "assistant"]
    assert [m.ordinal for m in messages] == [0, 1, 2]


def test_ordinals_survive_a_single_transaction_where_timestamps_cannot(
    client, conversation_id, two_documents, db
):
    """The reason `messages.ordinal` exists. Postgres `now()` is the transaction timestamp,
    so a single-commit turn gives every row the same `created_at` -- ordering by it would
    scramble the turn, including which message was the user's."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            doc_a.name: _answers("A says this.", [("A's claim.", chunk_a.id)]),
            doc_b.name: _answers("B says this.", [("B's claim.", chunk_b.id)]),
        }
    )

    _post(client, conversation_id, "What does the guidance say?")

    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.ordinal)
    ).all()
    assert len({m.created_at for m in messages}) == 1, "premise of this test no longer holds"
    assert [m.ordinal for m in messages] == [0, 1, 2]


def test_turn_persists_the_retrieval_evidence_with_used_hits(
    client, conversation_id, two_documents, db
):
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, BELOW_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {doc_a.name: _answers("A says this.", [("A's claim.", chunk_a.id)])}
    )

    _post(client, conversation_id, "A question with partial coverage")

    retrieval = db.scalars(
        select(Retrieval).where(Retrieval.query_text == "A question with partial coverage")
    ).one()
    # Both hits recorded; only the one that cleared the floor reached a generation call.
    assert len(retrieval.hits) == 2
    assert {h.used for h in retrieval.hits} == {True, False}


def test_history_reloads_as_grouped_cited_blocks(client, conversation_id, two_documents):
    """Exit criterion, and what makes a page refresh work: each assistant message carries
    its own document and its own cited claims."""
    (chunk_a, doc_a), (chunk_b, doc_b) = two_documents
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk_a, doc_a, 0.85), _scored(chunk_b, doc_b, 0.75)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        {
            doc_a.name: _answers("A says this.", [("A's claim.", chunk_a.id)]),
            doc_b.name: _answers("B says this.", [("B's claim.", chunk_b.id)]),
        }
    )
    assert _post(client, conversation_id, "What does the guidance say?").status_code == 200

    body = client.get(f"/conversations/{conversation_id}").json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "assistant"]
    user, first, second = body["messages"]
    assert user["document"] is None
    assert first["document"]["id"] == str(doc_a.id)
    assert second["document"]["id"] == str(doc_b.id)
    assert first["claims"][0]["source"]["chunk_id"] == str(chunk_a.id)
    assert first["claims"][0]["source"]["quote"] == chunk_a.text
    assert first["claims"][0]["legacy_source"] is None


def test_get_conversation_404_for_unknown_conversation(client):
    assert client.get(f"/conversations/{uuid.uuid4()}").status_code == 404


# --- Provider availability (Phase 2.10) ---


def test_provider_rate_limit_is_503_not_500(client, conversation_id, two_documents):
    """A Groq rate limit used to escape as an unhandled exception and an opaque HTTP 500.

    Found by the Phase 2.10 failure-log run against production, which hit the free-tier
    limit on its second question. The eval harnesses had grown backoff for exactly this in
    Phase 2.8; the application had not, so the one path a real user takes was the only one
    left unprotected.

    503 rather than 502 because the distinction is the whole point: 502 means the model
    answered badly, 503 means it did not answer at all and the user should retry.
    """
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelUnavailableError("RateLimitError: 429")
    )

    response = _post(client, conversation_id, "What does the guidance say?")

    assert response.status_code == 503
    assert "try again" in response.json()["detail"].lower()


def test_provider_outage_persists_nothing(client, conversation_id, two_documents, db):
    """A transient provider failure must leave the conversation exactly as it was, so a
    retry starts clean rather than replaying a half-written turn."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )
    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelUnavailableError("APITimeoutError")
    )

    _post(client, conversation_id, "What does the guidance say?")

    assert db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all() == []


def test_unavailable_and_malformed_are_different_statuses(
    client, conversation_id, two_documents
):
    """Same endpoint, two provider problems, two statuses - so an operator can tell
    "the model is busy" from "the model broke the contract" without reading a log."""
    chunk, document = two_documents[0]
    app.dependency_overrides[get_searcher] = lambda: _SpySearcher(
        [_scored(chunk, document, ABOVE_FLOOR)]
    )

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelUnavailableError("RateLimitError")
    )
    unavailable = _post(client, conversation_id, "What does the guidance say?")

    app.dependency_overrides[get_model_client] = lambda: _FakeModelClient(
        error=ModelResponseError("not conformant")
    )
    malformed = _post(client, conversation_id, "What does the guidance say?")

    assert (unavailable.status_code, malformed.status_code) == (503, 502)
