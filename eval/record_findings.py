"""Records reviewed findings against an EvalRun (eval.md §3.4 step 4).

Findings are a manual-review output -- this script just persists them as
eval_findings rows once a human (or, for this milestone, the assisting reviewer)
has read the raw output and classified it against the five failure types.

Usage (from repo root):
    backend/.venv/bin/python eval/record_findings.py <run_id> <findings.json>

findings.json: a list of {question_id, failure_type, description} objects.
failure_type must be one of: unsupported_claim, inconsistent_number,
unverifiable_source, missed_scope_restriction, unhelpful_hedging.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from db.models import EvalFinding  # noqa: E402
from db.session import SessionLocal  # noqa: E402

VALID_TYPES = {
    "unsupported_claim",
    "inconsistent_number",
    "unverifiable_source",
    "missed_scope_restriction",
    "unhelpful_hedging",
}


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    run_id, findings_path = sys.argv[1], Path(sys.argv[2])
    findings = json.loads(findings_path.read_text())

    for f in findings:
        if f["failure_type"] not in VALID_TYPES:
            raise ValueError(f"Invalid failure_type: {f['failure_type']!r}")

    db = SessionLocal()
    for f in findings:
        db.add(
            EvalFinding(
                run_id=run_id,
                question_id=f["question_id"],
                failure_type=f["failure_type"],
                description=f["description"],
            )
        )
    db.commit()
    db.close()

    print(f"Recorded {len(findings)} findings against run {run_id}.")


if __name__ == "__main__":
    main()
