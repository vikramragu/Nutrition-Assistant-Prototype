"""Embedding client for retrieval (architecture.md §2, §5.4, §5.5).

`BAAI/bge-small-en-v1.5` served locally as ONNX via `fastembed`. Local rather than an
API because Groq has no embeddings endpoint, and adding one would make this a
two-provider project for the sake of 384 floats.

**The asymmetry is the whole reason this module exists.** BGE v1.5 models are trained for
asymmetric retrieval: a passage is encoded bare, a query is encoded with an instruction
prefix. Get it wrong and nothing fails -- retrieval quality just drops, and because the
not-in-corpus refusal is keyed to a similarity floor, the symptom presents as
*over-refusal* rather than as a bug.

So the protocol exposes two methods rather than one with a flag. You cannot call this
wrong by forgetting an argument; you have to call the wrong method.

**Measured 2026-10-04, and the reason the prefix is applied by hand here:**
`fastembed`'s own `query_embed()` is *byte-identical* to `embed()` for this model --
`np.allclose(...) is True`. It does **not** apply the BGE prefix. Delegating to it would
have produced exactly the silent degradation described above. Manually prefixing yields a
measurably different vector (cosine 0.979 against the unprefixed form).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_ID = os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL_ID)

# Where the 64 MB of ONNX weights live. **This default is the baked-in location**, not a
# scratch directory: `scripts/prefetch_embedding_model.py` writes here at image build time
# and the running container reads from here (architecture.md §12.3).
#
# The constant exists so the build step and the runtime cannot disagree about the path. If
# they did, the symptom would not be an error -- fastembed would simply download the weights
# again at boot, turning a 0.09 s warm load into a 15 s cold one that fails whenever Hugging
# Face is slow. That is the exact failure this baking is meant to remove, and it would be
# invisible except as an occasional slow deploy.
#
# `FASTEMBED_CACHE_PATH` overrides it. The override is for unusual images; forgetting to set
# it must not break anything, which is why the default points at the baked location rather
# than requiring the variable to be present.
DEFAULT_CACHE_PATH = str(Path(__file__).resolve().parent.parent / ".fastembed_cache")
CACHE_PATH = os.environ.get("FASTEMBED_CACHE_PATH") or DEFAULT_CACHE_PATH

# Required by BGE v1.5 English models for the query side only. Do not apply to passages.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# bge-small-en-v1.5 output width. Must match the `vector(384)` column in the migration.
EMBEDDING_DIM = 384


@runtime_checkable
class EmbeddingClient(Protocol):
    """Two methods, deliberately. See the module docstring."""

    model_id: str
    dimension: int

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode corpus chunks. No prefix."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Encode a user question. Prefixed."""
        ...


class FastEmbedClient:
    """ONNX-backed client. Loads the model eagerly.

    Eager loading is intentional (architecture.md §12.3): the first use otherwise costs
    ~0.35 s warm, or ~15 s cold if the weights have to be downloaded. That belongs on
    application startup, not on a user's first question. In production the weights are
    baked into the image and `FASTEMBED_CACHE_PATH` points at them.
    """

    def __init__(self, model_id: str = MODEL_ID, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self.model_id = model_id
        self.dimension = EMBEDDING_DIM
        self.cache_dir = cache_dir or CACHE_PATH
        self._model = TextEmbedding(model_name=model_id, cache_dir=self.cache_dir)
        logger.info(
            "embedding model loaded: %s (%d dims) from %s",
            model_id,
            self.dimension,
            self.cache_dir,
        )

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [[float(x) for x in vector] for vector in self._model.embed(list(texts))]
        self._assert_width(vectors)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        # Explicit prefix. fastembed's query_embed() does NOT do this -- see module docstring.
        vectors = [[float(x) for x in v] for v in self._model.embed([QUERY_PREFIX + text])]
        self._assert_width(vectors)
        return vectors[0]

    def _assert_width(self, vectors: list[list[float]]) -> None:
        """A width mismatch means the configured model is not the one we think it is.

        Caught here rather than at the database, where it would surface as an opaque
        pgvector insert error several steps removed from the cause.
        """
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"{self.model_id} produced {len(vector)} dimensions, expected "
                    f"{self.dimension}. The vector(384) column and this model disagree."
                )


_client: FastEmbedClient | None = None


def get_embedding_client() -> FastEmbedClient:
    """Process-wide singleton. The model is ~279 MB resident; load it once."""
    global _client
    if _client is None:
        _client = FastEmbedClient()
    return _client
