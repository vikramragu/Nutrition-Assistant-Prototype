"""Phase 2.10 failure-log runner for the **retrieval** pipeline (eval.md §3).

Runs the fixed ten questions once each through the same sequence `routers/chat.py` uses,
and reruns the three numeric `nutrient_requirements` questions twice more — `inconsistent
number` is undetectable from a single pass by definition, so the reruns are the detector,
not a nicety.

**A second runner rather than an edit to `run_failure_log.py`**, for the same reason Phase
2.8 kept two regression harnesses: that script is the Phase 1 record this run is compared
against. Rewiring it would move the baseline and the thing being measured in one commit,
and every difference would then have two candidate causes.

Costs real Groq credits: 16 question-runs, two or three generation calls each for anything
the corpus answers. Refusals cost nothing.

Usage (from repo root, with GROQ_API_KEY and DATABASE_URL set):
    backend/.venv/bin/python eval/run_rag_failure_log.py
    backend/.venv/bin/python eval/run_rag_failure_log.py --api https://<railway-url>
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

QUESTIONS_PATH = Path(__file__).resolve().parent / "failure_log_questions.json"
RUNS_DIR = Path(__file__).resolve().parent / "rag_failure_log_runs"
SYSTEM_PROMPT_PATH = BACKEND_ROOT / "prompts" / "document_answer_prompt.md"

RERUN_CATEGORY = "nutrient_requirements"  # ids 1-3, per eval.md §3.4 step 2
EXTRA_RERUNS = 2

MAX_RETRIES = 6


def post(api: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        api + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def get(api: str, path: str) -> dict:
    with urllib.request.urlopen(api + path, timeout=60) as response:
        return json.load(response)


def ask(api: str, conversation_id: str, question: str) -> dict:
    """One question against the deployed endpoint, with backoff.

    Driving the HTTP endpoint rather than importing the services is deliberate: eval.md
    §3.4 says the failure log runs against the *deployed* pipeline, and the whole point of
    the artifact is to describe what a user would actually get — including the scope guard,
    the gates and the persistence, in the order the endpoint applies them.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return post(api, "/chat", {"conversation_id": conversation_id, "message": question})
        except urllib.error.HTTPError as exc:
            if exc.code == 502:
                # The hard-failure path: a citation that did not resolve, or a
                # non-conformant model response. That is a finding, not a crash.
                return {"type": "error_502", "detail": exc.read().decode()[:400]}
            if exc.code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                wait = 15 * (attempt + 1)
                print(f"      HTTP {exc.code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"      connection problem, retrying in {wait}s")
            time.sleep(wait)
    raise AssertionError("unreachable")


def summarise(response: dict) -> dict:
    """Flatten one response into the shape a reviewer reads, keeping every quote.

    The quotes are the point: `citation_mismatch` can only be judged by reading the claim
    against the passage it cites, and a run file without them forces a reviewer back to the
    database.
    """
    kind = response.get("type")

    if kind == "answer":
        return {
            "outcome": "answer",
            "document_count": len(response["document_answers"]),
            "document_answers": [
                {
                    "document": a["document"]["name"],
                    "publisher": a["document"]["publisher"],
                    "year": a["document"]["year"],
                    "answer": a["answer"],
                    "claims": [
                        {
                            "claim": c["claim"],
                            "section": c["source"]["section_heading"],
                            "page_from": c["source"]["page_from"],
                            "quote": c["source"]["quote"],
                            "cites_own_document": (
                                c["source"]["document"]["id"] == a["document"]["id"]
                            ),
                        }
                        for c in a["claims"]
                    ],
                }
                for a in response["document_answers"]
            ],
        }

    if kind == "not_in_corpus":
        return {"outcome": "not_in_corpus", "searched": len(response.get("searched", []))}

    if kind == "refused":
        return {"outcome": "refused", "reason": response.get("reason")}

    return {"outcome": response.get("type", "unknown"), "detail": response.get("detail")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="base URL of the deployed backend",
    )
    args = parser.parse_args()
    api = args.api.rstrip("/")

    questions = json.loads(QUESTIONS_PATH.read_text())
    prompt_hash = hashlib.sha256(SYSTEM_PROMPT_PATH.read_bytes()).hexdigest()[:12]

    corpus = get(api, "/corpus")
    health = get(api, "/health")
    print(f"target : {api}")
    print(f"corpus : {len(corpus['documents'])} documents, {corpus['chunk_count']} chunks")
    print(f"config : k={health.get('retrieval_k')} floor={health.get('retrieval_floor')}")
    print(f"prompt : document_answer_prompt.md @ {prompt_hash}\n")

    results = []
    for q in questions:
        runs_needed = 1 + (EXTRA_RERUNS if q["category"] == RERUN_CATEGORY else 0)
        print(f"=== q{q['id']} [{q['category']}] {q['question']}")

        for run_index in range(runs_needed):
            # A fresh conversation per run, so a rerun cannot be influenced by having just
            # answered the same question -- which would hide the very inconsistency the
            # reruns exist to surface.
            conversation_id = post(api, "/conversations", {})["id"]
            response = ask(api, conversation_id, q["question"])
            record = {
                **q,
                "run_index": run_index,
                "conversation_id": conversation_id,
                **summarise(response),
            }
            results.append(record)

            label = record["outcome"]
            if label == "answer":
                docs = ", ".join(a["publisher"] for a in record["document_answers"])
                print(f"   run {run_index + 1}: answer from {record['document_count']} ({docs})")
            elif label == "not_in_corpus":
                print(f"   run {run_index + 1}: not_in_corpus (searched {record['searched']})")
            elif label == "refused":
                print(f"   run {run_index + 1}: refused ({record['reason']})")
            else:
                print(f"   run {run_index + 1}: {label} {record.get('detail', '')[:120]}")

            if label == "answer":
                # The free tier limits tokens per minute, and one two-document answer is
                # ~6k tokens of prompt across two calls. 1.5s was nowhere near enough: the
                # first rerun of q1 hit the wall and every retry after it did too. This is
                # throughput, not politeness -- the run takes longer but completes.
                time.sleep(12)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_path = RUNS_DIR / f"{timestamp}.json"
    run_path.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "pipeline": "rag",
                "api": api,
                "prompt_version": prompt_hash,
                "corpus_documents": len(corpus["documents"]),
                "corpus_chunks": corpus["chunk_count"],
                "retrieval_k": health.get("retrieval_k"),
                "retrieval_floor": health.get("retrieval_floor"),
                "results": results,
            },
            indent=2,
        )
    )

    by_outcome: dict[str, int] = {}
    for r in results:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1

    print(f"\n{'=' * 72}")
    print(f"{len(results)} runs over {len(questions)} questions -> {run_path.relative_to(REPO_ROOT)}")
    print(f"outcomes: {by_outcome}")
    print("\nNow read every response against all nine failure types (eval/failure_types.py).")
    print("A not_in_corpus on a question the corpus DOES cover is `over_refusal`.")


if __name__ == "__main__":
    main()
