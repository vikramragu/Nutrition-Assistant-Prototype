"""Manual smoke test for Phase 2 (implementation-plan.md).

Calls the real Anthropic API with a handful of nutrition questions and confirms
the structured-output round trip works end to end. Costs real API credits --
run manually, not part of the automated test suite.

Usage (from backend/, with ANTHROPIC_API_KEY set in the environment or .env):
    .venv/bin/python scripts/smoke_test_model_client.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.model_client import AnthropicModelClient, ModelResponseError  # noqa: E402

QUESTIONS = [
    "How much protein does an adult need daily?",
    "How long can cooked chicken sit out at room temperature?",
    "What internal temperature is safe for ground beef?",
    "Is intermittent fasting good for you?",
]


def main() -> None:
    system_prompt = (Path(__file__).resolve().parents[1] / "prompts" / "system_prompt.md").read_text()
    client = AnthropicModelClient()

    failures = 0
    for question in QUESTIONS:
        print(f"\n=== {question}")
        try:
            result = client.get_structured_answer(system_prompt, [], question)
        except ModelResponseError as exc:
            failures += 1
            print(f"VALIDATION FAILURE: {exc}")
            continue

        print(f"answer: {result.answer}")
        print(f"claims ({len(result.claims)}):")
        for claim in result.claims:
            assert claim.source is None, "schema guarantee violated: non-null source reached this point"
            print(f"  - {claim.claim}  [source={claim.source!r}]")

    print(f"\n{len(QUESTIONS) - failures}/{len(QUESTIONS)} questions round-tripped successfully.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
