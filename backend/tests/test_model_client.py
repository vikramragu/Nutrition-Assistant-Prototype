import json
from types import SimpleNamespace

import pytest

from services.model_client import GroqModelClient, ModelResponseError


def _fake_response(*, content, finish_reason="stop"):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeChat:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeGroqClient:
    def __init__(self, response):
        self.chat = _FakeChat(response)


def _client_returning(response) -> GroqModelClient:
    return GroqModelClient(client=_FakeGroqClient(response))


def test_valid_model_output_parses_into_nutrition_answer():
    response = _fake_response(
        content=json.dumps(
            {
                "answer": "Adults typically need about 0.8g of protein per kg of body weight.",
                "claims": [
                    {"claim": "RDA for protein is 0.8g/kg body weight.", "source": None}
                ],
            }
        )
    )
    client = _client_returning(response)

    result = client.get_structured_answer("system prompt", [], "How much protein do I need?")

    assert result.answer.startswith("Adults typically need")
    assert len(result.claims) == 1
    assert result.claims[0].claim == "RDA for protein is 0.8g/kg body weight."
    assert result.claims[0].source is None


def test_missing_required_field_is_rejected_as_validation_failure():
    # Missing `claims` entirely -- schema-nonconformant.
    response = _fake_response(content=json.dumps({"answer": "Some answer."}))
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_non_null_source_is_rejected_as_validation_failure():
    # The model fabricated a source -- must be a hard failure, never silently coerced to null.
    response = _fake_response(
        content=json.dumps(
            {
                "answer": "Some answer.",
                "claims": [{"claim": "A claim.", "source": "https://example.com/fabricated"}],
            }
        )
    )
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_invalid_json_is_rejected_as_validation_failure():
    response = _fake_response(content="not valid json at all {")
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")


def test_empty_content_is_rejected_as_validation_failure():
    # e.g. the model hit max_tokens before producing any content.
    response = _fake_response(content=None, finish_reason="length")
    client = _client_returning(response)

    with pytest.raises(ModelResponseError):
        client.get_structured_answer("system prompt", [], "A question")
