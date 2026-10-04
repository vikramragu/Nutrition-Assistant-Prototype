"""Download and verify corpus documents (architecture.md §5.1).

The verification here exists because of a specific failure observed on 2026-10-04: two
documents truncated silently under a 45-second timeout and parsed as 5 and 4 pages
instead of 7 and 12. A truncated PDF does not announce itself -- it opens, reports fewer
pages, and the corpus is quietly short. Every assertion below is a guard against a
failure that produces a *plausible* result rather than an error.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from corpus.models import FetchedDocument, ManifestEntry

logger = logging.getLogger(__name__)

# Several publishers in the corpus return 403 to non-browser clients.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Document 3 is 6.37 MB and truncated under 45s. 240s is the floor, not a target.
TIMEOUT_SECONDS = 240.0

# `identity` is deliberate. With compression on, `Content-Length` describes the
# *compressed* stream while the client hands us decompressed bytes, so the completeness
# check compares two different numbers -- FSANZ declares 149,624 and yields 163,862.
# Asking for identity keeps the header meaning what the check assumes it means.
DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}

_LAST_UPDATED_RE = re.compile(
    r"(?:last\s+(?:updated|reviewed)|date\s+(?:published|modified))\s*[:\-]?\s*([^<\n]{3,60})",
    re.IGNORECASE,
)


class FetchError(RuntimeError):
    """A document could not be fetched, or arrived in a state we refuse to trust."""


def load_manifest(path: Path) -> list[ManifestEntry]:
    raw = yaml.safe_load(path.read_text())
    return [ManifestEntry(**entry) for entry in raw]


def fetch(entry: ManifestEntry, dest_dir: Path, *, client: httpx.Client | None = None) -> FetchedDocument:
    dest_dir.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True,
        timeout=TIMEOUT_SECONDS,
        headers=DEFAULT_HEADERS,
    )
    try:
        response = client.get(entry.source_url)
    except httpx.HTTPError as exc:
        raise FetchError(f"{entry.id}: request failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    if response.status_code != 200:
        raise FetchError(f"{entry.id}: HTTP {response.status_code} (expected 200)")

    body = response.content
    _assert_complete(entry, response, body)
    _assert_is_content(entry, response, body)

    digest = hashlib.sha256(body).hexdigest()
    _assert_unchanged(entry, digest)

    path = dest_dir / f"{entry.id}{_suffix_for(entry.media_type)}"
    path.write_bytes(body)

    return FetchedDocument(
        entry=entry,
        path=path,
        sha256=digest,
        byte_count=len(body),
        retrieval_date=datetime.now(UTC),
        content_length_declared=_declared_length(response),
        page_last_updated=_page_last_updated(entry, body),
    )


def _declared_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    return int(raw) if raw and raw.isdigit() else None


def _assert_complete(entry: ManifestEntry, response: httpx.Response, body: bytes) -> None:
    """Catch silent truncation.

    `Content-Length` is the primary check, but it is not always usable:

    - **Not served at all.** The Irish Department of Health omits it on `GET` (it does
      send one on `HEAD`, which is why an earlier HEAD-based survey missed this).
    - **Describes compressed bytes.** Handled by requesting identity encoding above; if
      a server compresses anyway, the comparison is meaningless and is skipped.

    In both cases the sha256 pin in the manifest becomes the completeness check instead.
    Failing closed on a missing header would reject documents that are in fact fine.
    """
    if response.headers.get("content-encoding"):
        # Server ignored our `identity` request and compressed anyway: `Content-Length`
        # now describes the wire bytes, not the decoded ones. Nothing to compare.
        return
    declared = _declared_length(response)
    if declared is None:
        return
    if declared != len(body):
        raise FetchError(
            f"{entry.id}: truncated download -- Content-Length declared {declared} bytes, "
            f"received {len(body)}. Raise the timeout and retry."
        )


def _assert_is_content(entry: ManifestEntry, response: httpx.Response, body: bytes) -> None:
    """A landing page, redirect stub or error body is not content."""
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if content_type != entry.media_type:
        raise FetchError(
            f"{entry.id}: expected {entry.media_type}, got {content_type!r} -- "
            f"likely a landing page or an error body, not the document."
        )
    if entry.media_type == "application/pdf" and not body.startswith(b"%PDF-"):
        raise FetchError(f"{entry.id}: body is not a PDF (no %PDF- magic), got {body[:16]!r}")


def _assert_unchanged(entry: ManifestEntry, digest: str) -> None:
    """Detect a publisher revising a document in place.

    The manifest pins a sha256 *prefix* rather than the full digest -- enough to catch a
    revision, short enough to stay readable in a YAML file a human reviews. A mismatch is
    not necessarily an error: guidance gets updated. It is a signal that the corpus needs
    re-reviewing and the manifest's year and page count re-verified.
    """
    if entry.sha256 and not digest.startswith(entry.sha256):
        raise FetchError(
            f"{entry.id}: content changed since the manifest was frozen. "
            f"Expected sha256 starting {entry.sha256}, got {digest[:16]}. "
            f"Re-verify the document, then update corpus.yaml deliberately."
        )


def _page_last_updated(entry: ManifestEntry, body: bytes) -> str | None:
    """Capture an HTML page's own publication date.

    For a page revised in place under the same URL, this date plus the retrieval date
    together are the version identifier (architecture.md §5.1) -- there is no edition
    number to fall back on.

    trafilatura's metadata extractor is tried first because a regex over the markup is
    not good enough: the WHO page renders its date as a bare *"26 January 2026"* with no
    "last updated" label anywhere, which the pattern below would miss entirely. The
    regex stays as a fallback for pages that do label it.
    """
    if entry.media_type != "text/html":
        return None

    text = body.decode("utf-8", errors="replace")
    try:
        import trafilatura

        metadata = trafilatura.extract_metadata(text)
        if metadata is not None and getattr(metadata, "date", None):
            return str(metadata.date)
    except Exception as exc:  # noqa: BLE001 -- fall through to the regex
        logger.warning("%s: trafilatura metadata extraction failed (%s)", entry.id, exc)

    match = _LAST_UPDATED_RE.search(text)
    return match.group(1).strip() if match else None


def _suffix_for(media_type: str) -> str:
    return {"application/pdf": ".pdf", "text/html": ".html"}.get(media_type, ".bin")
