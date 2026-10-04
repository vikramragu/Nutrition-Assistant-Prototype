"""Load the committed snapshot into Postgres (architecture.md §12.2).

    python -m corpus.seed                      # idempotent; safe to re-run
    python -m corpus.seed --snapshot path.gz
    python -m corpus.seed --dry-run

**Deliberately not part of application startup.** The `Procfile` runs migrations and then
uvicorn; seeding is a separate, explicit command. Ingestion needs network access to seven
government websites and several minutes; neither belongs in a boot path Railway will
retry on failure.

Idempotence rests on two things:

- a document is skipped when its `content_sha256` already matches, so re-running costs
  one query per document and writes nothing;
- chunk ids are deterministic (`corpus/ids.py`), so re-seeding changed content reuses the
  same primary keys and leaves existing citations pointing at valid rows.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from corpus.ids import chunk_id, chunk_key
from corpus.snapshot import SnapshotError, read_header, read_snapshot
from db.models import Chunk, Document
from db.session import SessionLocal

CORPUS_DIR = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = CORPUS_DIR / "corpus_snapshot.jsonl.gz"

logger = logging.getLogger("corpus.seed")


def seed(session: Session, snapshot_path: Path, *, dry_run: bool = False) -> dict[str, int]:
    from services.embeddings import EMBEDDING_DIM, MODEL_ID

    # Refused, not warned: vectors from a different model produce a database that
    # answers every question confidently and wrongly, with nothing in the logs.
    header = read_header(snapshot_path)
    rows = read_snapshot(snapshot_path, expect_model=MODEL_ID, expect_dimension=EMBEDDING_DIM)

    chunks_by_doc: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        chunks_by_doc[row["document_id"]].append(row)

    stats = {"documents_inserted": 0, "documents_skipped": 0, "chunks_written": 0}

    for meta in header["documents"]:
        slug = meta["id"]
        existing = session.scalar(select(Document).where(Document.slug == slug))

        if existing is not None and existing.content_sha256 == meta["content_sha256"]:
            logger.info("%-34s unchanged, skipping", slug)
            stats["documents_skipped"] += 1
            continue

        if existing is not None:
            # Content changed. Replacing the chunks cascades them away; the deterministic
            # ids mean the replacements reuse the same keys wherever the chunking is
            # unchanged, so citations survive. A claim citing a chunk that genuinely
            # disappears raises on the RESTRICT constraint rather than vanishing quietly.
            logger.info("%-34s content changed -- replacing", slug)
            session.delete(existing)
            session.flush()

        document = Document(
            id=uuid.uuid5(uuid.NAMESPACE_URL, meta["source_url"]),
            slug=slug,
            name=meta["name"],
            publisher=meta["publisher"],
            year=meta["year"],
            year_source=meta["year_source"],
            source_url=meta["source_url"],
            media_type=meta["media_type"],
            retrieval_date=meta["retrieval_date"],
            page_last_updated=meta.get("page_last_updated"),
            content_sha256=meta["content_sha256"],
            page_count=meta.get("page_count"),
            parser_used=meta["parser_used"],
        )
        session.add(document)
        session.flush()
        stats["documents_inserted"] += 1

        for row in chunks_by_doc[slug]:
            session.add(
                Chunk(
                    id=chunk_id(slug, row["ordinal"]),
                    chunk_key=chunk_key(slug, row["ordinal"]),
                    document_id=document.id,
                    ordinal=row["ordinal"],
                    section_heading=row["section_heading"],
                    page_from=row["page_from"],
                    page_to=row["page_to"],
                    text=row["text"],
                    token_count=row["token_count"],
                    embedding=row["embedding"],
                )
            )
            stats["chunks_written"] += 1

    if dry_run:
        session.rollback()
        logger.info("dry run -- rolled back")
    else:
        session.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed the corpus snapshot into Postgres.")
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    if not args.snapshot.exists():
        logger.error("%s not found -- run `python -m corpus.ingest` first", args.snapshot)
        return 1

    with SessionLocal() as session:
        try:
            stats = seed(session, args.snapshot, dry_run=args.dry_run)
        except SnapshotError as exc:
            logger.error("refusing to seed: %s", exc)
            return 1

    logger.info(
        "documents inserted=%d skipped=%d, chunks written=%d",
        stats["documents_inserted"],
        stats["documents_skipped"],
        stats["chunks_written"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
