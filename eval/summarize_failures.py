"""Groups and counts eval_findings by failure_type (eval.md §3.4 step 5).

Prints a markdown-ready table plus the underlying findings, so the output can be
pasted straight into docs/failure-log.md.

Usage (from repo root):
    backend/.venv/bin/python eval/summarize_failures.py [run_id]

If run_id is omitted, summarizes the most recent EvalRun.
"""

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from db.models import EvalFinding, EvalRun  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from sqlalchemy import select  # noqa: E402

FAILURE_TYPES = [
    "unsupported_claim",
    "inconsistent_number",
    "unverifiable_source",
    "missed_scope_restriction",
    "unhelpful_hedging",
]


def main() -> None:
    db = SessionLocal()

    if len(sys.argv) > 1:
        run_id = sys.argv[1]
    else:
        run = db.execute(select(EvalRun).order_by(EvalRun.run_at.desc())).scalars().first()
        if run is None:
            print("No EvalRun found.")
            return
        run_id = str(run.id)

    findings = (
        db.execute(select(EvalFinding).where(EvalFinding.run_id == run_id).order_by(EvalFinding.question_id))
        .scalars()
        .all()
    )
    db.close()

    counts = Counter(f.failure_type for f in findings)

    print(f"## Findings summary -- run {run_id}\n")
    print("| Failure type | Count |")
    print("|---|---|")
    for ft in FAILURE_TYPES:
        print(f"| {ft} | {counts.get(ft, 0)} |")
    print(f"| **Total** | **{len(findings)}** |")

    print("\n## Findings detail\n")
    print("| Question | Failure type | Description |")
    print("|---|---|---|")
    for f in findings:
        print(f"| q{f.question_id} | {f.failure_type} | {f.description} |")


if __name__ == "__main__":
    main()
