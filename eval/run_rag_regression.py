"""Phase 2.8 regression runner for the **retrieval** pipeline (eval.md §2.3).

Mirrors `routers/chat.py`'s sequence exactly, minus HTTP and persistence:

    check_request -> search -> apply_floor -> group_by_document
                  -> synthesise (one call per document) -> validate_document_answer
                  -> check_document_answer

Four outcomes, matching the endpoint's three wire types plus the hard-failure path:
`refused` (policy), `not_in_corpus` (coverage), `answer`, `validation_failure` (502).

**Why this is a second harness and not an edit to `run_regression.py`.** That script drives
the Phase 1 uncited path and is the recorded baseline this run's outcome flips are read
against. Rewiring it would have moved the baseline and the thing being measured in the same
commit, and every flip would then have two candidate causes -- the same mistake Phase 2.4
exists to prevent one layer down.

Costs real Groq credits: two or three generation calls per answered question. Not part of
`pytest`.

Usage (from repo root, with GROQ_API_KEY set and the corpus seeded):
    backend/.venv/bin/python eval/run_rag_regression.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from groq import (  # noqa: E402
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from db.session import SessionLocal  # noqa: E402
from services.answer_synthesiser import group_by_document, synthesise  # noqa: E402
from services.citation_validator import CitationError, lexical_overlap  # noqa: E402
from services.corpus_catalog import document_refs  # noqa: E402
from services.model_client import GroqModelClient, ModelResponseError  # noqa: E402
from services.retriever import DEFAULT_FLOOR, DEFAULT_K, apply_floor, search  # noqa: E402
from services.scope_guard import check_document_answer, check_request  # noqa: E402

QUESTIONS_PATH = Path(__file__).resolve().parent / "regression_questions.json"
RUNS_DIR = Path(__file__).resolve().parent / "rag_runs"
SYSTEM_PROMPT_PATH = BACKEND_ROOT / "prompts" / "system_prompt.md"

MAX_RETRIES = 6

# `InternalServerError` is Groq's 503 "model is currently over capacity", whose own message
# says to back off exponentially. Retrying it is not optional for a run that must complete:
# on 2026-10-05 an unretried 503 ended a regression eleven questions in.
RETRYABLE = (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)


class _RetryingClient:
    """Wraps the Groq client so a transport failure is a wait, not a lost run.

    Retries are per *document call*, inside the synthesiser's thread pool, because that is
    where the call happens -- retrying the whole question would re-pay for the documents
    that already succeeded.
    """

    def __init__(self, inner: GroqModelClient) -> None:
        self._inner = inner

    def answer_from_document(self, system_prompt, document_prompt, history, user_message):
        for attempt in range(MAX_RETRIES):
            try:
                return self._inner.answer_from_document(
                    system_prompt, document_prompt, history, user_message
                )
            except RETRYABLE as exc:
                if attempt == MAX_RETRIES - 1:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"    {type(exc).__name__}, retrying in {wait}s")
                time.sleep(wait)
        raise AssertionError("unreachable")


def run_one(session, client, system_prompt: str, question: str) -> dict:
    pre_verdict = check_request(question)
    if pre_verdict.blocked:
        return {"outcome": "refused", "reason": pre_verdict.reason, "stage": "pre_model"}

    hits = search(session, question, k=DEFAULT_K)
    contexts = group_by_document(apply_floor(hits, DEFAULT_FLOOR, query=question))

    retrieval = {
        "hits": len(hits),
        "above_floor": sum(len(c.chunks) for c in contexts),
        "best_score": round(hits[0].score, 4) if hits else None,
        "documents": [c.document.name for c in contexts],
    }

    # Gate 1 -- no generation call at all.
    if not contexts:
        return {"outcome": "not_in_corpus", "gate": 1, "retrieval": retrieval}

    try:
        result = synthesise(
            contexts,
            model_client=client,
            system_prompt=system_prompt,
            history=[],
            question=question,
        )
    except CitationError as exc:
        return {"outcome": "validation_failure", "kind": "citation", "error": str(exc),
                "retrieval": retrieval}
    except ModelResponseError as exc:
        return {"outcome": "validation_failure", "kind": "schema", "error": str(exc),
                "retrieval": retrieval}

    # Gate 2 -- every document read its passages and declined.
    if not result.answered:
        return {
            "outcome": "not_in_corpus",
            "gate": 2,
            "declined": [d.name for d in result.declined],
            "retrieval": retrieval,
        }

    for answer in result.document_answers:
        post_verdict = check_document_answer(answer.answer)
        if post_verdict.blocked:
            return {
                "outcome": "refused",
                "reason": post_verdict.reason,
                "stage": "post_model",
                # Kept for review, NOT for display: this is the text the gate discarded, and
                # reading it is how we tell "the gate was right" from "the gate over-fired".
                "blocked_answer": answer.answer,
                "retrieval": retrieval,
            }

    return {
        "outcome": "answer",
        "document_count": len(result.document_answers),
        "declined": [d.name for d in result.declined],
        "retrieval": retrieval,
        "document_answers": [
            {
                "document": a.document.name,
                "publisher": a.document.publisher,
                "year": a.document.year,
                "answer": a.answer,
                "claims": [
                    {
                        "claim": c.claim,
                        "chunk_key": _chunk_key_of(contexts, c.source.chunk_id),
                        "section": c.source.section_heading,
                        # The non-blocking citation_mismatch signal, recorded per claim so a
                        # reviewer knows which answers to read first.
                        "overlap": round(lexical_overlap(c.claim, c.source.quote), 3),
                    }
                    for c in a.claims
                ],
            }
            for a in result.document_answers
        ],
    }


def _chunk_key_of(contexts, chunk_id) -> str | None:
    """`fsanz-...:15` is checkable by hand in a run file; a UUID is not."""
    for context in contexts:
        for chunk in context.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk.chunk_key
    return None


def load_previous_run() -> dict | None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(RUNS_DIR.glob("*.json"))
    return json.loads(existing[-1].read_text()) if existing else None


def load_phase1_baseline() -> dict[str, str]:
    """The most recent Phase 1 run, so flips *across paths* are visible too.

    This is the comparison Phase 2.8 actually needs: not "did the RAG run change since last
    time" (there is no last time) but "what did inverting the prompt and adding retrieval do
    to each question's outcome".
    """
    runs = sorted((Path(__file__).resolve().parent / "runs").glob("*.json"))
    if not runs:
        return {}
    return {r["id"]: r["outcome"] for r in json.loads(runs[-1].read_text())["results"]}


def main() -> None:
    loaded = json.loads(QUESTIONS_PATH.read_text())
    questions = loaded["questions"] if isinstance(loaded, dict) else loaded
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    previous = load_previous_run()
    previous_by_id = {r["id"]: r for r in previous["results"]} if previous else {}
    phase1 = load_phase1_baseline()

    session = SessionLocal()
    client = _RetryingClient(GroqModelClient())
    corpus = document_refs(session)
    print(f"corpus: {len(corpus)} documents, k={DEFAULT_K}, floor={DEFAULT_FLOOR}")

    results = []
    mismatches: list[str] = []
    flips: list[str] = []
    path_flips: list[str] = []

    try:
        for q in questions:
            print(f"\n=== {q['id']} [{q['category']}]: {q['question']}")
            outcome = run_one(session, client, system_prompt, q["question"])
            results.append({**q, **outcome})

            if outcome["outcome"] == "answer":
                # The documents that *answered*, not the ones that cleared the floor -- those
                # are two different numbers whenever gate 2 declines one, which is most of the
                # time, and printing the second under the first count reads as a bug.
                answered = ", ".join(a["document"] for a in outcome["document_answers"])
                print(f"  answer from {outcome['document_count']} of "
                      f"{len(outcome['retrieval']['documents'])} document(s) above the floor: "
                      f"{answered}")
                for a in outcome["document_answers"]:
                    print(f"    [{a['publisher']}] {a['answer'][:110]}")
                    for c in a["claims"]:
                        print(f"       - ({c['chunk_key']}, overlap {c['overlap']}) {c['claim'][:90]}")
                if outcome["declined"]:
                    print(f"    declined: {', '.join(outcome['declined'])}")
                time.sleep(1.5)  # stay under the free-tier tokens-per-minute limit
            elif outcome["outcome"] == "not_in_corpus":
                detail = (f"best={outcome['retrieval']['best_score']}" if outcome["gate"] == 1
                          else f"declined by {', '.join(outcome['declined'])}")
                print(f"  not_in_corpus (gate {outcome['gate']}, {detail})")
                if outcome["gate"] == 2:
                    time.sleep(1.5)
            elif outcome["outcome"] == "refused":
                print(f"  refused ({outcome['stage']}, reason={outcome['reason']})")
            else:
                print(f"  validation_failure ({outcome['kind']}): {outcome['error'][:160]}")

            expected = q.get("expected_outcome_rag")
            if expected is not None and outcome["outcome"] != expected:
                msg = f"{q['id']}: expected {expected!r}, got {outcome['outcome']!r}"
                mismatches.append(msg)
                print(f"  MISMATCH: {msg}")

            prev = previous_by_id.get(q["id"])
            if prev is not None and prev["outcome"] != outcome["outcome"]:
                msg = f"{q['id']}: {prev['outcome']!r} -> {outcome['outcome']!r}"
                flips.append(msg)
                print(f"  OUTCOME FLIP vs previous RAG run: {msg}")

            if q["id"] in phase1 and phase1[q["id"]] != outcome["outcome"]:
                msg = f"{q['id']}: phase1={phase1[q['id']]!r} -> rag={outcome['outcome']!r}"
                path_flips.append(msg)
    finally:
        session.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_path = RUNS_DIR / f"{timestamp}.json"
    run_path.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "pipeline": "rag",
                "k": DEFAULT_K,
                "floor": DEFAULT_FLOOR,
                "corpus_documents": len(corpus),
                "results": results,
            },
            indent=2,
        )
    )

    by_outcome: dict[str, int] = {}
    for r in results:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1

    print(f"\n{'=' * 78}")
    print(f"RAG regression summary ({len(questions)} questions)")
    print(f"Run written to {run_path.relative_to(REPO_ROOT)}")
    print(f"Outcomes: {by_outcome}")
    print(f"\nExpectation mismatches: {len(mismatches)}")
    for m in mismatches:
        print(f"  - {m}")
    print(f"Flips vs previous RAG run: {len(flips)}"
          + (" (none -- this is the baseline)" if previous is None else ""))
    for f in flips:
        print(f"  - {f}")
    print(f"\nPath flips vs the Phase 1 run ({len(path_flips)}) "
          "-- EXPECTED, and each one must be explained in writing:")
    for f in path_flips:
        print(f"  - {f}")

    # Exit non-zero only on a mismatch against a *stated* expectation. Path flips are the
    # intended change, so failing on them would make the script cry wolf on its own purpose.
    if mismatches:
        sys.exit(1)


if __name__ == "__main__":
    main()
