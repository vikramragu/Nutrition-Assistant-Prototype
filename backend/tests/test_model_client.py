from types import SimpleNamespace

import pytest

from services.model_client import AnthropicModelClient, ModelResponseError


def _fake_response(*, stop_reason="tool_use", content=None, stop_details=None):
    return SimpleNamespace(stop_reason=stop_reason, content=content or [], stop_details=stop_details)


def _tool_use_block(input_):
    return SimpleNamespace(type="tool_use", input=input_)


class _FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _client_returning(response) -> AnthropicModelClient:
    return AnthropicModelClient(client=_FakeAnthropicClient(response))


def test_valid_model_output_parses_into_nutrition_answer():
    response = _fake_response(
        content=[
            _tool_use_block(
                {
                    "answer": "Adults typically need about 0.8g of protein per kg of body weight.",
                    "claims": [
                        {"claim": "RDA for protein is 0.8g/kg body weight.", "source": None}
                    ],
                }
            )
        ]
    )
    client = _client_returning(response)

    result = client.get_structured_answer("system prompt", [], "How much protein do I need?")

    assert result.answer.startswith("Adults typically need")
    assert len(result.claims) == 1
    assert result.claims[0].claim == "RDA for protein is 0.8g/kg body weight."
    assert result.claims[0].source is None


def test_missing_required_field_is_rejected_as_validation_failure():
    # Missing `claims` entirely -- schema-nonconformant.
    response = _fake_response(content=[_tool_use_block({"answer": "Some answer."})])
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_non_null_source_is_rejected_as_validation_failure():
    # The model fabricated a source -- must be a hard failure, never silently coerced to null.
    response = _fake_response(
        content=[
            _tool_use_block(
                {
                    "answer": "Some answer.",
                    "claims": [
                        {"claim": "A claim.", "source": "https://example.com/fabricated"}
                    ],
                }
            )
        ]
    )
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_no_tool_use_block_is_rejected_as_validation_failure():
    # e.g. the model responded with plain text instead of calling the tool.
    response = _fake_response(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="I'll just answer in prose instead.")],
    )
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_refusal_stop_reason_is_rejected_as_validation_failure():
    response = _fake_response(
        stop_reason="refusal",
        content=[],
        stop_details=SimpleNamespace(category="frontier_llm"),
    )
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")
