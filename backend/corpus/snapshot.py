"""Read and write `corpus_snapshot.jsonl.gz` (architecture.md §5.4).

The snapshot is the committed artifact that stands between ingestion and the database.
It exists so that deploying never requires reaching seven government websites, several
of which already return 403 to non-browser clients -- and so that changing what the
assistant knows is a reviewable diff rather than a side effect of re-running a script.

Format: gzipped JSONL. **Line 1 is a header**, every subsequent line is one chunk.

    {"schema": "corpus-snapshot/1", "embedding_model": "BAAI/bge-small-en-v1.5", ...}
    {"document_id": "...", "ordinal": 0, "text": "...", "embedding": [...384 floats]}
    {"document_id": "...", "ordinal": 1, ...}

JSONL rather than a JSON array for two reasons that both matter here: the file is
committed, so a single changed chunk must be a single changed line rather than a
reflowed document; and seeding streams line by line instead of holding every vector in
memory at once.

The header is what makes the model id checkable. A vector embedded by one model is
meaningless to another, and a silent model swap would degrade retrieval with no error
anywhere -- so `read_snapshot` refuses a mismatch rather than warning about it.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corpus.models import Chunk

SCHEMA = "corpus-snapshot/1"


class SnapshotError(RuntimeError):
    """The snapshot cannot be trusted for the configured embedding model."""


def write_snapshot(
    path: Path,
    chunks: list[Chunk],
    vectors: list[list[float]],
    *,
    embedding_model: str,
    dimension: int,
    query_prefix: str,
    documents: list[dict[str, Any]],
) -> None:
    if len(chunks) != len(vectors):
        raise SnapshotError(f"{len(chunks)} chunks but {len(vectors)} vectors")

    header = {
        "schema": SCHEMA,
        "embedding_model": embedding_model,
        "dimension": dimension,
        # Recorded so a future reader can tell *how* queries were encoded, not just by
        # which model. The prefix is part of the contract between index and query
        # (architecture.md §5.5); a snapshot built without it is not interchangeable
        # with one built with it.
        "query_prefix": query_prefix,
        "created_at": datetime.now(UTC).isoformat(),
        "chunk_count": len(chunks),
        "documents": documents,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so regenerating an unchanged corpus produces an identical file rather than
    # a spurious git diff from the gzip timestamp.
    with gzip.GzipFile(path, "wb", mtime=0) as raw:
        raw.write((json.dumps(header, ensure_ascii=False) + "\n").encode())
        for chunk, vector in zip(chunks, vectors, strict=True):
            row = dataclasses.asdict(chunk)
            row["embedding"] = [round(x, 6) for x in vector]
            raw.write((json.dumps(row, ensure_ascii=False) + "\n").encode())


def read_header(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.loads(handle.readline())


def read_snapshot(
    path: Path, *, expect_model: str | None = None, expect_dimension: int | None = None
) -> Iterator[dict[str, Any]]:
    """Stream chunk rows, after verifying the header.

    The model check is a hard failure, not a warning. Seeding a pgvector index with
    vectors from a different model produces a database that answers every question
    confidently and wrongly, with nothing in the logs to say why.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(handle.readline())

        if header.get("schema") != SCHEMA:
            raise SnapshotError(f"{path}: unknown schema {header.get('schema')!r}, expected {SCHEMA!r}")

        model = header.get("embedding_model")
        if expect_model is not None and model != expect_model:
            raise SnapshotError(
                f"{path}: built with {model!r} but this process is configured for "
                f"{expect_model!r}. Vectors from different models are not comparable -- "
                f"re-run `python -m corpus.ingest` or set EMBEDDING_MODEL to match."
            )

        dimension = header.get("dimension")
        if expect_dimension is not None and dimension != expect_dimension:
            raise SnapshotError(
                f"{path}: vectors are {dimension}-dimensional, expected {expect_dimension}."
            )

        for line in handle:
            if line.strip():
                yield json.loads(line)
