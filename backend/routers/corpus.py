"""`GET /corpus` — what the assistant can see (implementation-plan.md Phase 2.4).

This endpoint exists because of the refusal. When the assistant says "the guidance
documents I searched don't cover this", a user cannot judge the gap without knowing what
was searched. The brief requires naming the corpus; this is where the name list comes
from, and the Phase 2.5 `not_in_corpus` response reuses the same shape.

Deliberately lists **every** document, including ones that would score zero for any given
question (architecture.md §7.3).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Chunk, Document
from db.schemas import CorpusDocumentRead, CorpusResponse
from db.session import get_db
from services.embeddings import MODEL_ID

router = APIRouter(tags=["corpus"])


@router.get("/corpus", response_model=CorpusResponse)
def get_corpus(db: Session = Depends(get_db)) -> CorpusResponse:
    # Outer join, so a document that somehow seeded with no chunks still appears rather
    # than silently vanishing from the list of "what was searched".
    stmt = (
        select(Document, func.count(Chunk.id).label("chunk_count"))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.name)
    )
    rows = db.execute(stmt).all()

    documents = [
        CorpusDocumentRead(
            id=document.id,
            name=document.name,
            publisher=document.publisher,
            year=document.year,
            url=document.source_url,
            year_source=document.year_source,
            retrieval_date=document.retrieval_date,
            page_last_updated=document.page_last_updated,
            chunk_count=chunk_count,
        )
        for document, chunk_count in rows
    ]

    return CorpusResponse(
        documents=documents,
        chunk_count=sum(d.chunk_count for d in documents),
        embedding_model=MODEL_ID,
    )
