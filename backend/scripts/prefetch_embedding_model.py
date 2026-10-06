"""Download the embedding weights at **image build time** (architecture.md §12.3).

Run by `railway.json`'s `buildCommand`, so the 64 MiB of ONNX weights are already in the
image when the container starts. (Railway builds with Railpack; an earlier `nixpacks.toml`
was dead config -- see docs/features/rag-sourced-claims/deployment.md §3.0.)

**Why this is not optional.** Measured 2026-10-04: loading the model warm takes 0.09 s;
loading it cold, with a download, takes 15 s. A container that fetches 64 MB from Hugging
Face on boot is a deploy that fails whenever that host is slow, rate-limited or blocked —
and Railway retries a failed boot, so the failure mode is a redeploy loop rather than one
slow start.

**The failure it prevents is silent, which is the real argument.** If the weights are not
baked, nothing errors: fastembed just downloads them on first use. The deploy works. It
keeps working, until the day it doesn't, and then the cause is three layers away from the
symptom.

Writes to `services.embeddings.CACHE_PATH`, the same constant the running app reads, so the
build and the runtime cannot disagree about where the weights are. Verifies the download by
embedding one string and checking the width.

Usage (from backend/):
    python scripts/prefetch_embedding_model.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.embeddings import (  # noqa: E402
    CACHE_PATH,
    EMBEDDING_DIM,
    MODEL_ID,
    FastEmbedClient,
)


def main() -> None:
    print(f"prefetching {MODEL_ID} into {CACHE_PATH}")
    Path(CACHE_PATH).mkdir(parents=True, exist_ok=True)

    client = FastEmbedClient()

    # Not ceremony: a partial or corrupt download still produces a loadable object, and the
    # width is the one property that proves the weights are the model we think they are.
    vector = client.embed_query("verify the prefetched weights produce a usable vector")
    if len(vector) != EMBEDDING_DIM:
        raise SystemExit(
            f"prefetch produced {len(vector)} dimensions, expected {EMBEDDING_DIM}: "
            f"the weights at {CACHE_PATH} are not {MODEL_ID}"
        )

    # Deduplicated by inode. The HuggingFace cache layout stores each blob once and
    # hardlinks it into `snapshots/`, so summing file sizes naively double-counts the
    # 66 MB model and reports ~200 MB -- a number that would then be quoted in an image-size
    # discussion and be wrong by 3x.
    seen: set[int] = set()
    total = 0
    biggest: list[tuple[int, Path]] = []
    for path in sorted(Path(CACHE_PATH).rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        if stat.st_ino in seen:
            continue
        seen.add(stat.st_ino)
        total += stat.st_size
        biggest.append((stat.st_size, path))

    print(f"ok: {len(vector)} dimensions, {len(seen)} unique file(s), "
          f"{total / 1_000_000:.0f} MB on disk")
    for size, path in sorted(biggest, reverse=True)[:5]:
        print(f"  {size / 1_000_000:7.1f} MB  {path.relative_to(CACHE_PATH)}")


if __name__ == "__main__":
    main()
