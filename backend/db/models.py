import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Must match services.embeddings.EMBEDDING_DIM. Duplicated as a literal rather than
# imported because a migration must describe the column width at the time it ran, not
# whatever the application is configured for today.
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    scope_refusals: Mapped[list["ScopeRefusal"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text)  # user text, or assistant `answer`
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    claims: Mapped[list["Claim"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE")
    )
    claim_text: Mapped[str] = mapped_column(Text)

    # Phase 2: the citation. A foreign key rather than a denormalised string, so
    # publisher/year/url are derived by join and cannot drift from the corpus.
    #
    # RESTRICT, not CASCADE: deleting a chunk that a conversation cites should fail
    # loudly rather than silently delete the claim. Re-seeding the same snapshot
    # produces identical chunk ids (corpus/ids.py), so routine re-runs never hit this.
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=True
    )

    # Phase 1 legacy, always NULL. Kept so Phase 1 rows stay readable; drop in a later
    # migration once no non-null values exist (there are none today).
    source: Mapped[str | None] = mapped_column(Text, nullable=True)

    message: Mapped["Message"] = relationship(back_populates="claims")
    chunk: Mapped["Chunk | None"] = relationship()


class Document(Base):
    """One corpus document, with the provenance a citation needs (architecture.md §6)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(128), unique=True)  # manifest id
    name: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(Text)
    # Nullable because three documents state no publication year. The brief forbids
    # inventing one, so an unknown year is NULL and `year_source` says where it came from.
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(64))
    retrieval_date: Mapped[datetime] = mapped_column(server_default=func.now())
    page_last_updated: Mapped[str | None] = mapped_column(Text, nullable=True)  # HTML only
    content_sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0/NULL for HTML
    parser_used: Mapped[str] = mapped_column(String(32))

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ordinal"
    )


class Chunk(Base):
    """A retrievable passage. Every column here can end up visible in a citation."""

    __tablename__ = "chunks"

    # Deterministic uuid5 over `document_id:ordinal` -- see corpus/ids.py. NOT a uuid4:
    # re-seeding must produce identical ids or every stored citation is orphaned.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    chunk_key: Mapped[str] = mapped_column(String(160), unique=True)  # readable natural key
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    # Nullable: several corpus documents have no section structure. A null heading is
    # acceptable; a fabricated one is not.
    section_heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 = not paginated
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),)


class Retrieval(Base):
    """One row per /chat turn that reached retrieval -- the evidence trail."""

    __tablename__ = "retrievals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: a not-in-corpus refusal persists a retrieval but no assistant message.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    )
    query_text: Mapped[str] = mapped_column(Text)
    k: Mapped[int] = mapped_column(Integer)
    floor: Mapped[float] = mapped_column(Float)
    document_filter: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    hits: Mapped[list["RetrievalHit"]] = relationship(
        back_populates="retrieval", cascade="all, delete-orphan", order_by="RetrievalHit.rank"
    )


class RetrievalHit(Base):
    """What retrieval returned, and which of it reached a generation call."""

    __tablename__ = "retrieval_hits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    retrieval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retrievals.id", ondelete="CASCADE")
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE")
    )
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

    retrieval: Mapped["Retrieval"] = relationship(back_populates="hits")
    chunk: Mapped["Chunk"] = relationship()


class ScopeRefusal(Base):
    __tablename__ = "scope_refusals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    message_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))  # 'calorie_target' | 'weight_target' | 'medical_advice'
    stage: Mapped[str] = mapped_column(String(16))  # 'pre_model' | 'post_model'
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="scope_refusals")


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    prompt_version: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list["EvalFinding"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class EvalFinding(Base):
    __tablename__ = "eval_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(Integer)
    # enum matching the five failure types in problemStatement.md §6 / architecture.md §10.2:
    # unsupported_claim | inconsistent_number | unverifiable_source | missed_scope_restriction | unhelpful_hedging
    failure_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)

    run: Mapped["EvalRun"] = relationship(back_populates="findings")
