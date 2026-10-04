"""Phase 2.4 tests for `GET /corpus`.

This endpoint backs the refusal. When the assistant says "the guidance documents I
searched don't cover this", the list it names comes from here, so the property that
matters most is that it lists **every** document -- including the ones that would have
scored zero (architecture.md §7.3). A list that quietly omits them makes the gap
unjudgeable, which is the specific thing the brief forbids.

Runs against the seeded local database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from db.models import Document
from db.session import SessionLocal
from main import app
from services.embeddings import MODEL_ID


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def seeded_documents():
    with SessionLocal() as session:
        documents = session.query(Document).all()
        if not documents:
            pytest.skip("corpus not seeded -- run `python -m corpus.seed`")
        return [
            {"slug": d.slug, "name": d.name, "publisher": d.publisher, "year": d.year}
            for d in documents
        ]


def test_lists_every_seeded_document(client, seeded_documents):
    body = client.get("/corpus").json()

    assert {d["name"] for d in body["documents"]} == {d["name"] for d in seeded_documents}


def test_chunk_count_totals_match_the_documents(client):
    body = client.get("/corpus").json()

    assert body["chunk_count"] == sum(d["chunk_count"] for d in body["documents"])
    assert body["chunk_count"] > 0


def test_every_document_reports_at_least_one_chunk(client):
    """A document with no chunks is unreachable by retrieval but still claims to be
    searched -- a silent hole in the corpus."""
    body = client.get("/corpus").json()

    empty = [d["name"] for d in body["documents"] if d["chunk_count"] == 0]
    assert empty == []


def test_citation_fields_are_present_on_every_document(client):
    """name/publisher/url are what a user needs to check a claim by hand."""
    body = client.get("/corpus").json()

    for document in body["documents"]:
        assert document["name"]
        assert document["publisher"]
        assert document["url"].startswith("http")


def test_year_may_be_null_but_year_source_never_is(client):
    """Three corpus documents state no publication year and the brief forbids inventing
    one, so `year` is nullable -- but the provenance of that decision is not."""
    body = client.get("/corpus").json()

    for document in body["documents"]:
        assert document["year_source"]
        assert document["year"] is None or isinstance(document["year"], int)


def test_reports_the_embedding_model_in_use(client):
    """A corpus embedded with one model and queried with another retrieves confidently
    and wrongly, so the model in play is part of the corpus description."""
    body = client.get("/corpus").json()

    assert body["embedding_model"] == MODEL_ID
