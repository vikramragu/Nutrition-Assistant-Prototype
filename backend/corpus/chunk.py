"""Group parsed blocks into retrievable chunks (architecture.md §5.3).

Three things drive the design, two of them discovered during Phase 2.0:

1. **The token budget is bounded by the embedding model, not by taste.**
   `BAAI/bge-small-en-v1.5` truncates at 512 tokens, and §5.4 prepends
   `"{document} — {heading}\\n\\n"` to every chunk before embedding. A 512-token chunk
   would therefore lose its tail *silently*, with no error anywhere. The text budget is
   set below 512 to leave the header room.

2. **Most documents here have no usable section structure.** Six of seven are leaflets;
   the WHO fact sheet yields one heading across six pages. The no-heading path is the
   primary path, not an edge case, and `section_heading` stays `None` rather than being
   invented.

3. **Blocks are never split except as a last resort.** The parser's block boundaries are
   the best available proxy for a table or a numbered-recommendation list, and
   `find_tables()` was shown to be unreliable on this corpus. A chunk holding half a
   storage-time table retrieves confidently and answers wrongly, which is worse than an
   oversized chunk.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Callable, Iterable

from corpus.models import Block, Chunk, ParsedDocument

logger = logging.getLogger(__name__)

TOKENIZER_ID = "BAAI/bge-small-en-v1.5"

# bge-small-en-v1.5 truncates at 512 tokens. §5.4 prepends a document/heading header
# before embedding, so the text itself gets less than the full window.
MODEL_MAX_TOKENS = 512
HEADER_ALLOWANCE_TOKENS = 64
TEXT_BUDGET_TOKENS = MODEL_MAX_TOKENS - HEADER_ALLOWANCE_TOKENS - 48  # = 400
OVERLAP_TOKENS = 64

# A chunk this small carries too little context to retrieve well -- it matches on a
# phrase and then gives the model almost nothing to answer from. Undersized chunks are
# merged forward into the next one where the budget allows.
MIN_CHUNK_TOKENS = 40

# Sections that are *about* the document rather than guidance from it. Excluded
# entirely, for two different reasons:
#
# - A bibliography is a list of other people's study names. Retrieved into an answer's
#   context, it is a direct invitation for the model to attribute a claim to "Hooper et
#   al., Cochrane 2015" -- reintroducing the `unverifiable_source` failure Phase 1
#   suppressed with a prompt rule. The whole point of this milestone is that a citation
#   points at a passage the user can open, not at a name the model saw nearby.
# - A table of contents is a list of headings. It matches queries lexically and answers
#   nothing.
NON_CONTENT_HEADINGS = frozenset(
    {
        "references",
        "reference",
        "bibliography",
        "further reading",
        "acknowledgments",
        "acknowledgements",
        "contents",
        "table of contents",
        "index",
        "notes",
        "abbreviations",
    }
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIST_MARKER = re.compile(r"(?:^|\s)(?:[•·▪◦‣–-]|\(?\d{1,2}[.)])\s+")

TokenCounter = Callable[[str], int]


def default_token_counter() -> TokenCounter:
    """Exact counts from the real tokenizer, with a documented fallback.

    An approximate counter is acceptable here only because the budget already carries a
    112-token safety margin below the model's window. It is not acceptable to let the
    count be wrong *and* run the budget to 512.
    """
    try:
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_pretrained(TOKENIZER_ID)
    except Exception as exc:  # noqa: BLE001 -- offline, or tokenizers absent
        logger.warning("falling back to approximate token counting (%s)", exc)
        return lambda text: max(1, round(len(text.split()) * 1.3))
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)


def chunk_document(
    parsed: ParsedDocument,
    *,
    token_counter: TokenCounter | None = None,
    budget: int = TEXT_BUDGET_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    count = token_counter or default_token_counter()
    entry = parsed.entry
    chunks: list[Chunk] = []
    ordinal = 0

    for heading, body in _sections(parsed.blocks):
        if _is_non_content(heading):
            logger.info("skipping non-content section %r in %s", heading, entry.id)
            continue
        for text, page_from, page_to in _pack(body, count, budget, overlap):
            chunks.append(
                Chunk(
                    document_id=entry.id,
                    ordinal=ordinal,
                    text=text,
                    token_count=count(text),
                    page_from=page_from,
                    page_to=page_to,
                    section_heading=heading,
                    document_name=entry.name,
                    publisher=entry.publisher,
                    year=entry.year,
                )
            )
            ordinal += 1

    chunks = _merge_undersized(chunks, count, budget)
    _warn_on_truncation_risk(chunks, count)
    return chunks


def _merge_undersized(chunks: list[Chunk], count: TokenCounter, budget: int) -> list[Chunk]:
    """Fold chunks below `MIN_CHUNK_TOKENS` into the following chunk.

    Short sections are the norm in this corpus -- a heading followed by a single
    sentence -- and each would otherwise become its own near-contextless chunk. The
    merged chunk keeps the *earlier* heading, because that is the one the reader was
    under when the text began.

    A chunk that cannot merge (no successor, or the result would blow the budget) is
    kept as-is rather than dropped: short text is still citable text.
    """
    merged: list[Chunk] = []
    pending: Chunk | None = None

    for chunk in chunks:
        if pending is None:
            pending = chunk
            continue
        combined = count(f"{pending.text} {chunk.text}")
        if pending.token_count < MIN_CHUNK_TOKENS and combined <= budget:
            pending = dataclasses.replace(
                pending,
                text=f"{pending.text} {chunk.text}",
                token_count=combined,
                page_to=max(pending.page_to, chunk.page_to),
                section_heading=pending.section_heading or chunk.section_heading,
            )
            continue
        merged.append(pending)
        pending = chunk

    if pending is not None:
        merged.append(pending)

    return [dataclasses.replace(c, ordinal=i) for i, c in enumerate(merged)]


def _is_non_content(heading: str | None) -> bool:
    """Exact match on the normalised heading, plus a substring test for reference lists.

    The substring test exists because the FSSAI document titles its bibliography
    *"Other References"* -- an exact-match set would have let a list of journal citations
    straight into the index, which is the specific thing NON_CONTENT_HEADINGS is for.
    """
    if not heading:
        return False
    normalised = re.sub(r"[^a-z ]", " ", heading.lower())
    normalised = " ".join(normalised.split())
    if normalised in NON_CONTENT_HEADINGS:
        return True
    return any(word in normalised for word in ("reference", "bibliograph"))


def _sections(blocks: Iterable[Block]) -> list[tuple[str | None, list[Block]]]:
    """Split blocks into (heading, body-blocks) sections.

    Body text appearing before any heading -- which is most of this corpus -- forms a
    leading section with `heading=None`. That null is deliberate: a fabricated heading
    would appear in every citation drawn from these documents.
    """
    sections: list[tuple[str | None, list[Block]]] = []
    heading: str | None = None
    body: list[Block] = []

    for block in blocks:
        if block.is_heading:
            if body:
                sections.append((heading, body))
                body = []
            heading = block.text
        else:
            body.append(block)

    if body:
        sections.append((heading, body))
    return sections


def _pack(
    blocks: list[Block],
    count: TokenCounter,
    budget: int,
    overlap: int,
) -> list[tuple[str, int, int]]:
    """Accumulate whole blocks up to the budget, with sentence-level overlap."""
    packed: list[tuple[str, int, int]] = []
    buffer: list[Block] = []
    buffer_tokens = 0

    for block in blocks:
        tokens = count(block.text)

        if tokens > budget:
            # One block alone exceeds the budget. Flush what we have, then deal with it.
            if buffer:
                packed.append(_emit(buffer))
                buffer, buffer_tokens = [], 0
            packed.extend(_split_oversized(block, count, budget))
            continue

        if buffer_tokens + tokens > budget and buffer:
            packed.append(_emit(buffer))
            carry = _carry_over(buffer, count, overlap)
            buffer = carry
            buffer_tokens = sum(count(b.text) for b in carry)

        buffer.append(block)
        buffer_tokens += tokens

    if buffer:
        packed.append(_emit(buffer))
    return packed


def _emit(buffer: list[Block]) -> tuple[str, int, int]:
    pages = [b.page for b in buffer if b.page]
    return (
        " ".join(b.text for b in buffer),
        min(pages) if pages else 0,
        max(pages) if pages else 0,
    )


def _carry_over(buffer: list[Block], count: TokenCounter, overlap: int) -> list[Block]:
    """Take the trailing ~`overlap` tokens of the emitted chunk into the next one.

    Overlap is taken at sentence boundaries, not token boundaries -- half a sentence
    carried into the next chunk is noise in the embedding, not context.
    """
    if overlap <= 0 or not buffer:
        return []
    tail = buffer[-1]
    sentences = _SENTENCE_SPLIT.split(tail.text)
    kept: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        tokens = count(sentence)
        if total + tokens > overlap and kept:
            break
        kept.insert(0, sentence)
        total += tokens
    if not kept or len(kept) == len(sentences):
        return []
    return [Block(text=" ".join(kept), page=tail.page, font_size=tail.font_size, is_heading=False)]


def _split_oversized(block: Block, count: TokenCounter, budget: int) -> list[tuple[str, int, int]]:
    """Last resort for a single block larger than the budget.

    A block that looks like a list or a table is kept whole and allowed to exceed the
    budget -- see the module docstring. Everything else is split on sentence boundaries.
    An oversized chunk will be truncated by the embedding model, so it is logged loudly.
    """
    if _looks_structured(block.text):
        logger.warning(
            "oversized structured block kept whole on page %s (%s tokens > %s budget); "
            "its tail will be truncated at embedding time",
            block.page,
            count(block.text),
            budget,
        )
        return [(block.text, block.page, block.page)]

    out: list[tuple[str, int, int]] = []
    current: list[str] = []
    total = 0
    for sentence in _SENTENCE_SPLIT.split(block.text):
        tokens = count(sentence)
        if total + tokens > budget and current:
            out.append((" ".join(current), block.page, block.page))
            current, total = [], 0
        current.append(sentence)
        total += tokens
    if current:
        out.append((" ".join(current), block.page, block.page))
    return out


def _looks_structured(text: str) -> bool:
    """Heuristic for a list or table that must not be split.

    Deliberately *not* `page.find_tables()`, which reported bulleted prose as 5-column
    grids on the FSANZ document during Phase 2.0. Counting list markers is cruder and
    errs toward keeping things together, which is the safe direction here.
    """
    return len(_LIST_MARKER.findall(text)) >= 2


def _warn_on_truncation_risk(chunks: list[Chunk], count: TokenCounter) -> None:
    """Flag chunks whose embedding input will exceed the model window.

    This is the silent failure the budget exists to prevent, so it is checked against
    the *actual* embedding input rather than assumed from the text budget.
    """
    for chunk in chunks:
        total = count(chunk.embedding_input())
        if total > MODEL_MAX_TOKENS:
            logger.warning(
                "chunk %s:%s embeds to %s tokens, over the %s model window -- "
                "its tail will be silently dropped",
                chunk.document_id,
                chunk.ordinal,
                total,
                MODEL_MAX_TOKENS,
            )
