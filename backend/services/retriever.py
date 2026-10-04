"""Vector retrieval over the corpus (architecture.md §7.1).

Exact cosine scan over `chunks.embedding`. No ANN index: at ~105 chunks the scan is
single-digit milliseconds, and IVFFlat or HNSW would add tuning surface and *approximate*
recall to a solved problem (architecture.md §2). Revisit above roughly 50k chunks.

Two entry points, and the split matters:

- `search()` returns the top `k` by similarity with **no floor applied**. This is what the
  calibration harness uses, so a floor sweep costs one query per question rather than one
  per (question, floor) pair.
- `retrieve()` is what the application calls. It applies the floor, and that floor *is*
  the not-in-corpus refusal (architecture.md §7.3).

The query is encoded with `embed_query()`, never `embed_passages()`. BGE v1.5 is
asymmetric and the prefix belongs on the query side only -- see `services/embeddings.py`,
where that distinction is enforced by having two methods rather than one flag.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Chunk, Document
from services.embeddings import EmbeddingClient, get_embedding_client

logger = logging.getLogger(__name__)

# Both measured, 2026-10-05, against eval/retrieval_set.json (20 in-corpus questions,
# 8 out-of-corpus). Full sweep and reasoning in
# docs/features/rag-sourced-claims/retrieval-calibration.md.
#
# FLOOR: worst in-corpus top-1 is 0.7087; best out-of-corpus is 0.6796. Floors in
# 0.68..0.70 separate the set, and 0.69 is the midpoint -- margins of 0.010 below and
# 0.019 above.
#
# **This band is only 0.02 wide, down from 0.07.** It narrowed when two destroyed tables
# were quarantined out of the corpus (corpus.yaml): questions about figures the corpus
# deliberately no longer states still land on topically adjacent text, and they score
# 0.66-0.68. The floor is therefore a much weaker discriminator than it first appeared,
# and gate 2 -- the model's `answers_question` verdict, architecture.md §7.3 -- is now
# load-bearing rather than a backstop.
#
# 0.68, the bottom of the band, would follow the usual "fail toward a wasted model call"
# rule, but its margin over the worst negative is 0.0004 -- a coincidence, not a margin.
# Hence the midpoint.
#
# K: recall@8 = 0.975 and recall@10 = 0.975 -- the curve is flat past 8, so a larger k
# buys nothing but context.
#
# Changing either without re-running `python eval/run_retrieval_eval.py` makes this
# comment false. The floor *is* the not-in-corpus refusal; it is not a tuning knob.
DEFAULT_FLOOR = 0.69
DEFAULT_K = 8


@dataclass(frozen=True)
class ScoredChunk:
    """One retrieval hit, with enough provenance to build a citation without a second query."""

    chunk_id: uuid.UUID
    chunk_key: str
    document_id: uuid.UUID
    document_slug: str
    document_name: str
    publisher: str
    year: int | None
    source_url: str
    section_heading: str | None
    page_from: int | None
    page_to: int | None
    text: str
    score: float


def search(
    session: Session,
    query: str,
    *,
    k: int = DEFAULT_K,
    document_id: uuid.UUID | None = None,
    client: EmbeddingClient | None = None,
) -> list[ScoredChunk]:
    """Top `k` chunks by cosine similarity. No floor -- callers decide what to keep.

    `document_id` is the brief's second retrieval mode: scope the search to one named
    document. It is also what the Phase 2.5 per-document answering loop will use.
    """
    if not query.strip():
        return []

    client = client or get_embedding_client()
    query_vector = client.embed_query(query)

    # pgvector's <=> is cosine *distance*; similarity is 1 - distance. Ordering by distance
    # ascending is the same as ordering by similarity descending, so the LIMIT is applied
    # in the database rather than after fetching the whole table.
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")

    stmt = (
        select(Chunk, Document, distance)
        .join(Document, Document.id == Chunk.document_id)
        .order_by(distance)
        .limit(k)
    )
    if document_id is not None:
        stmt = stmt.where(Chunk.document_id == document_id)

    return [
        ScoredChunk(
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
            score=1.0 - float(dist),
        )
        for chunk, document, dist in session.execute(stmt).all()
    ]


def retrieve(
    session: Session,
    query: str,
    *,
    k: int = DEFAULT_K,
    floor: float = DEFAULT_FLOOR,
    document_id: uuid.UUID | None = None,
    client: EmbeddingClient | None = None,
) -> list[ScoredChunk]:
    """Top `k` chunks scoring at or above `floor`.

    An empty list is a meaningful result, not an error: it is gate 1 of the not-in-corpus
    refusal (architecture.md §7.3), and it means no generation call is made at all.
    """
    hits = search(session, query, k=k, document_id=document_id, client=client)
    kept = [hit for hit in hits if hit.score >= floor]

    if hits and not kept:
        logger.info(
            "below floor: best=%.3f floor=%.2f query=%r",
            hits[0].score,
            floor,
            query[:80],
        )
    return kept
