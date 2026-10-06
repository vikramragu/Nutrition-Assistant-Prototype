"""Phase 2.9 deployment guards.

Three things that are only wrong in production, where nobody is watching the logs:

- the embedding model is loaded **at startup**, not on a user's first question;
- `/health` reports the configuration, so a misconfigured deploy is one curl away from
  visible rather than one wrong answer away;
- importing the app pulls in **no ingest-only dependency**, which is what makes "production
  never parses a PDF" a property of the code rather than of the requirements file.

The preload is tested by patching the loader rather than by loading 279 MB of ONNX into the
test process. What can realistically regress is the *wiring* -- someone dropping
`lifespan=lifespan` while refactoring `main.py` -- and that is what this catches. The model
genuinely loading is verified by `scripts/prefetch_embedding_model.py` and by the startup
log on a real boot.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import main
from services.embeddings import CACHE_PATH, EMBEDDING_DIM, MODEL_ID
from services.retriever import DEFAULT_FLOOR, DEFAULT_K, RETRIEVAL_FLOOR, RETRIEVAL_K

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class _StubClient:
    model_id = "stub-model"
    dimension = EMBEDDING_DIM
    cache_dir = "/stub/cache"


def test_embedding_model_is_loaded_at_startup_not_on_first_request(monkeypatch):
    """Eager, so the ~0.3 s warm load lands on the platform's health check rather than on a
    user, and so a container with missing weights fails the deploy instead of booting green
    and downloading 64 MiB at the first question."""
    calls: list[str] = []

    def fake_loader():
        calls.append("loaded")
        return _StubClient()

    monkeypatch.setattr(main, "get_embedding_client", fake_loader)

    # TestClient only runs lifespan as a context manager, which is also why the rest of the
    # suite never pays for the model.
    with TestClient(main.app) as client:
        assert calls == ["loaded"], "the model was not loaded during startup"
        client.get("/health")

    assert calls == ["loaded"], "the model was loaded again per request"


def test_no_model_is_loaded_without_entering_the_lifespan(monkeypatch):
    """Guards the premise of the test above: if a future change loaded the model at import
    or per request, the assertion there would still pass and mean nothing."""
    calls: list[str] = []
    monkeypatch.setattr(main, "get_embedding_client", lambda: calls.append("loaded"))

    TestClient(main.app).get("/health")

    assert calls == []


def test_health_reports_the_configuration_a_deploy_can_get_wrong():
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["embedding_model"] == MODEL_ID
    assert body["embedding_dim"] == EMBEDDING_DIM
    assert body["embedding_weights_path"] == CACHE_PATH
    assert body["retrieval_k"] == RETRIEVAL_K
    assert body["retrieval_floor"] == RETRIEVAL_FLOOR


def test_health_exposes_nothing_secret():
    """It is a public endpoint. A model id and two calibration numbers are already written
    down in retrieval-calibration.md; a key or a connection string is not."""
    body = TestClient(main.app).get("/health").text.lower()

    for forbidden in ("groq", "api_key", "apikey", "postgres", "password", "secret", "token"):
        assert forbidden not in body, f"/health leaked {forbidden!r}"


def test_retrieval_parameters_default_to_the_measured_values():
    """With no environment override, the deployment uses the Phase 2.4 measurement. The
    floor is the not-in-corpus refusal, so the safe default is the one that was calibrated."""
    assert (RETRIEVAL_K, RETRIEVAL_FLOOR) == (DEFAULT_K, DEFAULT_FLOOR)


def test_importing_the_app_pulls_in_no_ingest_only_dependency():
    """"Production never parses a PDF" (architecture.md §12.2), checked rather than asserted.

    `requirements-ingest.txt` keeps the parsers out of the deployed image, but nothing stops
    an import of `corpus.parse` creeping into a router and making the runtime depend on a
    package that is not installed there -- a crash that appears only in production, because
    locally every parser is present.

    A subprocess, so the measurement is a clean import rather than whatever the test session
    has already loaded.
    """
    code = (
        "import sys; before = set(sys.modules); import main; "
        "print(','.join(sorted({m.split('.')[0] for m in set(sys.modules) - before})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_ROOT, capture_output=True, text=True, check=True
    )
    imported = set(result.stdout.strip().split(","))

    # `httpx` is absent from this list on purpose: the Groq SDK depends on it, so it is a
    # runtime dependency regardless of `corpus/fetch.py` also using it.
    ingest_only = {"pymupdf", "fitz", "pdfminer", "trafilatura", "yaml"}
    assert not (imported & ingest_only), (
        f"importing the app pulled in ingest-only package(s): {sorted(imported & ingest_only)}. "
        "These are not installed in the deployed image."
    )


def test_procfile_runs_migrations_and_the_server_and_nothing_else():
    """No ingestion in the boot path (architecture.md §12.2). Ingestion needs network access
    to seven government sites and several minutes; neither belongs in something Railway will
    retry. Seeding is a separate manual command for the same reason."""
    procfile = (BACKEND_ROOT / "Procfile").read_text()

    assert "alembic upgrade head" in procfile
    assert "uvicorn main:app" in procfile
    for forbidden in ("corpus.ingest", "corpus.seed", "corpus.fetch"):
        assert forbidden not in procfile, f"Procfile runs {forbidden} at boot"


def test_the_build_bakes_the_weights_into_the_image():
    """A missing build step does not fail a deploy -- it moves the 64 MiB download back to
    boot, where it is invisible until the day Hugging Face is slow. So the build config is
    pinned by a test rather than trusted.

    This test originally read `nixpacks.toml`, and passed, while the config it was reading
    was **dead**: Railway's builder is Railpack, which does not read that file, and
    Nixpacks is no longer a selectable builder at all. The test asserted a file's contents
    rather than that the file was the one the platform reads -- so it confirmed exactly the
    thing that was wrong. Found on the first real deploy, 2026-10-06.

    Hence the builder assertion below: the build command is only live while the builder is
    the one that reads this file.
    """
    config = json.loads((BACKEND_ROOT / "railway.json").read_text())

    assert config["build"]["builder"] == "RAILPACK"
    assert config["build"]["buildCommand"] == "python scripts/prefetch_embedding_model.py"
    assert (BACKEND_ROOT / "scripts" / "prefetch_embedding_model.py").exists()
    assert not (BACKEND_ROOT / "nixpacks.toml").exists(), (
        "nixpacks.toml is dead config -- Railway builds with Railpack. Leaving it around "
        "is a trap for whoever next tries to change the build."
    )


def test_the_prefetch_script_and_the_app_agree_on_the_weights_path():
    """They share `services.embeddings.CACHE_PATH` rather than each naming a directory.

    If they diverged, nothing would error: fastembed would download the weights again at
    boot and the baking would be silently pointless.
    """
    script = (BACKEND_ROOT / "scripts" / "prefetch_embedding_model.py").read_text()

    assert "CACHE_PATH" in script
    assert "fastembed_cache" not in script, (
        "the prefetch script hardcodes a path instead of importing CACHE_PATH"
    )
