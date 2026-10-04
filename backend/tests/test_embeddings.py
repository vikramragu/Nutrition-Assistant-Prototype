"""Tests for the embedding client (architecture.md §5.5).

The prefix asymmetry is the thing under test. It is worth a dedicated test file because
getting it wrong raises nothing, logs nothing, and degrades retrieval in a way that
presents as over-refusal rather than as a bug -- the single hardest failure in this
pipeline to notice after the fact.
"""

from __future__ import annotations

import math

import pytest

from services.embeddings import (
    EMBEDDING_DIM,
    QUERY_PREFIX,
    EmbeddingClient,
    FastEmbedClient,
)

pytestmark = pytest.mark.filterwarnings("ignore")

TEXT = "How long can cooked chicken stay in the fridge?"


@pytest.fixture(scope="module")
def client() -> FastEmbedClient:
    try:
        return FastEmbedClient()
    except Exception as exc:  # noqa: BLE001 -- model weights unavailable offline
        pytest.skip(f"embedding model unavailable: {exc}")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def test_query_and_passage_encodings_differ(client: FastEmbedClient) -> None:
    """The core guarantee: identical text encodes differently on each side.

    If someone drops the prefix from `embed_query`, these two vectors become identical
    and this test fails. Nothing else in the codebase would notice.
    """
    query = client.embed_query(TEXT)
    passage = client.embed_passages([TEXT])[0]

    assert query != passage
    # Related but distinct -- same sentence, different instruction. A near-zero or
    # near-one similarity would both suggest something is wrong.
    similarity = _cosine(query, passage)
    assert 0.8 < similarity < 0.999, f"unexpected query/passage similarity {similarity}"


def test_query_encoding_equals_manually_prefixed_passage(client: FastEmbedClient) -> None:
    """Pins *which* prefix is applied, not merely that one is.

    A typo in QUERY_PREFIX would still produce an asymmetry and still pass the test
    above, while silently costing retrieval quality.
    """
    query = client.embed_query(TEXT)
    manual = client.embed_passages([QUERY_PREFIX + TEXT])[0]

    assert _cosine(query, manual) > 0.9999


def test_passage_encoding_has_no_prefix(client: FastEmbedClient) -> None:
    """Passages must be bare. Prefixing both sides destroys the asymmetry entirely."""
    passage = client.embed_passages([TEXT])[0]
    prefixed = client.embed_passages([QUERY_PREFIX + TEXT])[0]

    assert _cosine(passage, prefixed) < 0.999


def test_dimension_matches_the_vector_column(client: FastEmbedClient) -> None:
    """384 is not arbitrary -- it is the width of `chunks.embedding` in the migration."""
    assert client.dimension == EMBEDDING_DIM
    assert len(client.embed_query(TEXT)) == EMBEDDING_DIM
    assert len(client.embed_passages([TEXT])[0]) == EMBEDDING_DIM


def test_vectors_are_normalised(client: FastEmbedClient) -> None:
    """bge-small returns unit vectors, which is why pgvector's `<=>` cosine operator is
    the right choice and why inner product would be equivalent."""
    vector = client.embed_passages([TEXT])[0]
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-4)


def test_batch_order_is_preserved(client: FastEmbedClient) -> None:
    """Chunks are zipped with vectors positionally when the snapshot is written.

    If the client ever reordered a batch, every chunk in the corpus would be paired with
    another chunk's vector -- and nothing downstream would detect it.
    """
    texts = ["protein requirements", "refrigerator temperature", "cooking oil disposal"]
    batch = client.embed_passages(texts)
    for text, vector in zip(texts, batch, strict=True):
        assert _cosine(vector, client.embed_passages([text])[0]) > 0.9999


def test_fastembed_client_satisfies_the_protocol(client: FastEmbedClient) -> None:
    assert isinstance(client, EmbeddingClient)
