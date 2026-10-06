"""Conversation persistence and replay.

Phase 2 changed the shape of a turn. It used to be one user message and one assistant
message. It is now one user message and **one assistant message per document that had
something to say** (architecture.md §7.2), written in a single transaction so there is no
state in which a user message exists without its answer.

Two consequences that drive everything in this module:

- **Order has to be stored.** Postgres `now()` is the transaction timestamp, so every row
  of a single-commit turn shares a `created_at`. Ordering by it would scramble the turn.
  `messages.ordinal` is the order; `created_at` is only ever "when".
- **Replay and persistence are different shapes.** The database keeps one row per
  document, because that is what was answered. The model's history is folded back into one
  assistant turn, because that is what the conversation sounded like.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from db.models import Chunk, Claim, Conversation, Message, Retrieval, RetrievalHit
from db.schemas import (
    Citation,
    ClaimRead,
    ClaimSchema,
    ConversationRead,
    DocumentAnswerOut,
    DocumentRef,
    MessageRead,
)
from services.model_client import ChatMessage
from services.retriever import ScoredChunk


@dataclass(frozen=True)
class RetrievalEvidence:
    """The evidence trail for one turn that reached retrieval (architecture.md §6).

    `hits` is **every** chunk retrieval returned, not just the ones above the floor. A
    coverage refusal with no hit rows records that a search happened but not how close it
    came, and "how close it came" is the only thing that separates a correct refusal from
    `over_refusal` when the failure log is read.
    """

    query_text: str
    k: int
    floor: float
    document_filter: uuid.UUID | None
    hits: Sequence[ScoredChunk]
    used_chunk_ids: frozenset[uuid.UUID] = frozenset()

    def rows(self, retrieval_id: uuid.UUID) -> list[RetrievalHit]:
        """Hit rows in rank order. `used` is "did this chunk reach a generation call"."""
        return [
            RetrievalHit(
                retrieval_id=retrieval_id,
                chunk_id=hit.chunk_id,
                score=hit.score,
                rank=rank,
                used=hit.chunk_id in self.used_chunk_ids,
            )
            for rank, hit in enumerate(self.hits, 1)
        ]


def create_conversation(db: Session) -> Conversation:
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def next_ordinal(db: Session, conversation_id: uuid.UUID) -> int:
    """The next free position in this conversation.

    `UNIQUE (conversation_id, ordinal)` means a lost race fails loudly on insert rather
    than quietly producing two messages that claim the same position. One conversation is
    one person typing, so the race is not expected; the constraint is there because
    "not expected" is not a guarantee.
    """
    highest = db.scalar(
        select(func.max(Message.ordinal)).where(Message.conversation_id == conversation_id)
    )
    return 0 if highest is None else highest + 1


def append_message(
    db: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    claims: list[ClaimSchema] | None = None,
) -> Message:
    """Write one message and commit. The Phase 1 primitive, kept for the eval harnesses.

    `POST /chat` does **not** use this: it must write a whole turn in one transaction, and
    this commits per call. See `persist_turn()`.
    """
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        ordinal=next_ordinal(db, conversation_id),
    )
    if claims:
        message.claims = [Claim(claim_text=c.claim, source=c.source) for c in claims]
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def persist_turn(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    user_message: str,
    document_answers: Sequence[DocumentAnswerOut],
    evidence: RetrievalEvidence,
) -> list[Message]:
    """Write a whole turn -- user message, one assistant message per document, every
    claim with its citation, and the retrieval evidence -- in **one** commit.

    One commit is the requirement, not an optimisation (architecture.md §10): every gate
    has already passed by the time this is called, and a failure part-way through must
    leave the conversation exactly as it was. A user message persisted without its answer
    would replay as a question the assistant ignored.

    The retrieval row is linked to the **first** assistant message. `retrievals.message_id`
    is one column and a turn now has several assistant messages; the first is the one the
    strongest-scoring document produced, so it is the stable choice rather than an
    arbitrary one. The turn is recoverable from `messages.ordinal` regardless.
    """
    ordinal = next_ordinal(db, conversation_id)

    user_row = Message(
        conversation_id=conversation_id, role="user", content=user_message, ordinal=ordinal
    )
    db.add(user_row)

    assistant_rows: list[Message] = []
    for offset, answer in enumerate(document_answers, start=1):
        row = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer.answer,
            ordinal=ordinal + offset,
            claims=[
                # `source` stays NULL: the Phase 1 text column. The citation is chunk_id,
                # so publisher/year/url are derived by join and cannot drift (§6.1).
                Claim(claim_text=claim.claim, chunk_id=claim.source.chunk_id)
                for claim in answer.claims
            ],
        )
        db.add(row)
        assistant_rows.append(row)

    db.flush()  # assign primary keys without ending the transaction

    retrieval = Retrieval(
        message_id=assistant_rows[0].id if assistant_rows else None,
        query_text=evidence.query_text,
        k=evidence.k,
        floor=evidence.floor,
        document_filter=evidence.document_filter,
    )
    db.add(retrieval)
    db.flush()
    db.add_all(evidence.rows(retrieval.id))

    db.commit()
    return assistant_rows


def persist_coverage_refusal(
    db: Session, conversation_id: uuid.UUID, evidence: RetrievalEvidence
) -> Retrieval:
    """A not-in-corpus refusal: the retrieval evidence, and no messages.

    Coverage refusals persist to `retrievals` with zero `used` hits (architecture.md §9.2).
    No `Message` row is written, matching how Phase 1 handles a policy refusal -- a refused
    turn is not part of the conversation the model is later shown, and persisting the
    question as a message would make the assistant look like it ignored it.

    `conversation_id` is taken for symmetry and logging; `retrievals` has no conversation
    column of its own, reaching one only through `message_id`, which is null here. That is
    a real gap in the evidence trail -- a coverage refusal cannot currently be traced back
    to the conversation it happened in. Recorded rather than fixed: adding the column is a
    migration, and `query_text` plus `created_at` is enough to find it in practice.
    """
    retrieval = Retrieval(
        message_id=None,
        query_text=evidence.query_text,
        k=evidence.k,
        floor=evidence.floor,
        document_filter=evidence.document_filter,
    )
    db.add(retrieval)
    db.flush()
    db.add_all(evidence.rows(retrieval.id))
    db.commit()
    return retrieval


def load_history(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    """Load a conversation with everything a cited history needs, in one round trip.

    The chunk and its document are eager-loaded because `MessageRead.document` is derived
    from them. Lazily loading would be one query per claim on a long conversation.
    """
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.messages)
            .selectinload(Message.claims)
            .selectinload(Claim.chunk)
            .selectinload(Chunk.document)
        )
    )
    return db.scalars(stmt).first()


def to_conversation_read(conversation: Conversation) -> ConversationRead:
    """The history as the client sees it: one block per document, each claim cited.

    Grouping is not computed here -- it is already in the data. One assistant message *is*
    one document's answer, so "grouped, cited claims" is what reading the rows in `ordinal`
    order gives you.
    """
    return ConversationRead(
        id=conversation.id,
        created_at=conversation.created_at,
        messages=[to_message_read(message) for message in conversation.messages],
    )


def to_message_read(message: Message) -> MessageRead:
    claims = [
        ClaimRead(
            id=claim.id,
            claim_text=claim.claim_text,
            source=_citation_of(claim),
            legacy_source=claim.source,
        )
        for claim in message.claims
    ]
    # Derived, not stored (db.models.Message.content). Every claim of one assistant message
    # cites the same document -- citation_validator rule 1 enforces that at write time --
    # so the first citation is the message's document. None for user messages and for
    # Phase 1 rows, which have no citation to derive it from.
    document = next((claim.source.document for claim in claims if claim.source), None)

    return MessageRead(
        id=message.id,
        role=message.role,
        content=message.content,
        ordinal=message.ordinal,
        created_at=message.created_at,
        document=document,
        claims=claims,
    )


def _citation_of(claim: Claim) -> Citation | None:
    """Expand a stored `chunk_id` into a full citation by join. None for Phase 1 rows."""
    chunk = claim.chunk
    if chunk is None:
        return None
    return Citation(
        chunk_id=chunk.id,
        document=DocumentRef(
            id=chunk.document.id,
            name=chunk.document.name,
            publisher=chunk.document.publisher,
            year=chunk.document.year,
            url=chunk.document.source_url,
        ),
        section_heading=chunk.section_heading,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        quote=chunk.text,
    )


def model_history(conversation: Conversation) -> list[ChatMessage]:
    """Replay the conversation for the model, folding each turn's documents into one turn.

    Storage keeps one assistant row per document because that is what was answered. The
    model should see one assistant reply per turn, because that is what the conversation
    sounded like -- a run of consecutive assistant messages is legal on the chat API but
    misrepresents the exchange, and the next turn's question is a reply to all of it.

    Each document's answer keeps its own paragraph. Nothing is merged inside a paragraph:
    that would be exactly the blending §7.2 exists to prevent, reintroduced through the
    back door of history.
    """
    history: list[ChatMessage] = []
    for message in conversation.messages:
        if message.role == "assistant" and history and history[-1]["role"] == "assistant":
            history[-1] = {
                "role": "assistant",
                "content": f"{history[-1]['content']}\n\n{message.content}",
            }
            continue
        history.append({"role": message.role, "content": message.content})
    return history
