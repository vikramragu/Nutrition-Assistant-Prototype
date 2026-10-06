"""Shared fixtures for the Phase 2.5 answer-layer tests.

`ScoredChunk` carries eleven fields of provenance because a citation has to be buildable
without a second query. Constructing one by hand in every test would bury the thing each
test is actually about, so it is built here and overridden a field at a time.
"""

from __future__ import annotations

import uuid

import pytest

from services.retriever import ScoredChunk

CORPUS_TEST_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "nutrition-project.tests")


def build_chunk(
    *,
    slug: str = "doc-a",
    ordinal: int = 0,
    name: str | None = None,
    publisher: str | None = None,
    year: int | None = 2024,
    text: str = "Adults should limit free sugars to less than 10% of total energy intake.",
    score: float = 0.80,
    section_heading: str | None = "Free sugars",
    page_from: int | None = 1,
    page_to: int | None = 1,
) -> ScoredChunk:
    """One retrieval hit. Ids are deterministic per `slug:ordinal`, as the real ones are.

    Mirroring `corpus/ids.py`'s uuid5 scheme rather than using `uuid4` keeps a test's
    expected ids readable and stable across runs -- the same reason the production ids are
    derived rather than random.
    """
    return ScoredChunk(
        chunk_id=uuid.uuid5(CORPUS_TEST_NAMESPACE, f"{slug}:{ordinal}"),
        chunk_key=f"{slug}:{ordinal}",
        document_id=uuid.uuid5(CORPUS_TEST_NAMESPACE, slug),
        document_slug=slug,
        document_name=name or f"Document {slug}",
        publisher=publisher or f"Publisher of {slug}",
        year=year,
        source_url=f"https://example.org/{slug}.pdf",
        section_heading=section_heading,
        page_from=page_from,
        page_to=page_to,
        text=text,
        score=score,
    )


@pytest.fixture()
def chunk():
    """The factory, not a chunk: most tests need two or three that differ in one field."""
    return build_chunk
