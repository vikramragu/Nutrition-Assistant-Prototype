"""Phase 2.4 retrieval calibration (implementation-plan.md Phase 2.4).

Answers two questions, and only these two:

1. **Does retrieval find the right passages?** recall@k over hand-labelled questions.
2. **Where should the relevance floor sit?** The floor *is* the not-in-corpus refusal
   (architecture.md §7.3), so it has to separate "the corpus answers this" from "it does
   not" on measured data rather than on a guess.

No generation call, no Groq credits. One embedding per question, reused across the whole
sweep -- the retriever's `search()` deliberately applies no floor so a 40-value sweep
costs 26 queries, not 1,040.

Usage (from the repo root, with the corpus seeded):
    backend/.venv/bin/python eval/run_retrieval_eval.py
    backend/.venv/bin/python eval/run_retrieval_eval.py --k 5 --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from services.embeddings import get_embedding_client  # noqa: E402
from services.retriever import search  # noqa: E402

SET_PATH = Path(__file__).resolve().parent / "retrieval_set.json"
K_VALUES = (1, 3, 5, 8, 10)
MAX_K = max(K_VALUES)
FLOOR_GRID = [round(0.30 + 0.01 * i, 2) for i in range(46)]  # 0.30 .. 0.75


def _assert_labels_resolve(session, questions: list[dict]) -> None:
    """Every labelled chunk_key must exist in the database. Refuse to measure otherwise.

    `chunk_key` is `slug:ordinal`, so ordinals shift whenever a document gains or loses
    a chunk. On 2026-10-05 quarantining two passages renumbered every later chunk in the
    Irish document, and six labels silently began pointing at the wrong text. Recall fell
    0.975 -> 0.800 and looked exactly like a real regression.

    A dangling label can only ever understate recall, which is the direction that wastes
    the most time, so this is a hard failure rather than a warning.
    """
    from db.models import Chunk  # noqa: PLC0415 -- keeps the import local to the check

    labelled = {key for q in questions for key in q.get("relevant", [])}
    if not labelled:
        return
    known = set(session.scalars(select(Chunk.chunk_key).where(Chunk.chunk_key.in_(labelled))))
    missing = sorted(labelled - known)
    if missing:
        raise SystemExit(
            "Labelled chunks no longer exist:\n  "
            + "\n  ".join(missing)
            + "\n\nThe corpus was re-chunked and ordinals shifted. Re-map these against "
            "the current corpus (`python -m corpus.show --doc <slug>`) rather than "
            "deleting them -- the questions are still valid."
        )


def _recall_at_k(hits: list[str], relevant: set[str], k: int) -> float:
    """Fraction of labelled chunks found in the top k.

    Recall rather than precision: the answer layer reads every chunk it is given and can
    decline (`answers_question`), so a spare retrieved chunk is cheap. A *missing* one is
    unrecoverable -- nothing downstream can cite a passage retrieval never returned.
    """
    if not relevant:
        return float("nan")
    return len(set(hits[:k]) & relevant) / len(relevant)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure retrieval and calibrate the floor.")
    ap.add_argument("--set", type=Path, default=SET_PATH)
    ap.add_argument("--k", type=int, default=MAX_K, help="k used for the floor sweep")
    ap.add_argument("--json", type=Path, help="write the full result as JSON")
    args = ap.parse_args(argv)

    payload = json.loads(args.set.read_text())
    questions = payload["questions"]

    client = get_embedding_client()  # loaded once, outside the timing loop
    results = []

    with SessionLocal() as session:
        _assert_labels_resolve(session, questions)
        for question in questions:
            started = time.perf_counter()
            hits = search(session, question["question"], k=MAX_K, client=client)
            elapsed_ms = (time.perf_counter() - started) * 1000
            results.append(
                {
                    "id": question["id"],
                    "question": question["question"],
                    "kind": question["kind"],
                    "relevant": question.get("relevant", []),
                    "hits": [{"chunk_key": h.chunk_key, "score": round(h.score, 4)} for h in hits],
                    "latency_ms": round(elapsed_ms, 1),
                }
            )

    in_corpus = [r for r in results if r["kind"] == "in_corpus"]
    out_corpus = [r for r in results if r["kind"] == "out_of_corpus"]

    # ---------------------------------------------------------------- recall@k
    print("=" * 78)
    print("RECALL@K  (in-corpus questions only)")
    print("=" * 78)
    recall_table = {}
    for k in K_VALUES:
        scores = [
            _recall_at_k([h["chunk_key"] for h in r["hits"]], set(r["relevant"]), k)
            for r in in_corpus
        ]
        recall_table[k] = statistics.mean(scores)
        full = sum(1 for s in scores if s == 1.0)
        print(f"  k={k:<3} recall={recall_table[k]:.3f}   all labels found: {full}/{len(scores)}")

    # ---------------------------------------------------------------- per question
    print()
    print("=" * 78)
    print(f"PER QUESTION  (top-1 score, recall@{args.k})")
    print("=" * 78)
    for r in in_corpus:
        keys = [h["chunk_key"] for h in r["hits"]]
        rec = _recall_at_k(keys, set(r["relevant"]), args.k)
        top = r["hits"][0] if r["hits"] else None
        flag = "   " if rec == 1.0 else ("<- " if rec > 0 else "<<<")
        print(
            f"{flag}{r['id']}  recall={rec:.2f}  top={top['score']:.3f} "
            f"{top['chunk_key']:<40} {r['question'][:44]}"
        )
        if rec < 1.0:
            missed = sorted(set(r["relevant"]) - set(keys[: args.k]))
            print(f"      missed: {', '.join(missed)}")

    # ---------------------------------------------------------------- separation
    print()
    print("=" * 78)
    print("SEPARATION  (what the floor has to divide)")
    print("=" * 78)
    best_in = [r["hits"][0]["score"] for r in in_corpus if r["hits"]]
    best_out = [r["hits"][0]["score"] for r in out_corpus if r["hits"]]
    print(f"  in-corpus  top-1: min={min(best_in):.3f} median={statistics.median(best_in):.3f} "
          f"max={max(best_in):.3f}")
    print(f"  out-corpus top-1: min={min(best_out):.3f} median={statistics.median(best_out):.3f} "
          f"max={max(best_out):.3f}")
    print(f"  gap between worst in-corpus and best out-of-corpus: "
          f"{min(best_in) - max(best_out):+.3f}")
    print()
    for r in sorted(out_corpus, key=lambda r: -r["hits"][0]["score"] if r["hits"] else 0):
        if r["hits"]:
            print(f"    {r['hits'][0]['score']:.3f}  {r['id']}  {r['question'][:52]}")
            print(f"           drawn to: {r['hits'][0]['chunk_key']}")

    # ---------------------------------------------------------------- floor sweep
    print()
    print("=" * 78)
    print(f"FLOOR SWEEP  (k={args.k})")
    print("=" * 78)
    print("  floor | in-corpus kept | out-corpus refused | recall of labels kept")
    sweep = []
    for floor in FLOOR_GRID:
        kept = sum(
            1 for r in in_corpus if r["hits"] and r["hits"][0]["score"] >= floor
        )
        refused = sum(
            1 for r in out_corpus if not r["hits"] or r["hits"][0]["score"] < floor
        )
        label_recall = statistics.mean(
            [
                _recall_at_k(
                    [h["chunk_key"] for h in r["hits"] if h["score"] >= floor],
                    set(r["relevant"]),
                    args.k,
                )
                for r in in_corpus
            ]
        )
        sweep.append(
            {
                "floor": floor,
                "in_corpus_kept": kept,
                "out_corpus_refused": refused,
                "label_recall": round(label_recall, 4),
            }
        )
        if round(floor * 100) % 5 == 0:
            print(
                f"  {floor:>5.2f} | {kept:>8}/{len(in_corpus):<5} | "
                f"{refused:>11}/{len(out_corpus):<6} | {label_recall:.3f}"
            )

    # The widest floor that still answers every in-corpus question is the safe ceiling;
    # the lowest that refuses every out-of-corpus question is the safe base.
    perfect = [s for s in sweep if s["in_corpus_kept"] == len(in_corpus)
               and s["out_corpus_refused"] == len(out_corpus)]
    print()
    if perfect:
        lo, hi = perfect[0]["floor"], perfect[-1]["floor"]
        print(f"  Floors separating the set perfectly: {lo:.2f} .. {hi:.2f}")
        print(f"  Midpoint (maximum margin either way): {(lo + hi) / 2:.3f}")
    else:
        print("  No floor separates the set perfectly -- the classes overlap.")
        print("  Pick from the table above against the cost of each error direction.")

    latencies = [r["latency_ms"] for r in results]
    print()
    print(f"  query latency: median {statistics.median(latencies):.1f} ms, "
          f"max {max(latencies):.1f} ms  (embedding + exact scan, {len(questions)} queries)")

    if args.json:
        args.json.write_text(
            json.dumps(
                {"results": results, "recall": recall_table, "sweep": sweep}, indent=2
            )
        )
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
