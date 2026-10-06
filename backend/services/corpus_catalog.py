"""What the assistant searched — the list the coverage refusal has to name.

Separate from `routers/corpus.py` because two callers need the same list for two different
reasons: `GET /corpus` powers the sources panel's idle state, and the `not_in_corpus`
response has to say what it looked in (architecture.md §7.3).

**The list is every document, always.** Not the documents that scored above the floor, not
the ones that returned `answers_question: true`. The brief requires the refusal to name
what was searched, and a user cannot judge the gap from a list that silently omits
everything that scored zero -- a refusal that named only the near-misses would read as "we
have nothing on this topic" when the truth may be "we have seven documents and none of
them covers it".

The one legitimate narrowing is an explicit `document_filter`: if the user scoped the
question to one document, that document *is* what was searched.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Document
from db.schemas import DocumentRef

# Static text, like the policy refusals in `services/scope_guard.py`. Not model-generated:
# a refusal that says the corpus does not cover something must not itself be a sentence the
# model composed out of the material it just declined to use.
#
# Phrased as a statement about the corpus, not about the question. "I don't know" invites a
# retry; "the documents I searched don't cover this" tells the user what to do with the
# `searched` list sitting next to it.
NOT_IN_CORPUS_MESSAGE = (
    "The guidance documents I searched don't cover this. I only answer from the documents "
    "listed here, so if it isn't in one of them I have nothing to cite."
)


def document_refs(session: Session, *, document_id: uuid.UUID | None = None) -> list[DocumentRef]:
    """The corpus as citation-shaped references, ordered by name.

    Same ordering as `GET /corpus`, so the panel and the refusal list the corpus in the
    same order and a user comparing them is not left wondering whether they differ.
    """
    stmt = select(Document).order_by(Document.name)
    if document_id is not None:
        stmt = stmt.where(Document.id == document_id)

    return [
        DocumentRef(
            id=document.id,
            name=document.name,
            publisher=document.publisher,
            year=document.year,
            url=document.source_url,
        )
        for document in session.scalars(stmt).all()
    ]
