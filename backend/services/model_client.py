from typing import Literal, Protocol, TypedDict

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from db.schemas import NutritionAnswer

load_dotenv()

MODEL_ID = "claude-opus-5"
MAX_TOKENS = 16000

SUBMIT_ANSWER_TOOL_NAME = "submit_answer"

SUBMIT_ANSWER_TOOL = {
    "name": SUBMIT_ANSWER_TOOL_NAME,
    "description": (
        "Submit the structured nutrition-assistant answer. This is the only way to respond -- "
        "always call this tool, never respond with plain text."
    ),
    "strict": True,
    "input_schema": {
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


class AnthropicModelClient:
    """Forced tool-use ModelClient backed by the Anthropic API.

    The API key is resolved by the SDK from the environment (ANTHROPIC_API_KEY),
    never accepted as a constructor argument from request input.
    """

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()

    def get_structured_answer(
        self, system_prompt: str, history: list[ChatMessage], user_message: str
    ) -> NutritionAnswer:
        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": user_message})

        response = self._client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[SUBMIT_ANSWER_TOOL],
            tool_choice={"type": "tool", "name": SUBMIT_ANSWER_TOOL_NAME},
            messages=messages,
        )

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise ModelResponseError(f"Model refused to respond (category={category!r})")

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            raise ModelResponseError(
                f"Model response contained no tool_use block (stop_reason={response.stop_reason!r})"
            )

        raw_input = tool_use_blocks[0].input
        try:
            return NutritionAnswer.model_validate(raw_input)
        except ValidationError as exc:
            raise ModelResponseError(f"Model response failed schema validation: {exc}") from exc
