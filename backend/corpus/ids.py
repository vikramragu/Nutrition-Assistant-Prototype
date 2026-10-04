"""Deterministic chunk identity (implementation-plan.md Phase 2.3, option B).

A chunk's primary key is derived from its natural key -- `document_id:ordinal` -- rather
than generated randomly. The reason is citation durability.

`claims.chunk_id` is a foreign key to `chunks.id`. With a random `uuid4`, re-seeding
regenerates every id, so every citation in every past conversation would point at a row
that no longer exists. Guidance documents *are* revised (the WHO page in this corpus is
revised in place under a stable URL), so re-ingestion is expected, not hypothetical --
and a citation pointing at a vanished chunk is exactly the failure this milestone exists
to eliminate.

`uuid5` keeps the column a real UUID, consistent with every other primary key in the
schema, while making the value reproducible:

    seed the same snapshot twice  -> identical ids, citations intact
    re-chunk the corpus           -> ordinals shift, ids change, citations affected

The second case is a deliberate corpus change rather than a routine re-run, and it
should be visible. Hashing the chunk *text* instead would survive re-chunking, but then
any whitespace or overlap tweak would silently mint new ids -- trading a loud surprise
for a quiet one.
"""

from __future__ import annotations

import uuid

# Fixed namespace: uuid5(NAMESPACE_DNS, "corpus.nutrition-assistant").
# Hardcoded rather than computed so it can never drift if that string is edited.
CORPUS_NAMESPACE = uuid.UUID("a5f1604b-44fe-5b4c-aa51-bff906833e10")


def chunk_key(document_id: str, ordinal: int) -> str:
    """The human-readable natural key, stored alongside the UUID.

    This is what appears in logs, in `corpus.show --id`, and in any hand-written query.
    `fsanz-temperature-control-2002:15` is navigable; a UUID is not.
    """
    return f"{document_id}:{ordinal}"


def chunk_id(document_id: str, ordinal: int) -> uuid.UUID:
    """Stable primary key for a chunk."""
    return uuid.uuid5(CORPUS_NAMESPACE, chunk_key(document_id, ordinal))
