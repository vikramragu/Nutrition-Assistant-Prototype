import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ClaimSchema(BaseModel):
    """A single claim within a structured model answer.

    `source` is forced to `None` for this milestone (problemStatement.md §3):
    it is typed as `Literal[None]` rather than `Optional[str]` so "must be
    null" is a schema-level guarantee, not a convention. Widening this to
    `Optional[SourceRef]` is the only change a future milestone needs here.
    """

    claim: str
    source: Literal[None] = None


class NutritionAnswer(BaseModel):
    """The structured-output contract the model must conform to (architecture.md §5)."""

    answer: str
    claims: list[ClaimSchema]


class ClaimRead(BaseModel):
    id: uuid.UUID
    claim_text: str
    source: str | None

    model_config = ConfigDict(from_attributes=True)


class MessageRead(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    claims: list[ClaimRead] = []

    model_config = ConfigDict(from_attributes=True)


class ConversationRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    messages: list[MessageRead] = []

    model_config = ConfigDict(from_attributes=True)
