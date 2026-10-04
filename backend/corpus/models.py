"""Shared types for the ingestion pipeline (architecture.md §5).

These are plain dataclasses rather than Pydantic models: nothing here crosses an API
boundary or is validated against untrusted input. The Pydantic contract lives in
`db/schemas.py`; this is the offline pipeline's internal shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

YearSource = Literal["document_text", "pdf_creation_date", "page_last_updated"]


@dataclass(frozen=True)
class ManifestEntry:
    """One document as declared in corpus.yaml."""

    id: str
    name: str
    publisher: str
    year: int | None
    year_source: YearSource
    source_url: str
    media_type: str
    expected_pages: int | None = None
    sha256: str | None = None  # prefix; see fetch.py for why a prefix is enough

    # Passages to keep out of the index. Each entry is a distinctive substring plus the
    # reason it is excluded, reviewed by hand -- see corpus.yaml and chunk._is_quarantined.
    #
    # A manifest list rather than a heuristic because three text statistics were measured
    # against this corpus (repetition ratio, prose density, function-word density) and
    # none separates a destroyed table from ordinary bulleted guidance. A detector tuned
    # to catch these would also drop real advice, which is the worse error.
    quarantine: list[dict[str, str]] = field(default_factory=list)


@dataclass
class FetchedDocument:
    """A document on disk, with the provenance needed to cite it."""

    entry: ManifestEntry
    path: Path
    sha256: str
    byte_count: int
    retrieval_date: datetime
    content_length_declared: int | None
    page_last_updated: str | None = None


@dataclass
class Block:
    """A contiguous run of text from one page, classified as heading or body.

    `font_size` is the largest span size in the block, used only during heading
    detection -- it is not carried downstream.
    """

    text: str
    page: int
    font_size: float
    is_heading: bool


@dataclass
class ParsedDocument:
    entry: ManifestEntry
    blocks: list[Block]
    page_count: int
    parser_used: str
    body_font_size: float | None = None


@dataclass
class Chunk:
    """A retrievable unit. Every field here ends up visible in a citation.

    `section_heading` is nullable because several corpus documents genuinely have no
    section structure (architecture.md §5.3). A null heading is acceptable; a
    fabricated one is not.
    """

    document_id: str
    ordinal: int
    text: str
    token_count: int
    page_from: int
    page_to: int
    section_heading: str | None = None

    # Denormalised from the manifest so a chunk is self-describing in the snapshot.
    document_name: str = ""
    publisher: str = ""
    year: int | None = None

    def embedding_input(self) -> str:
        """The string actually embedded (architecture.md §5.4).

        The document name and heading carry real retrieval signal for short chunks --
        a two-page brochure's chunks are otherwise nearly contextless.
        """
        header = self.document_name
        if self.section_heading:
            header = f"{header} — {self.section_heading}"
        return f"{header}\n\n{self.text}"


@dataclass
class IngestReport:
    """What Phase 2.1's exit criteria are checked against."""

    documents: list[ParsedDocument] = field(default_factory=list)
    fetched: list[FetchedDocument] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
