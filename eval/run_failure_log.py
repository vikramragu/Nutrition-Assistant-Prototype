"""Phase 8 failure-log runner (implementation-plan.md Phase 8, eval.md §3).

Runs the fixed 10-question failure_log_questions.json once each through the real
pipeline (ScopeGuard -> ModelClient -> validation). Per eval.md §3.4 step 2, the three
numeric-heavy nutrient_requirements questions (ids 1-3) are additionally rerun two more
times each, since "inconsistent number" is only detectable across repeated runs of the
same question. Saves raw output to eval/failure_log_runs/<timestamp>.json and creates
the EvalRun DB row that eval/record_findings.py attaches findings to.

Usage (from repo root, with GROQ_API_KEY and DATABASE_URL set for the backend):
    backend/.venv/bin/python eval/run_failure_log.py
"""

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from db.models import EvalRun  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from groq import RateLimitError  # noqa: E402
from services.model_client import GroqModelClient, ModelResponseError  # noqa: E402
from services.scope_guard import check_request, check_response  # noqa: E402

QUESTIONS_PATH = Path(__file__).resolve().parent / "failure_log_questions.json"
RUNS_DIR = Path(__file__).resolve().parent / "failure_log_runs"
SYSTEM_PROMPT_PATH = BACKEND_ROOT / "prompts" / "system_prompt.md"

RERUN_CATEGORY = "nutrient_requirements"  # ids 1-3, per eval.md §3.4 step 2
EXTRA_RERUNS = 2

MAX_RATE_LIMIT_RETRIES = 5


def _call_with_retry(client: GroqModelClient, system_prompt: str, question: str):
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        try:
            return client.get_structured_answer(system_prompt, [], question)
        except RateLimitError as exc:
            if attempt == MAX_RATE_LIMIT_RETRIES - 1:
                raise
            wait_seconds = 2**attempt
            print(f"    rate limited, retrying in {wait_seconds}s ({exc})")
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")


def run_once(client: GroqModelClient, system_prompt: str, question: str) -> dict:
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


def main() -> None:
    questions = json.loads(QUESTIONS_PATH.read_text())
    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:12]

    db = SessionLocal()
    eval_run = EvalRun(prompt_version=prompt_hash, notes="Phase 8 failure log")
    db.add(eval_run)
    db.commit()
    db.refresh(eval_run)
    run_id = str(eval_run.id)
    db.close()

    client = GroqModelClient()
    results = []

    for q in questions:
        runs_needed = 1 + (EXTRA_RERUNS if q["category"] == RERUN_CATEGORY else 0)
        print(f"=== q{q['id']} [{q['category']}]: {q['question']} ({runs_needed} run(s))")
        attempts = []
        for i in range(runs_needed):
            outcome = run_once(client, system_prompt, q["question"])
            attempts.append(outcome)
            if outcome["outcome"] == "answer":
                print(f"  [{i + 1}] answer: {outcome['answer'][:100]}...")
            elif outcome["outcome"] == "refused":
                print(f"  [{i + 1}] refused ({outcome['stage']}, reason={outcome['reason']})")
            else:
                print(f"  [{i + 1}] validation_failure: {outcome['error']}")
            time.sleep(1.5)  # stay under Groq's free-tier tokens-per-minute limit

        results.append({**q, "attempts": attempts})

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_path = RUNS_DIR / f"{timestamp}.json"
    run_path.write_text(
        json.dumps({"run_id": run_id, "timestamp": timestamp, "prompt_version": prompt_hash, "results": results}, indent=2)
    )

    print(f"\nEvalRun id: {run_id}")
    print(f"Raw output written to {run_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
