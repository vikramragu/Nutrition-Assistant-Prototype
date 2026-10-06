"""Phase 2.6 — an explicit per-conversation message order.

Why this is needed, and why `created_at` cannot do the job:

A Phase 2 turn produces **one assistant message per document** (architecture.md §7.2), and
architecture.md §10 requires every row of that turn to be written in a single commit. In
PostgreSQL `now()` is the *transaction* timestamp, so every message in that commit gets an
**identical** `created_at` -- the user's message included. Ordering by `created_at` would
return a turn's messages in arbitrary order, losing both the user/assistant sequence and
the strongest-document-first ordering the answer layer produced.

So the order becomes data rather than a side effect of when the row was written.
`UNIQUE (conversation_id, ordinal)` mirrors `UNIQUE (document_id, ordinal)` on `chunks`:
the same pattern, for the same reason.

Additive. The backfill derives ordinals for existing rows from `created_at`, which is
correct for Phase 1 data because Phase 1 committed each message separately and therefore
*did* give them distinct timestamps. That is exactly the property Phase 2 gives up.

Revision ID: c3d9a51e7b42
Revises: b1c7e4a92f08
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d9a51e7b42"
down_revision = "b1c7e4a92f08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added nullable so the backfill can run, then made NOT NULL. Adding it NOT NULL with
    # a server_default of 0 would have been one step, but it would also have silently
    # given every pre-existing message in a conversation the same ordinal -- the exact
    # ambiguity this migration exists to remove.
    op.add_column("messages", sa.Column("ordinal", sa.Integer(), nullable=True))

    # Phase 1 rows: one commit per message, so created_at is distinct and faithful.
    # `id` breaks ties deterministically if two rows somehow share a timestamp; without it
    # the backfill itself would be non-deterministic, which is what we are fixing.
    op.execute(
        """
        UPDATE messages AS m
        SET ordinal = numbered.row_number - 1
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id ORDER BY created_at, id
                   ) AS row_number
            FROM messages
        ) AS numbered
        WHERE m.id = numbered.id
        """
    )

    op.alter_column("messages", "ordinal", nullable=False)
    op.create_unique_constraint(
        "uq_message_conversation_ordinal", "messages", ["conversation_id", "ordinal"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_message_conversation_ordinal", "messages", type_="unique")
    op.drop_column("messages", "ordinal")
