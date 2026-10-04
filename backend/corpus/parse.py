"""Turn fetched bytes into ordered, heading-classified text blocks (architecture.md §5.2).

Parser choice, verified against the whole corpus on 2026-10-04:

- **PyMuPDF handles every document**, including the two whose damaged cross-reference
  tables make `pypdf` raise `Invalid object in /Pages`, and the three using CID-encoded
  fonts. `pdfminer.six` is defensive insurance, not load-bearing.
- **`find_tables()` is not used.** It reports bulleted prose as 5-column grids on at
  least two pages of the FSANZ document. Naive table detection would make ordinary text
  look unsplittable.

Heading detection is the part that needed rethinking after Phase 2.0. A font-size sweep
found that most large-text runs in this corpus are *cover-title fragments* -- the DGA
yields "Dietary" / "Guidelines" / "For Americans" as three separate candidates -- and
that the WHO fact sheet contains one heading across six pages. So: lines are classified
first, then **consecutive heading lines are merged**, and several documents are expected
to produce no headings at all.
"""

from __future__ import annotations

import collections
import logging
import re
from typing import NamedTuple
from xml.etree import ElementTree

from corpus.models import Block, FetchedDocument, ParsedDocument


class Line(NamedTuple):
    """One visual line, with the signals heading classification needs."""

    text: str
    page: int
    size: float
    bold: bool
    pdf_block: int

logger = logging.getLogger(__name__)

# A line qualifies as a heading only if it is meaningfully larger than body text.
# 1.25, not 1.15: at 1.15 the FSANZ document's ordinary body lines (which vary between
# 9.0 and 10.4pt) were classified as headings, severing sentences mid-clause.
HEADING_SIZE_RATIO = 1.25

# Bold text slightly larger than body is also a heading -- many run-in headings in this
# corpus are bold at near-body size and would be missed by the size test alone.
BOLD_HEADING_RATIO = 1.02
BOLD_FLAG = 1 << 4  # PyMuPDF span flags: bit 4 is bold

# Longer than this and it is a sentence set in large type, not a heading.
HEADING_MAX_CHARS = 120

# A line repeated on at least this many pages is running header/footer furniture.
REPEAT_PAGE_THRESHOLD = 3

_SENTENCE_END = re.compile(r"[.!?:]\s*$")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_STARTS_LOWER = re.compile(r"^[a-z]")
# Page numbers, figure labels and other bare-number lines. They become 1-token chunks.
_NUMERIC_ONLY = re.compile(r"^[\W\d]+$")
# PDFs with broken glyph maps emit C0 control characters where a symbol belongs --
# FSANZ renders the degree sign as \x01. Strip rather than guess at the intended glyph.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DIGITS = re.compile(r"\d+")

# A web page has no pages. 0 means "not paginated" -- more honest than claiming page 1,
# which is what an earlier version did and which would have put a false page number in
# every citation drawn from an HTML source.
HTML_NO_PAGE = 0

# trafilatura's XML body elements worth keeping. `head` is handled separately.
_HTML_BODY_TAGS = frozenset({"p", "list", "table", "quote"})


class ParseError(RuntimeError):
    """A document could not be parsed into usable text."""


def parse(fetched: FetchedDocument) -> ParsedDocument:
    if fetched.entry.media_type == "text/html":
        return _parse_html(fetched)
    return _parse_pdf(fetched)


# --------------------------------------------------------------------------- PDF


def _parse_pdf(fetched: FetchedDocument) -> ParsedDocument:
    try:
        parsed = _parse_pdf_pymupdf(fetched)
    except Exception as exc:  # noqa: BLE001 -- any parser failure falls back
        logger.warning("pymupdf failed on %s (%s); falling back to pdfminer", fetched.entry.id, exc)
        parsed = _parse_pdf_pdfminer(fetched)
    else:
        if parsed.page_count == 0 or not parsed.blocks:
            logger.warning("pymupdf produced no content for %s; falling back", fetched.entry.id)
            parsed = _parse_pdf_pdfminer(fetched)

    _assert_page_count(fetched, parsed.page_count)
    return parsed


def _parse_pdf_pymupdf(fetched: FetchedDocument) -> ParsedDocument:
    import pymupdf  # `fitz` is the deprecated alias and warns on import

    doc = pymupdf.open(fetched.path)
    try:
        page_lines = [_lines_on_page(page, number) for number, page in enumerate(doc, start=1)]
        page_count = doc.page_count
    finally:
        doc.close()

    flat = [line for page in page_lines for line in page]
    if not flat:
        return ParsedDocument(fetched.entry, [], page_count, "pymupdf")

    body_size = _body_font_size(flat)
    furniture = _running_furniture(page_lines)
    blocks = _blocks_from_lines(flat, body_size, furniture)

    return ParsedDocument(fetched.entry, blocks, page_count, "pymupdf", body_font_size=body_size)


def _lines_on_page(page, page_number: int) -> list[Line]:
    """Return one `Line` per visual line, in reading order, with heading signals.

    **Blocks are reordered before anything else happens.** A PDF's content stream order
    is arbitrary, and in this corpus it disagrees with position on most pages of every
    document: the DGA emits each section heading *after* the bullets it introduces.
    Left as-is, `chunk._sections()` attaches every heading to the *preceding* section, so
    a chunk about vegetables and fruit is filed under "Gut Health". Nothing errors --
    the citation just names the wrong section, and the wrong heading is embedded into
    the chunk's vector by `Chunk.embedding_input()`.

    A plain top-to-bottom sort is *not* the fix, and trying it is what showed why: this
    document sets its bullets in two columns, so ordering by `(y, x)` splices the left
    and right columns together mid-sentence. See `_reading_order`.
    """
    lines: list[Line] = []
    for block_index, block in enumerate(_reading_order(page)):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = _clean(" ".join(span["text"] for span in spans))
            if not text or _NUMERIC_ONLY.match(text):
                continue  # page numbers and rule glyphs become 1-token chunks
            size = max((span["size"] for span in spans), default=0.0)
            bold = any(span.get("flags", 0) & BOLD_FLAG for span in spans)
            lines.append(Line(text, page_number, round(size, 1), bold, block_index))
    return lines


def _reading_order(page) -> list[dict]:
    """Order a page's text blocks the way a person reads them.

    The layout this has to survive is the DGA's: a full-width heading, then two columns
    of bullets beneath it, repeated down the page as a stack of cards. Three orderings
    were measured against it (2026-10-05):

    - **content-stream order** -- headings land after their own bullets, so every
      section heading is off by one;
    - **`(y, x)`** (what PyMuPDF's `sort=True` gives) -- headings land correctly, but
      left- and right-column bullets at the same height interleave, cutting sentences
      in half;
    - **band, then column, then y** -- correct on both counts, and on a single-column
      page it degenerates to a plain top-to-bottom sort.

    A *band* is the span between one heading and the next, so a heading always leads the
    text it introduces. Within a band, each column is read out in full before the next.
    Headings are identified by type size alone here -- deliberately cruder than
    `_is_heading`, which needs the document-wide body size that is not known yet. A
    missed heading merges two bands, which costs ordering nothing.
    """
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0 and b.get("lines")]
    if len(blocks) < 2:
        return blocks

    body_size = _page_body_size(blocks)
    is_heading = {id(b): _block_size(b) >= body_size * HEADING_SIZE_RATIO for b in blocks}
    heading_tops = sorted(b["bbox"][1] for b in blocks if is_heading[id(b)])

    bands: dict[int, list[dict]] = collections.defaultdict(list)
    for block in blocks:
        bands[sum(1 for top in heading_tops if top <= block["bbox"][1])].append(block)

    # Column detection is per band, not per page: one DGA page sets three cards in two
    # columns and a fourth ("Gut Health") full width. A single page-wide gutter cannot
    # describe that page, and looking for one finds nothing -- which is how this was
    # found, with the two-column cards still interleaving after the gutter test went in.
    ordered: list[dict] = []
    for band in sorted(bands):
        group = bands[band]
        split_x = _column_split([b for b in group if not is_heading[id(b)]], page.rect.width)
        group.sort(
            key=lambda b: (
                0 if is_heading[id(b)] else 1,
                0 if split_x is None or (b["bbox"][0] + b["bbox"][2]) / 2 < split_x else 1,
                b["bbox"][1],
            )
        )
        ordered.extend(group)
    return ordered


def _column_split(body_blocks: list[dict], page_width: float) -> float | None:
    """The x of the gutter between two text columns, or None if the page has one column.

    Detected as a **vertical strip no body block crosses**, searched only in the middle
    of the page. Measuring the gutter rather than assuming the page midpoint is the
    whole point: FSANZ sets a single column from x=147 to x=497 on a 595pt page, so a
    midpoint test calls its long lines "right column" and its short lines "left", which
    shuffles ordinary paragraphs into nonsense. That document has no gutter, so it is
    correctly left alone.

    Headings are excluded from the test because they routinely span both columns and
    would mask the gutter -- in the DGA they reach 40pt past it.
    """
    if not body_blocks:
        return None

    lo, hi = page_width * 0.3, page_width * 0.7
    spans = [(b["bbox"][0], b["bbox"][2]) for b in body_blocks]

    # Walk candidate gutters at 2pt resolution; keep the widest uncrossed run.
    best_run: tuple[float, float] | None = None
    run_start: float | None = None
    x = lo
    while x <= hi:
        crossed = any(x0 < x < x1 for x0, x1 in spans)
        if not crossed and run_start is None:
            run_start = x
        elif crossed and run_start is not None:
            if best_run is None or (x - run_start) > (best_run[1] - best_run[0]):
                best_run = (run_start, x)
            run_start = None
        x += 2.0
    if run_start is not None and (best_run is None or (hi - run_start) > (best_run[1] - best_run[0])):
        best_run = (run_start, hi)

    if best_run is None:
        return None
    # A gutter narrower than this is word spacing in a ragged single column, not a column
    # break. Both real two-column pages in this corpus clear it comfortably.
    if best_run[1] - best_run[0] < 8.0:
        return None
    return (best_run[0] + best_run[1]) / 2


def _page_body_size(blocks: list[dict]) -> float:
    """Character-weighted dominant type size on one page.

    Per page rather than per document because this runs before the document-wide size is
    known. Weighted by characters so a page with many short headings and one dense
    paragraph still reports the paragraph's size as body.
    """
    weights: collections.Counter[float] = collections.Counter()
    for block in blocks:
        for line in block["lines"]:
            for span in line.get("spans", []):
                weights[round(span["size"], 1)] += len(span.get("text", ""))
    return weights.most_common(1)[0][0] if weights else 0.0


def _block_size(block: dict) -> float:
    return max(
        (span["size"] for line in block["lines"] for span in line.get("spans", [])),
        default=0.0,
    )


def _clean(text: str) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", text).split())


def _body_font_size(lines: list[Line]) -> float:
    """The size that most *characters* are set in -- not the most common line size.

    Weighting by character count matters: a document can have more heading lines than
    body lines while body text still dominates the page.
    """
    weights: collections.Counter[float] = collections.Counter()
    for line in lines:
        weights[line.size] += len(line.text)
    return weights.most_common(1)[0][0]


def _running_furniture(page_lines: list[list[Line]]) -> set[str]:
    """Line texts that repeat across pages -- running headers, footers, page chrome.

    Left in, these become chunks of their own, or pad real chunks with a URL and a page
    number. The WHO fact sheet carries its own source URL as a running header.

    Digits are normalised away before comparing, because the commonest footer form
    carries the page number itself -- the DGA's *"Dietary Guidelines for Americans,
    2025-2030 | 6"* is textually unique on every page and would otherwise survive as a
    12-token chunk of pure furniture.
    """
    pages_seen: dict[str, set[int]] = collections.defaultdict(set)
    for lines in page_lines:
        for line in lines:
            pages_seen[_furniture_key(line.text)].add(line.page)
    return {key for key, pages in pages_seen.items() if len(pages) >= REPEAT_PAGE_THRESHOLD}


def _furniture_key(text: str) -> str:
    return _DIGITS.sub("#", text)


def _is_heading(line: Line, body_size: float, following: Line | None) -> bool:
    """Classify one line, using the line that follows it as context.

    The `following` argument is what separates a heading from a wrapped sentence. Phase
    2.1's first run classified lines like *"...is 5 C or colder or 60 C or hotter when"*
    as headings, leaving *"you:"* as the body -- a sentence severed mid-clause, and the
    reason 37 of 143 chunks came out under 50 tokens. A line that flows into the next
    one is never a heading, whatever size it is set in.
    """
    if len(line.text) > HEADING_MAX_CHARS or not _HAS_LETTER.search(line.text):
        return False
    if _SENTENCE_END.search(line.text):
        return False

    # Continuation test: this line does not end a sentence, and the next begins in
    # lower case -- they are one wrapped sentence, not a heading plus its body.
    if following is not None and _STARTS_LOWER.match(following.text):
        return False

    if line.size >= body_size * HEADING_SIZE_RATIO:
        return True
    return line.bold and line.size >= body_size * BOLD_HEADING_RATIO


def _blocks_from_lines(
    lines: list[Line],
    body_size: float,
    furniture: set[str],
) -> list[Block]:
    """Group classified lines into blocks, merging consecutive same-class lines.

    The merge is what fixes the Phase 2.0 finding: a cover title split across three
    lines becomes one heading rather than three, and a paragraph split across lines
    becomes one block rather than many.
    """
    kept = [line for line in lines if _furniture_key(line.text) not in furniture]

    blocks: list[Block] = []
    current: list[str] = []
    current_meta: tuple[int, float, bool, int] | None = None

    for index, line in enumerate(kept):
        text, page, size, _bold, pdf_block = line
        following = kept[index + 1] if index + 1 < len(kept) else None
        heading = _is_heading(line, body_size, following)
        key = (page, size if heading else body_size, heading, pdf_block)

        # Headings merge across pdf-block boundaries (titles are often split into
        # several); body text merges only within one pdf block, which is the parser's
        # own paragraph boundary.
        same_run = current_meta is not None and (
            (heading and current_meta[2] and current_meta[0] == page)
            or (not heading and not current_meta[2] and current_meta[3] == pdf_block)
        )
        if same_run:
            current.append(text)
        else:
            _flush(blocks, current, current_meta)
            current = [text]
            current_meta = key

    _flush(blocks, current, current_meta)
    return _join_continuations(blocks)


def _join_continuations(blocks: list[Block]) -> list[Block]:
    """Rejoin body blocks that together form one sentence.

    PyMuPDF emits a separate block when a paragraph crosses a column or a page break, so
    a single sentence can arrive as two blocks: *"...adequate physical activity are the"*
    followed by *"only strategies for halting..."*.

    `chunk._pack()` never splits a block, but it does split *between* blocks -- so an
    unjoined pair can end up in different chunks with the sentence severed across the
    boundary. Measured before this pass: **12 such pairs**, 9 of them in the FSANZ
    document, which is the only one long enough for the token budget to bind.

    The test is the same one heading detection uses: the first block does not end a
    sentence and the second begins in lower case. Requiring a lowercase start is what
    keeps this from merging genuinely separate items -- labelled panels and list entries
    begin with a capital.
    """
    if not blocks:
        return blocks

    joined: list[Block] = [blocks[0]]
    for block in blocks[1:]:
        previous = joined[-1]
        continues = (
            not previous.is_heading
            and not block.is_heading
            and not _SENTENCE_END.search(previous.text)
            and _STARTS_LOWER.match(block.text)
        )
        if continues:
            joined[-1] = Block(
                text=f"{previous.text} {block.text}",
                page=previous.page,
                font_size=previous.font_size,
                is_heading=False,
            )
        else:
            joined.append(block)
    return joined


def _flush(blocks: list[Block], parts: list[str], meta: tuple[int, float, bool, int] | None) -> None:
    if not parts or meta is None:
        return
    page, size, heading, _pdf_block = meta
    blocks.append(Block(text=" ".join(parts), page=page, font_size=size, is_heading=heading))


def _parse_pdf_pdfminer(fetched: FetchedDocument) -> ParsedDocument:
    """Fallback parser. Produces body blocks only -- no heading classification.

    Unexercised by the current corpus (PyMuPDF handles all seven). Kept because the two
    documents with damaged xref tables prove such files exist, and the next one added
    may be worse.
    """
    from pdfminer.high_level import extract_text
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    with fetched.path.open("rb") as handle:
        page_count = sum(1 for _ in PDFPage.create_pages(PDFDocument(PDFParser(handle))))

    blocks = [
        Block(text=" ".join(para.split()), page=0, font_size=0.0, is_heading=False)
        for para in extract_text(str(fetched.path)).split("\n\n")
        if para.strip()
    ]
    if not blocks:
        raise ParseError(f"{fetched.entry.id}: pdfminer also produced no text")
    return ParsedDocument(fetched.entry, blocks, page_count, "pdfminer")


# -------------------------------------------------------------------------- HTML


def _parse_html(fetched: FetchedDocument) -> ParsedDocument:
    """Main-content extraction for an HTML source.

    HTML is the *easier* format, and the implementation should reflect that. A PDF hides
    its structure -- headings have to be inferred from font size and boldness, which is
    what the hundred lines above are for. HTML states its structure outright, so the job
    here is to read it rather than guess at it, and to discard the boilerplate that
    surrounds it.

    `output_format="xml"` is what makes that possible. The default plain-text output
    flattens everything into lines and throws the structure away -- an earlier version of
    this function did exactly that, hardcoded `is_heading=False`, and would have produced
    chunks with no section heading at all. The XML form preserves:

        <head rend="h2">Key facts</head>     -> Block(is_heading=True)
        <p>...</p>                           -> Block(is_heading=False)
        <list><item>..</item>...</list>      -> ONE Block, list kept intact
        <table><row><cell>..</cell></row>    -> ONE Block, table kept intact

    Keeping `<list>` and `<table>` as single blocks is the HTML counterpart of §5.3's
    "never split a table" rule -- and here it is exact rather than heuristic, because the
    markup says where the list ends.
    """
    import trafilatura

    raw = fetched.path.read_text(encoding="utf-8", errors="replace")
    xml = trafilatura.extract(
        raw,
        output_format="xml",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if not xml:
        raise ParseError(f"{fetched.entry.id}: trafilatura extracted no main content")

    try:
        main = ElementTree.fromstring(xml).find("main")
    except ElementTree.ParseError as exc:
        raise ParseError(f"{fetched.entry.id}: trafilatura returned unparseable XML: {exc}") from exc
    if main is None:
        raise ParseError(f"{fetched.entry.id}: trafilatura output has no <main> element")

    blocks: list[Block] = []
    for element in main:
        if element.tag == "head":
            text = _clean(" ".join(element.itertext()))
            if text:
                blocks.append(Block(text=text, page=HTML_NO_PAGE, font_size=0.0, is_heading=True))
        elif element.tag in _HTML_BODY_TAGS:
            text = _html_body_text(element)
            if text:
                blocks.append(Block(text=text, page=HTML_NO_PAGE, font_size=0.0, is_heading=False))

    if not blocks:
        raise ParseError(f"{fetched.entry.id}: no usable blocks after extraction")

    # Markup is not a reliable sentence boundary either: trafilatura can end one element
    # mid-clause ("...review portion sizes and pricing;") and begin the next with its
    # continuation ("through subsidies) for producers..."). Same join as the PDF path.
    return ParsedDocument(fetched.entry, _join_continuations(blocks), HTML_NO_PAGE, "trafilatura")


def _html_body_text(element) -> str:
    """Flatten one body element, preserving list and table boundaries as markers.

    List items are rejoined with a bullet rather than a plain space. That is not
    cosmetic: `chunk._looks_structured()` counts list markers to decide whether a block
    may be split, so dropping the bullets would let a long list be cut in half -- the
    failure this whole design is trying to avoid.
    """
    if element.tag == "list":
        items = [_clean(" ".join(item.itertext())) for item in element]
        return " ".join(f"• {item}" for item in items if item)
    if element.tag == "table":
        rows = [
            " | ".join(_clean(" ".join(cell.itertext())) for cell in row)
            for row in element
        ]
        return " • ".join(row for row in rows if row.strip(" |"))
    return _clean(" ".join(element.itertext()))


def _assert_page_count(fetched: FetchedDocument, actual: int) -> None:
    expected = fetched.entry.expected_pages
    if expected is not None and actual != expected:
        raise ParseError(
            f"{fetched.entry.id}: parsed {actual} pages, manifest expects {expected}. "
            f"Either the download truncated or the publisher revised the document."
        )
