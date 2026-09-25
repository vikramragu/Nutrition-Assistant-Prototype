import json
import os
from typing import Literal, Protocol, TypedDict

from dotenv import load_dotenv
from groq import Groq
from pydantic import ValidationError

from db.schemas import NutritionAnswer

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
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": m["role"], "content": m["content"]} for m in history)
        messages.append({"role": "user", "content": user_message})

        response = self._client.chat.completions.create(
            model=MODEL_ID,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            messages=messages,
            response_format=RESPONSE_FORMAT,
        )

        choice = response.choices[0]
        content = choice.message.content
        if not content:
            raise ModelResponseError(
                f"Model returned no content (finish_reason={choice.finish_reason!r})"
            )

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelResponseError(f"Model response was not valid JSON: {exc}") from exc

        try:
            return NutritionAnswer.model_validate(raw)
        except ValidationError as exc:
            raise ModelResponseError(f"Model response failed schema validation: {exc}") from exc
