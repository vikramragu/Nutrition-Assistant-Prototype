"""Phase 2.3 — corpus, chunks, retrieval evidence, and the claim citation FK.

Additive only: no column is dropped, no data is migrated, and every Phase 1 row stays
readable. `claims.chunk_id` is nullable precisely so existing rows (which have no
citation) remain valid.

Revision ID: b1c7e4a92f08
Revises: fe0fc63c332c
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "b1c7e4a92f08"
down_revision = "fe0fc63c332c"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384  # bge-small-en-v1.5


def upgrade() -> None:
    # Idempotent by design: the extension may already exist (it was created by hand
    # during Phase 2.0 preflight on both Railway and local). A fresh database -- a new
    # environment, a reviewer's clone -- still needs this, so it must not be skipped.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=False),
        # Nullable: three corpus documents state no publication year, and the brief
        # forbids inventing one.
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("year_source", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("retrieval_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("page_last_updated", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parser_used", sa.String(32), nullable=False),
    )

    op.create_table(
        "chunks",
        # Deterministic uuid5 over "document_slug:ordinal" -- not a uuid4. See
        # corpus/ids.py: a random key would orphan every stored citation on re-seed.
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("chunk_key", sa.String(160), nullable=False, unique=True),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section_heading", sa.Text(), nullable=True),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
    )
    # No ANN index. ~105 chunks: an exact scan is single-digit milliseconds, while
    # IVFFlat or HNSW would add tuning surface and *approximate* recall to a solved
    # problem. Revisit above roughly 50k chunks (architecture.md §2).

    op.create_table(
        "retrievals",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        # Nullable: a not-in-corpus refusal records a retrieval but produces no
        # assistant message to attach it to.
        sa.Column(
            "message_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("k", sa.Integer(), nullable=False),
        sa.Column("floor", sa.Float(), nullable=False),
        sa.Column(
            "document_filter",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "retrieval_hits",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "retrieval_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("retrievals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_retrieval_hits_retrieval_id", "retrieval_hits", ["retrieval_id"])

    # The citation. RESTRICT rather than CASCADE: deleting a chunk a conversation cites
    # should fail loudly, not silently delete the claim and leave an uncited answer.
    op.add_column(
        "claims",
        sa.Column("chunk_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_claims_chunk_id", "claims", "chunks", ["chunk_id"], ["id"], ondelete="RESTRICT"
    )


def downgrade() -> None:
    op.drop_constraint("fk_claims_chunk_id", "claims", type_="foreignkey")
    op.drop_column("claims", "chunk_id")
    op.drop_index("ix_retrieval_hits_retrieval_id", table_name="retrieval_hits")
    op.drop_table("retrieval_hits")
    op.drop_table("retrievals")
    op.drop_table("chunks")
    op.drop_table("documents")
    # The extension is deliberately not dropped: another database object may use it, and
    # dropping it would cascade to any remaining vector column.
