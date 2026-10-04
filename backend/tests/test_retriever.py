"""Phase 2.4 retriever tests.

These run without a database and without the embedding model: both are stubbed, and the
SQL is checked by compiling the statement rather than executing it. The point is to pin
the behaviour that is easy to break silently -- using the wrong embedding method, getting
the distance-to-similarity conversion backwards, or letting the floor filter before the
`LIMIT` instead of after.

Whether retrieval actually finds the right passages is a *measurement*, not an assertion,
and lives in eval/run_retrieval_eval.py against the real corpus.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from services.embeddings import EMBEDDING_DIM, QUERY_PREFIX
from services.retriever import DEFAULT_FLOOR, DEFAULT_K, ScoredChunk, retrieve, search


class StubEmbeddingClient:
    """Records which method was called. The distinction is the whole bug class."""

    model_id = "stub"
    dimension = EMBEDDING_DIM

    def __init__(self) -> None:
        self.query_calls: list[str] = []
        self.passage_calls: list[list[str]] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [0.1] * EMBEDDING_DIM

    def embed_passages(self, texts) -> list[list[float]]:
        self.passage_calls.append(list(texts))
        return [[0.1] * EMBEDDING_DIM for _ in texts]


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    """Captures the statement and returns canned (chunk, document, distance) rows."""

    def __init__(self, rows=()):
        self.rows = rows
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(list(self.rows))


class FakeChunk:
    def __init__(self, ordinal: int):
        self.id = uuid.uuid5(uuid.NAMESPACE_DNS, f"chunk{ordinal}")
        self.chunk_key = f"doc:{ordinal}"
        self.document_id = uuid.uuid5(uuid.NAMESPACE_DNS, "doc")
        self.ordinal = ordinal
        self.section_heading = "A heading"
        self.page_from = 1
        self.page_to = 1
        self.text = f"chunk {ordinal} text"


class FakeDocument:
    def __init__(self):
        self.id = uuid.uuid5(uuid.NAMESPACE_DNS, "doc")
        self.slug = "doc"
        self.name = "A Document"
        self.publisher = "A Publisher"
        self.year = 2024
        self.source_url = "https://example.org/doc.pdf"


def _rows(*distances: float):
    doc = FakeDocument()
    return [(FakeChunk(i), doc, d) for i, d in enumerate(distances)]


def _sql(session: FakeSession) -> str:
    return str(
        session.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


def test_uses_embed_query_not_embed_passages():
    """The BGE prefix belongs on the query side. Calling the wrong method degrades
    retrieval silently and presents as over-refusal (services/embeddings.py)."""
    client = StubEmbeddingClient()
    search(FakeSession(), "how much salt", client=client)

    assert client.query_calls == ["how much salt"]
    assert client.passage_calls == []


def test_query_text_is_not_pre_prefixed_by_the_caller():
    """The prefix is the client's job. Applying it here too would double it."""
    client = StubEmbeddingClient()
    search(FakeSession(), "how much salt", client=client)

    assert not client.query_calls[0].startswith(QUERY_PREFIX)


def test_score_is_one_minus_cosine_distance():
    """pgvector's <=> is distance; similarity is 1 - distance. Getting this backwards
    would rank the least relevant chunk first while every score still looked plausible."""
    session = FakeSession(_rows(0.2, 0.35))
    hits = search(session, "q", client=StubEmbeddingClient())

    assert [round(h.score, 6) for h in hits] == [0.8, 0.65]


def test_results_are_ordered_by_distance_in_sql_not_in_python():
    """The LIMIT must be applied by the database, or `k` means nothing."""
    session = FakeSession(_rows(0.1))
    search(session, "q", k=5, client=StubEmbeddingClient())

    sql = _sql(session).lower()
    assert "order by" in sql
    assert "limit" in sql


def test_document_filter_scopes_results():
    """The brief's second retrieval mode: search within one named document."""
    session = FakeSession(_rows(0.1))
    document_id = uuid.uuid4()
    search(session, "q", document_id=document_id, client=StubEmbeddingClient())

    assert "document_id = " in _sql(session).lower().replace("chunks.", "")


def test_no_document_filter_emits_no_document_predicate():
    session = FakeSession(_rows(0.1))
    search(session, "q", client=StubEmbeddingClient())

    assert "where" not in _sql(session).lower()


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_blank_query_short_circuits(query):
    """No embedding call, no database round trip."""
    client = StubEmbeddingClient()
    session = FakeSession(_rows(0.1))
    assert search(session, query, client=client) == []
    assert client.query_calls == []
    assert session.statements == []


def test_retrieve_drops_chunks_below_the_floor():
    session = FakeSession(_rows(0.2, 0.5))  # scores 0.80, 0.50
    hits = retrieve(session, "q", floor=0.65, client=StubEmbeddingClient())

    assert [h.chunk_key for h in hits] == ["doc:0"]


def test_retrieve_returns_empty_when_everything_is_below_the_floor():
    """An empty list is gate 1 of the not-in-corpus refusal, not an error."""
    session = FakeSession(_rows(0.6, 0.7))  # scores 0.40, 0.30
    assert retrieve(session, "q", floor=DEFAULT_FLOOR, client=StubEmbeddingClient()) == []


def test_floor_is_applied_after_the_limit_not_inside_the_query():
    """Top-k first, then the floor. Filtering in SQL would let a low-scoring chunk be
    promoted into the top k and change what the answer layer sees."""
    session = FakeSession(_rows(0.1))
    retrieve(session, "q", k=3, floor=0.9, client=StubEmbeddingClient())

    sql = _sql(session).lower()
    assert "limit" in sql
    assert "0.9" not in sql


def test_floor_boundary_is_inclusive():
    session = FakeSession(_rows(0.35))  # score exactly 0.65
    hits = retrieve(session, "q", floor=0.65, client=StubEmbeddingClient())

    assert len(hits) == 1


def test_calibrated_defaults_match_the_recorded_measurement():
    """Guards the numbers in docs/features/rag-sourced-claims/retrieval-calibration.md.

    If someone changes these, this test fails and points at the document that has to be
    re-measured -- the floor is the refusal, not a tuning knob.
    """
    assert DEFAULT_FLOOR == 0.65
    assert DEFAULT_K == 8


def test_scored_chunk_carries_everything_a_citation_needs():
    """A citation must be buildable without a second query (architecture.md §8.2)."""
    session = FakeSession(_rows(0.2))
    hit = search(session, "q", client=StubEmbeddingClient())[0]

    assert isinstance(hit, ScoredChunk)
    for field in ("chunk_id", "document_name", "publisher", "source_url", "section_heading", "text"):
        assert getattr(hit, field) is not None
