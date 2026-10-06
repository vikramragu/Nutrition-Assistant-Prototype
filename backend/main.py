import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.chat import router as chat_router
from routers.corpus import router as corpus_router
from services.embeddings import CACHE_PATH, EMBEDDING_DIM, MODEL_ID, get_embedding_client
from services.retriever import RETRIEVAL_FLOOR, RETRIEVAL_K

load_dotenv()

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the embedding model before the app reports ready (architecture.md §12.3).

    Eagerly, not lazily, and the reason is where the cost lands rather than how much it is.
    Warm it is ~0.3 s — negligible, but it is a user's first question that would pay it, and
    Railway cold-starts mean that user is a real one. Loading here puts it on the platform's
    health check instead.

    It also makes a missing-weights deploy fail *at startup*, which is the far more valuable
    property. Lazily, a container with no baked weights boots green and then downloads 64 MiB
    on the first question — fine on a good day, a timeout on a bad one, and the symptom
    arrives several layers from the cause. Here it is the deploy that fails, with the path it
    looked in printed next to it.

    No exception handling: if the model cannot load, this process cannot answer anything, and
    a half-working container that fails every question is worse than one that will not start.
    """
    client = get_embedding_client()
    logger.info(
        "startup: embedding model %s (%d dims) loaded from %s; k=%d floor=%.2f",
        client.model_id,
        client.dimension,
        client.cache_dir,
        RETRIEVAL_K,
        RETRIEVAL_FLOOR,
    )
    yield


app = FastAPI(title="AI Nutrition Assistant API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(chat_router)
app.include_router(corpus_router)


@app.get("/health")
def health() -> dict[str, str | int | float | bool]:
    """Liveness, plus the four things that are wrong most often after a deploy.

    Reporting the retrieval parameters and whether the weights were loaded from the baked
    path turns "did this deploy come up configured the way we think?" into one curl. All of
    it is non-secret — a public model id and two numbers already written down in
    retrieval-calibration.md.
    """
    return {
        "status": "ok",
        "embedding_model": MODEL_ID,
        "embedding_dim": EMBEDDING_DIM,
        "embedding_weights_path": CACHE_PATH,
        "retrieval_k": RETRIEVAL_K,
        "retrieval_floor": RETRIEVAL_FLOOR,
    }
