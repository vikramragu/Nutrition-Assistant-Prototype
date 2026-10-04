"""Tests for corpus quarantine (corpus.yaml `quarantine`, chunk._quarantine_reason).

Quarantine exists for one failure: a table whose row/column structure PDF extraction
destroys, leaving numbers adjacent to labels they may not belong to. The passage reads
as ordinary prose, retrieves normally, and invites a confidently wrong figure under a
citation that looks sound.

The property these tests protect is not "the right chunks are excluded" -- that is a
human judgement recorded in corpus.yaml with reasons. It is that the mechanism cannot
fail *silently*: a rule that stops matching must raise, because the alternative is a
manifest that claims a passage is excluded while it sits in the index.
"""

from __future__ import annotations

import pytest
import yaml

from corpus.chunk import _quarantine_reason, verify_quarantine_rules
from corpus.models import ManifestEntry

CALORIE_LABELS = "Active Child Teenager Adult Adult Inactive Teenager Adult Adult"
CALORIE_VALUES = "Active 2000kcal Inactive 1800kcal Active 2500kcal Inactive 2000kcal"

RULES = [
    {"match": "Active Child Teenager Adult Adult Inactive", "reason": "label row"},
    {"match": "Active 2000kcal Inactive 1800kcal", "reason": "value row"},
]


def _entry(**kw) -> ManifestEntry:
    defaults = dict(
        id="doc",
        name="A Document",
        publisher="A Publisher",
        year=2016,
        year_source="pdf_creation_date",
        source_url="https://example.org/d.pdf",
        media_type="application/pdf",
    )
    return ManifestEntry(**{**defaults, **kw})


def test_matching_passage_is_excluded_with_its_reason():
    assert _quarantine_reason(CALORIE_LABELS, RULES) == "label row"


def test_both_halves_of_a_split_table_are_caught():
    """Excluding only one half made things worse, not better: the orphaned values
    re-chunked under the heading "Average daily calorie needs ... for adults", which
    reads as authoritative, and scored *higher* than before the exclusion."""
    assert _quarantine_reason(CALORIE_VALUES, RULES) == "value row"


def test_unrelated_text_is_kept():
    text = "Drink at least 8 cups of fluid a day - water is best."
    assert _quarantine_reason(text, RULES) is None


def test_no_rules_keeps_everything():
    assert _quarantine_reason(CALORIE_LABELS, []) is None


def test_matching_ignores_whitespace_differences():
    """Rules are matched on normalised text so line wrapping cannot defeat them."""
    wrapped = "Active Child\n  Teenager   Adult\tAdult Inactive Teenager Adult Adult"
    assert _quarantine_reason(wrapped, RULES) == "label row"


def test_stale_rule_raises_rather_than_passing_quietly():
    """The dangerous case: the passage moved, the rule matches nothing, the manifest
    still claims an exclusion, and the text is back in the index unannounced."""
    entry = _entry(quarantine=[{"match": "text that is not there", "reason": "x"}])

    with pytest.raises(ValueError, match="matches nothing"):
        verify_quarantine_rules(entry, [], "some entirely different document text")


def test_live_rule_passes_verification():
    entry = _entry(quarantine=RULES)
    verify_quarantine_rules(entry, [], f"preamble {CALORIE_LABELS} and {CALORIE_VALUES} tail")


# --------------------------------------------------------------- the real manifest


def _manifest() -> list[dict]:
    from corpus.ingest import CORPUS_DIR  # noqa: PLC0415

    return yaml.safe_load((CORPUS_DIR / "corpus.yaml").read_text())


def test_every_manifest_rule_states_a_reason():
    """A quarantine entry without a reason is an unexplained deletion from the corpus."""
    for document in _manifest():
        for rule in document.get("quarantine", []):
            assert rule.get("reason"), f"{document['id']}: rule {rule['match']!r} has no reason"


def test_quarantined_text_is_absent_from_the_committed_snapshot():
    """End-to-end: whatever the manifest excludes must not be in the shipped corpus."""
    import gzip  # noqa: PLC0415
    import json  # noqa: PLC0415

    from corpus.ingest import CORPUS_DIR  # noqa: PLC0415

    path = CORPUS_DIR / "corpus_snapshot.jsonl.gz"
    if not path.exists():
        pytest.skip("snapshot not built")

    with gzip.open(path, "rt") as handle:
        rows = [json.loads(line) for line in handle][1:]
    corpus_text = {r["document_id"]: [] for r in rows}
    for row in rows:
        corpus_text[row["document_id"]].append(" ".join(row["text"].split()))

    for document in _manifest():
        for rule in document.get("quarantine", []):
            needle = " ".join(rule["match"].split())
            for text in corpus_text.get(document["id"], []):
                assert needle not in text, (
                    f"{document['id']}: quarantined passage {needle!r} is in the snapshot"
                )
