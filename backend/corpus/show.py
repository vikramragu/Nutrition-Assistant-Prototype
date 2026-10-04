"""Inspect chunks produced by `corpus.ingest` (implementation-plan.md Phase 2.1).

Phase 2.1's exit criteria require a human to read chunks and judge them -- whether a
table got split, whether a heading is real, whether a chunk carries enough context to be
worth retrieving. That review is only useful if it is repeatable, and grepping a JSON
file is not repeatable.

    python -m corpus.show                          # one line per document
    python -m corpus.show --doc fsanz              # every chunk in a document
    python -m corpus.show --id fsanz...:18         # one chunk, in full
    python -m corpus.show --find "2 hour/4 hour"   # chunks containing a phrase
    python -m corpus.show --headings               # every section heading, by document
    python -m corpus.show --short 60               # chunks under N tokens

Reads `chunks.json` as written by `corpus.ingest --out`. After Phase 2.3 the same
questions are answerable in SQL against the `chunks` table; this exists for the window
before the database does.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import textwrap
from pathlib import Path

DEFAULT_CHUNKS = Path("chunks.json")
WIDTH = 96


def load(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"{path} not found -- run: python -m corpus.ingest --out {path}")
    return json.loads(path.read_text())


def summarise(chunks: list[dict]) -> None:
    by_doc: dict[str, list[dict]] = collections.defaultdict(list)
    for chunk in chunks:
        by_doc[chunk["document_id"]].append(chunk)

    print(f"{'document':34} {'chunks':>7} {'tokens med/max':>15} {'headings':>9}")
    print("-" * WIDTH)
    for doc_id, items in by_doc.items():
        tokens = sorted(c["token_count"] for c in items)
        headings = len({c["section_heading"] for c in items if c["section_heading"]})
        median = tokens[len(tokens) // 2]
        print(f"{doc_id:34} {len(items):>7} {median:>7}/{tokens[-1]:<7} {headings:>9}")
    print("-" * WIDTH)
    print(f"{'TOTAL':34} {len(chunks):>7}")


def show_chunk(chunk: dict, *, full: bool = False) -> None:
    print()
    print(f"[{chunk['document_id']}:{chunk['ordinal']}]  {chunk['token_count']} tokens", end="")
    if chunk["page_from"]:
        print(f"  pages {chunk['page_from']}-{chunk['page_to']}", end="")
    else:
        print("  (not paginated)", end="")
    print()
    print(f"  document : {chunk['document_name']}")
    print(f"  publisher: {chunk['publisher']}  ({chunk['year']})")
    print(f"  section  : {chunk['section_heading']}")
    body = chunk["text"] if full else chunk["text"][:300] + ("…" if len(chunk["text"]) > 300 else "")
    print(textwrap.fill(body, WIDTH, initial_indent="  ", subsequent_indent="  "))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inspect ingested chunks.")
    ap.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    ap.add_argument("--doc", help="document id, or any prefix of one")
    ap.add_argument("--id", help="exact chunk id as document_id:ordinal")
    ap.add_argument("--find", help="show chunks whose text contains this (case-insensitive)")
    ap.add_argument("--headings", action="store_true", help="list section headings per document")
    ap.add_argument("--short", type=int, metavar="N", help="show chunks under N tokens")
    ap.add_argument("--full", action="store_true", help="print whole chunk text, not a preview")
    args = ap.parse_args(argv)

    chunks = load(args.chunks)

    if args.id:
        doc_id, _, ordinal = args.id.rpartition(":")
        match = [c for c in chunks if c["document_id"] == doc_id and str(c["ordinal"]) == ordinal]
        if not match:
            sys.exit(f"no chunk {args.id!r}")
        show_chunk(match[0], full=True)
        return 0

    if args.headings:
        by_doc: dict[str, list[str]] = collections.defaultdict(list)
        for chunk in chunks:
            heading = chunk["section_heading"]
            if heading and heading not in by_doc[chunk["document_id"]]:
                by_doc[chunk["document_id"]].append(heading)
        for doc_id, headings in by_doc.items():
            print(f"\n{doc_id}  ({len(headings)} sections)")
            for heading in headings:
                print(f"  · {heading}")
        return 0

    selected = chunks
    if args.doc:
        selected = [c for c in selected if c["document_id"].startswith(args.doc)]
    if args.find:
        needle = args.find.lower()
        selected = [c for c in selected if needle in c["text"].lower()]
    if args.short is not None:
        selected = [c for c in selected if c["token_count"] < args.short]

    if selected is chunks:
        summarise(chunks)
        return 0

    if not selected:
        print("no chunks matched")
        return 1

    for chunk in selected:
        show_chunk(chunk, full=args.full)
    print(f"\n{len(selected)} chunk(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
