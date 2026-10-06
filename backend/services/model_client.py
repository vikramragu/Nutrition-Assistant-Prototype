import json
import os
from typing import Literal, Protocol, TypedDict

from dotenv import load_dotenv
from groq import Groq
from pydantic import ValidationError

from db.schemas import DocumentAnswer, NutritionAnswer

load_dotenv()

DEFAULT_MODEL_ID = "openai/gpt-oss-120b"
MODEL_ID = os.environ.get("GROQ_MODEL", DEFAULT_MODEL_ID)
MAX_COMPLETION_TOKENS = 16000

# Groq's strict mode uses constrained decoding to guarantee the output matches
# this schema exactly -- every property listed in `required`, `additionalProperties: false`.
# See https://console.groq.com/docs/structured-outputs.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "nutrition_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The assistant's answer to the user's question.",
                },
                "claims": {
                    "type": "array",
                    "description": (
                        "Every specific factual claim made in `answer`, each as a standalone "
                        "statement. Empty if the answer makes no factual claims."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "source": {
                                "type": "null",
                                "description": "Always null -- sources are not supported yet.",
                            },
                        },
                        "required": ["claim", "source"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answer", "claims"],
            "additionalProperties": False,
        },
    },
}


# Phase 2.5, architecture.md §7.2 / §8.2. One of these per document, never one call
# holding two documents' chunks.
#
# The model emits `{claim, chunk_id}` and nothing else about the source. It never writes a
# publisher, a year or a URL, so it cannot fabricate one -- the backend expands the id by
# join. That is the §8.2 "a model cannot fabricate a citation it never writes" property.
#
# `chunk_id` is an unconstrained string rather than an `enum` of the ids actually in
# context. Constraining it would make a fabricated id *undecodable*, which is this
# project's preferred kind of guarantee -- but it would mean a per-call response schema
# whose support in Groq's strict mode is unverified, and it would move the guarantee into
# the vendor's decoder where a regression is invisible to us. The check in
# services/citation_validator.py is the guarantee instead: it runs in our code, it is
# tested, and it fails loudly. Revisit if strict-mode enums are confirmed.
DOCUMENT_ANSWER_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "document_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "answers_question": {
                    "type": "boolean",
                    "description": (
                        "True only if the supplied passages contain the information the "
                        "question asks for. False when they are merely on the same topic."
                    ),
                },
                "answer": {
                    "type": "string",
                    "description": (
                        "The answer drawn only from the supplied passages. Empty string "
                        "when answers_question is false."
                    ),
                },
                "claims": {
                    "type": "array",
                    "description": (
                        "Every specific factual statement in `answer`, each as a "
                        "standalone statement citing the passage it came from. Empty when "
                        "answers_question is false; never empty when `answer` is not."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "chunk_id": {
                                "type": "string",
                                "description": (
                                    "The chunk_id of the supplied passage this claim came "
                                    "from, copied exactly. Must be one of the ids given "
                                    "in this request."
                                ),
                            },
                        },
                        "required": ["claim", "chunk_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answers_question", "answer", "claims"],
            "additionalProperties": False,
        },
    },
}


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class ModelResponseError(Exception):
    """The model's response did not conform to the NutritionAnswer schema.

    Per problemStatement.md §3, this is a hard parsing/validation failure: callers
    must not attempt to extract or repair an answer from it, only surface the failure.
    """


class ModelClient(Protocol):
    def get_structured_answer(
        self, system_prompt: str, history: list[ChatMessage], user_message: str
    ) -> NutritionAnswer: ...

    def answer_from_document(
        self,
        system_prompt: str,
        document_prompt: str,
        history: list[ChatMessage],
        user_message: str,
    ) -> DocumentAnswer: ...


class GroqModelClient:
    """Structured-output ModelClient backed by the Groq API.

    The API key is resolved by the SDK from the environment (GROQ_API_KEY),
    never accepted as a constructor argument from request input.
    """

    def __init__(self, client: Groq | None = None) -> None:
        self._client = client or Groq()

    def get_structured_answer(
        self, system_prompt: str, history: list[ChatMessage], user_message: str
    ) -> NutritionAnswer:
        raw = self._complete(
            system_messages=[system_prompt],
            history=history,
            user_message=user_message,
            response_format=RESPONSE_FORMAT,
        )
        try:
            return NutritionAnswer.model_validate(raw)
        except ValidationError as exc:
            raise ModelResponseError(f"Model response failed schema validation: {exc}") from exc

    def answer_from_document(
        self,
        system_prompt: str,
        document_prompt: str,
        history: list[ChatMessage],
        user_message: str,
    ) -> DocumentAnswer:
        """Answer from one document's passages only (architecture.md §7.2).

        `document_prompt` arrives already rendered with that document's passages and their
        ids. Rendering happens in `services/answer_synthesiser.py`, which owns the
        grouping, rather than here: this client stays what Phase 1 made it -- transport
        plus schema validation -- and the one place that decides what a call may see is the
        one place that decides how to say it.

        It is sent as a **second system message**, after `system_prompt`, so the
        document-specific task and its passages sit together and the precedence between
        the two prompts is positional as well as stated.
        """
        raw = self._complete(
            system_messages=[system_prompt, document_prompt],
            history=history,
            user_message=user_message,
            response_format=DOCUMENT_ANSWER_RESPONSE_FORMAT,
        )
        try:
            return DocumentAnswer.model_validate(raw)
        except ValidationError as exc:
            raise ModelResponseError(
                f"Document answer failed schema validation: {exc}"
            ) from exc

    def _complete(
        self,
        *,
        system_messages: list[str],
        history: list[ChatMessage],
        user_message: str,
        response_format: dict,
    ) -> object:
        messages = [{"role": "system", "content": prompt} for prompt in system_messages]
        messages.extend({"role": m["role"], "content": m["content"]} for m in history)
        messages.append({"role": "user", "content": user_message})

        response = self._client.chat.completions.create(
            model=MODEL_ID,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            messages=messages,
            response_format=response_format,
        )

        choice = response.choices[0]
        content = choice.message.content
        if not content:
            raise ModelResponseError(
                f"Model returned no content (finish_reason={choice.finish_reason!r})"
            )

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelResponseError(f"Model response was not valid JSON: {exc}") from exc
