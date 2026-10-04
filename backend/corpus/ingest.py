"""Phase 2.1 entry point: manifest -> fetched -> parsed -> chunked.

    python -m corpus.ingest                 # fetch, parse, chunk, print the report
    python -m corpus.ingest --use-cache     # skip the network, reuse downloaded files
    python -m corpus.ingest --out chunks.json

**Never runs at application boot** (architecture.md §12.2). Embedding and the committed
snapshot are Phase 2.2; this stage stops at chunks so they can be read by a human first,
which is the point of the Phase 2.1 exit criteria.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import statistics
import sys
from pathlib import Path

import httpx

from corpus.chunk import TEXT_BUDGET_TOKENS, chunk_document, default_token_counter
from corpus.fetch import (
    DEFAULT_HEADERS,
    TIMEOUT_SECONDS,
    FetchError,
    _suffix_for,
    fetch,
    load_manifest,
)
from corpus.models import FetchedDocument, IngestReport
from corpus.parse import ParseError, parse
from corpus.snapshot import SnapshotError, write_snapshot

CORPUS_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = CORPUS_DIR / "corpus.yaml"
DEFAULT_CACHE = CORPUS_DIR / ".cache"
DEFAULT_SNAPSHOT = CORPUS_DIR / "corpus_snapshot.jsonl.gz"

logger = logging.getLogger("corpus.ingest")


def run(manifest_path: Path, cache_dir: Path, *, use_cache: bool) -> IngestReport:
    report = IngestReport()
    entries = load_manifest(manifest_path)
    count_tokens = default_token_counter()

    with httpx.Client(
        follow_redirects=True, timeout=TIMEOUT_SECONDS, headers=DEFAULT_HEADERS
    ) as client:
        for entry in entries:
            cached = cache_dir / f"{entry.id}{_suffix_for(entry.media_type)}"
            if use_cache and cached.exists():
                fetched = _from_cache(entry, cached)
                logger.info("%-34s cached  %7d bytes", entry.id, fetched.byte_count)
            else:
                fetched = fetch(entry, cache_dir, client=client)
                declared = fetched.content_length_declared
                logger.info(
                    "%-34s fetched %7d bytes  content-length=%s",
                    entry.id,
                    fetched.byte_count,
                    declared if declared is not None else "NOT SERVED",
                )
                if declared is None:
                    rests_on = (
                        "completeness rests on the sha256 pin"
                        if entry.sha256
                        else "and no sha256 pin either (page is revised in place) -- "
                        "completeness is unverified"
                    )
                    report.warnings.append(f"{entry.id}: no Content-Length served; {rests_on}")

            parsed = parse(fetched)
            report.documents.append(parsed)
            report.fetched.append(fetched)
            report.chunks.extend(chunk_document(parsed, token_counter=count_tokens))

    return report


def embed_and_write(report: IngestReport, snapshot_path: Path) -> None:
    """Embed every chunk and write the committed snapshot (Phase 2.2).

    Passages are embedded bare -- the BGE query prefix belongs on the query side only
    (architecture.md §5.5). The string embedded is `Chunk.embedding_input()`, which
    prepends the document name and section heading: short chunks from a two-page
    brochure are otherwise nearly contextless.
    """
    from services.embeddings import MODEL_ID, QUERY_PREFIX, FastEmbedClient

    client = FastEmbedClient()
    inputs = [chunk.embedding_input() for chunk in report.chunks]
    logger.info("embedding %d chunks with %s ...", len(inputs), client.model_id)
    vectors = client.embed_passages(inputs)

    documents = [
        {
            "id": fetched.entry.id,
            "name": fetched.entry.name,
            "publisher": fetched.entry.publisher,
            "year": fetched.entry.year,
            "year_source": fetched.entry.year_source,
            "source_url": fetched.entry.source_url,
            "media_type": fetched.entry.media_type,
            "retrieval_date": fetched.retrieval_date.isoformat(),
            "content_sha256": fetched.sha256,
            "page_last_updated": fetched.page_last_updated,
            "page_count": parsed.page_count,
            "parser_used": parsed.parser_used,
        }
        for fetched, parsed in zip(report.fetched, report.documents, strict=True)
    ]

    write_snapshot(
        snapshot_path,
        report.chunks,
        vectors,
        embedding_model=client.model_id,
        dimension=client.dimension,
        query_prefix=QUERY_PREFIX,
        documents=documents,
    )
    size = snapshot_path.stat().st_size
    logger.info("wrote %s (%d chunks, %.0f KB)", snapshot_path, len(vectors), size / 1024)


def _from_cache(entry, path: Path) -> FetchedDocument:
    """Rebuild a FetchedDocument from a previously downloaded file.

    This must reproduce *everything* the network path derives, not just the bytes. An
    earlier version skipped `page_last_updated`, which silently wrote `None` into the
    snapshot for the WHO page -- a document whose manifest entry declares
    `year_source: page_last_updated`, so the date is the provenance for its year.

    `retrieval_date` comes from the file's mtime rather than the clock: the document was
    retrieved when it was downloaded, not when the cache was read.
    """
    import hashlib
    from datetime import UTC, datetime

    from corpus.fetch import _page_last_updated

    body = path.read_bytes()
    return FetchedDocument(
        entry=entry,
        path=path,
        sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        retrieval_date=datetime.fromtimestamp(path.stat().st_mtime, UTC),
        content_length_declared=None,
        page_last_updated=_page_last_updated(entry, body),
    )


def print_report(report: IngestReport) -> None:
    """The evidence for Phase 2.1's exit criteria and the README's 'what it cost'."""
    print("\n" + "=" * 96)
    print(f"{'document':34} {'pp':>3} {'parser':>9} {'blocks':>7} {'head':>5} {'chunks':>7} {'tokens med/max':>15}")
    print("-" * 96)

    for parsed in report.documents:
        chunks = [c for c in report.chunks if c.document_id == parsed.entry.id]
        tokens = [c.token_count for c in chunks] or [0]
        headings = sum(1 for b in parsed.blocks if b.is_heading)
        pages = str(parsed.page_count) if parsed.page_count else "--"
        print(
            f"{parsed.entry.id:34} {pages:>3} {parsed.parser_used:>9} "
            f"{len(parsed.blocks):>7} {headings:>5} {len(chunks):>7} "
            f"{int(statistics.median(tokens)):>7}/{max(tokens):<7}"
        )

    tokens = [c.token_count for c in report.chunks] or [0]
    untitled = sum(1 for c in report.chunks if not c.section_heading)
    oversized = sum(1 for c in report.chunks if c.token_count > TEXT_BUDGET_TOKENS)

    print("-" * 96)
    print(f"{'TOTAL':34} {sum(d.page_count for d in report.documents):>3} {'':>9} "
          f"{sum(len(d.blocks) for d in report.documents):>7} "
          f"{sum(1 for d in report.documents for b in d.blocks if b.is_heading):>5} "
          f"{len(report.chunks):>7} {int(statistics.median(tokens)):>7}/{max(tokens):<7}")
    print()
    print(f"  token budget            {TEXT_BUDGET_TOKENS}")
    print(f"  mean tokens/chunk       {statistics.mean(tokens):.0f}")
    print(f"  chunks over budget      {oversized}  (structured blocks kept whole)")
    print(f"  chunks w/o section head {untitled}/{len(report.chunks)}"
          f"  ({100 * untitled / max(1, len(report.chunks)):.0f}%)")

    for warning in report.warnings:
        print(f"  WARNING  {warning}")
    print("=" * 96 + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch, parse and chunk the guidance corpus.")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--use-cache", action="store_true", help="skip the network if a file is cached")
    ap.add_argument("--out", type=Path, help="write chunks as JSON/JSONL for manual review")
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT,
                    help="where to write the embedded, committed snapshot")
    ap.add_argument("--no-embed", action="store_true",
                    help="stop after chunking (Phase 2.1 only -- skips the snapshot)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    try:
        report = run(args.manifest, args.cache_dir, use_cache=args.use_cache)
    except (FetchError, ParseError) as exc:
        logger.error("ingestion failed: %s", exc)
        return 1

    print_report(report)

    if not args.no_embed:
        try:
            embed_and_write(report, args.snapshot)
        except SnapshotError as exc:
            logger.error("snapshot failed: %s", exc)
            return 1

    if args.out:
        rows = [dataclasses.asdict(c) for c in report.chunks]
        if args.out.suffix == ".jsonl":
            args.out.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
        else:
            args.out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        logger.info("wrote %d chunks to %s", len(report.chunks), args.out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
