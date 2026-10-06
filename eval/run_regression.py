"""Phase 7 regression runner (implementation-plan.md Phase 7, eval.md §2).

Runs every question in regression_questions.json through the real pipeline --
ScopeGuard.check_request -> ModelClient.get_structured_answer -> ScopeGuard.check_response
-- exactly mirroring routers/chat.py's sequence, minus DB persistence (this suite is
file-based, per eval.md's Storage row). Costs real Groq API credits per run; not part
of the automated test suite.

Usage (from repo root, with GROQ_API_KEY set for the backend):
    backend/.venv/bin/python eval/run_regression.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from groq import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError  # noqa: E402
from services.model_client import GroqModelClient, ModelResponseError  # noqa: E402
from services.scope_guard import check_request, check_response  # noqa: E402

MAX_RETRIES = 6

# `InternalServerError` covers Groq's 503 "model is currently over capacity", whose own
# message says to back off exponentially. It was not retried until 2026-10-05, when a run
# died eleven questions in and took the credits already spent with it. A transport hiccup
# is not a finding; it must not be able to end a regression run.
RETRYABLE = (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)


def _call_with_retry(client: GroqModelClient, system_prompt: str, question: str):
    for attempt in range(MAX_RETRIES):
        try:
            return client.get_structured_answer(system_prompt, [], question)
        except RETRYABLE as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            wait_seconds = 2 ** (attempt + 1)
            print(f"  {type(exc).__name__}, retrying in {wait_seconds}s")
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")

QUESTIONS_PATH = Path(__file__).resolve().parent / "regression_questions.json"
RUNS_DIR = Path(__file__).resolve().parent / "runs"
SYSTEM_PROMPT_PATH = BACKEND_ROOT / "prompts" / "system_prompt.md"


def run_one(client: GroqModelClient, system_prompt: str, question: str) -> dict:
    pre_verdict = check_request(question)
    if pre_verdict.blocked:
        return {"outcome": "refused", "reason": pre_verdict.reason, "stage": "pre_model"}

    try:
        answer = _call_with_retry(client, system_prompt, question)
    except ModelResponseError as exc:
        return {"outcome": "validation_failure", "error": str(exc)}

    post_verdict = check_response(answer)
    if post_verdict.blocked:
        return {"outcome": "refused", "reason": post_verdict.reason, "stage": "post_model"}

    return {
        "outcome": "answer",
        "answer": answer.answer,
        "claims": [c.claim for c in answer.claims],
    }


def load_previous_run() -> dict | None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(RUNS_DIR.glob("*.json"))
    if not existing:
        return None
    return json.loads(existing[-1].read_text())


def load_questions() -> list[dict]:
    """The set gained an `_about` block in Phase 2.8, so it is now an object.

    This is the only change 2.8 made to this harness. What it *measures* is untouched --
    `check_response`, the Phase 1 three categories, the uncited `get_structured_answer`
    path -- because this run is the baseline the RAG run's outcome flips are read against,
    and a baseline that moved with the thing it measures would be worthless.
    """
    loaded = json.loads(QUESTIONS_PATH.read_text())
    return loaded["questions"] if isinstance(loaded, dict) else loaded


def main() -> None:
    questions = load_questions()
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    previous_run = load_previous_run()
    previous_by_id = {r["id"]: r for r in previous_run["results"]} if previous_run else {}

    client = GroqModelClient()
    results = []
    mismatches: list[str] = []
    flips: list[str] = []

    for q in questions:
        print(f"=== {q['id']} [{q['category']}]: {q['question']}")
        outcome = run_one(client, system_prompt, q["question"])
        record = {**q, **outcome}
        results.append(record)
        if outcome["outcome"] != "refused":
            time.sleep(1.5)  # stay under Groq's free-tier tokens-per-minute limit

        expected = q.get("expected_outcome")
        if expected is not None and outcome["outcome"] != expected:
            msg = f"{q['id']}: expected outcome={expected!r} but got {outcome['outcome']!r}"
            mismatches.append(msg)
            print(f"  MISMATCH: {msg}")
        elif outcome["outcome"] == "refused":
            print(f"  refused ({outcome['stage']}, reason={outcome['reason']})")
        elif outcome["outcome"] == "answer":
            print(f"  answer: {outcome['answer'][:100]}...")
        else:
            print(f"  validation_failure: {outcome['error']}")

        prev = previous_by_id.get(q["id"])
        if prev is not None and prev["outcome"] != outcome["outcome"]:
            msg = f"{q['id']}: outcome flipped {prev['outcome']!r} -> {outcome['outcome']!r}"
            flips.append(msg)
            print(f"  OUTCOME FLIP vs previous run: {msg}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_path = RUNS_DIR / f"{timestamp}.json"
    run_path.write_text(json.dumps({"timestamp": timestamp, "results": results}, indent=2))

    print(f"\n=== Regression Summary ({len(questions)} questions) ===")
    print(f"Run written to {run_path.relative_to(REPO_ROOT)}")
    if previous_run is None:
        print("No previous run found -- this is the baseline.")
    print(f"Expectation mismatches: {len(mismatches)}")
    for m in mismatches:
        print(f"  - {m}")
    print(f"Outcome flips vs previous run: {len(flips)}")
    for f in flips:
        print(f"  - {f}")

    if mismatches or flips:
        sys.exit(1)


if __name__ == "__main__":
    main()
