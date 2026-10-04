import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScopeCategory = Literal["calorie_target", "weight_target", "medical_advice"]


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


class ConversationCreateResponse(BaseModel):
    id: uuid.UUID


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID
    message: str = Field(min_length=1)


class ChatAnswerResponse(BaseModel):
    type: Literal["answer"] = "answer"
    answer: str
    claims: list[ClaimSchema]


class ChatRefusedResponse(BaseModel):
    type: Literal["refused"] = "refused"
    reason: ScopeCategory
    message: str


class DocumentRef(BaseModel):
    """The minimum a citation needs to be checkable by hand (architecture.md §8.2).

    `year` is nullable because three corpus documents state no publication year, and the
    brief forbids inventing one.
    """

    id: uuid.UUID
    name: str
    publisher: str
    year: int | None
    url: str


class CorpusDocumentRead(DocumentRef):
    """One row of `GET /corpus` — what the assistant searched, and how current it is."""

    year_source: str
    retrieval_date: datetime
    page_last_updated: str | None
    chunk_count: int


class CorpusResponse(BaseModel):
    documents: list[CorpusDocumentRead]
    chunk_count: int
    embedding_model: str
