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


# --- Phase 2.5: answer_from_document (architecture.md §7.2, §8.2) ---


class _RecordingCompletions:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def _recording_client(response):
    completions = _RecordingCompletions(response)
    groq = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GroqModelClient(client=groq), completions


_VALID_DOCUMENT_ANSWER = {
    "answers_question": True,
    "answer": "Free sugars should be under 10% of total energy intake.",
    "claims": [{"claim": "Free sugars should be under 10% of energy.", "chunk_id": "who:3"}],
}


def test_document_answer_parses_into_the_structured_contract():
    client, _ = _recording_client(_fake_response(content=json.dumps(_VALID_DOCUMENT_ANSWER)))

    result = client.answer_from_document("system", "document prompt", [], "How much sugar?")

    assert result.answers_question is True
    assert result.claims[0].chunk_id == "who:3"


def test_document_prompt_is_sent_as_a_second_system_message_after_the_system_prompt():
    """Order is the point: the document-specific task and its passages sit together, and
    the precedence this prompt claims over system_prompt.md is positional as well as
    stated. system_prompt.md still carries Phase 1's "no named sources" rule until 2.8."""
    client, completions = _recording_client(
        _fake_response(content=json.dumps(_VALID_DOCUMENT_ANSWER))
    )

    client.answer_from_document("SYSTEM", "DOCUMENT", [{"role": "user", "content": "earlier"}], "now")

    messages = completions.kwargs["messages"]
    assert [m["role"] for m in messages] == ["system", "system", "user", "user"]
    assert messages[0]["content"] == "SYSTEM"
    assert messages[1]["content"] == "DOCUMENT"
    assert messages[-1]["content"] == "now"


def test_document_answer_uses_its_own_response_schema_not_phase_one_s():
    """Sending the Phase 1 schema would ask for `source: null` on every claim -- the exact
    rule Phase 2 inverts -- and no chunk id would come back at all."""
    client, completions = _recording_client(
        _fake_response(content=json.dumps(_VALID_DOCUMENT_ANSWER))
    )

    client.answer_from_document("system", "document prompt", [], "How much sugar?")

    schema = completions.kwargs["response_format"]["json_schema"]
    assert schema["name"] == "document_answer"
    assert schema["schema"]["required"] == ["answers_question", "answer", "claims"]


def test_document_answer_missing_answers_question_is_a_validation_failure():
    """`answers_question` is gate 2 of the not-in-corpus refusal. A response without it
    cannot be read as either an answer or a decline, so it is not repairable."""
    response = _fake_response(content=json.dumps({"answer": "Some answer.", "claims": []}))
    client, _ = _recording_client(response)

    with pytest.raises(ModelResponseError):
        client.answer_from_document("system", "document prompt", [], "A question")


def test_document_answer_claim_without_a_chunk_id_is_a_validation_failure():
    """A claim with no citation is unshippable, and catching it at the schema keeps the
    citation validator's failures meaning "cited something it wasn't given"."""
    response = _fake_response(
        content=json.dumps(
            {"answers_question": True, "answer": "An answer.", "claims": [{"claim": "A claim."}]}
        )
    )
    client, _ = _recording_client(response)

    with pytest.raises(ModelResponseError):
        client.answer_from_document("system", "document prompt", [], "A question")
