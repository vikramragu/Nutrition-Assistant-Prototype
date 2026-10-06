"""Request, response and model-facing contracts.

Ordered by dependency, in four groups: shared primitives, what the model emits, what the
API returns, and what the database reads back. `DocumentRef` sits at the top because
almost everything below cites it.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# `personalised_guidance` is new in Phase 2.6 (architecture.md §9.1). Retrieval is what
# makes it a live risk: the corpus states population-level quantities, restating them is
# correct, and converting one into an instruction for the person asking is not.
ScopeCategory = Literal[
    "calorie_target", "weight_target", "medical_advice", "personalised_guidance"
]


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


# --- What the model emits ---------------------------------------------------------------


class ClaimSchema(BaseModel):
    """A single claim within a Phase 1 structured model answer.

    `source` is forced to `None` (problemStatement.md §3): it is typed as `Literal[None]`
    rather than `Optional[str]` so "must be null" is a schema-level guarantee, not a
    convention. Phase 2 does not widen this -- it replaces it with `ModelCitedClaim`,
    which requires a chunk id instead. Both shapes exist because the Phase 1 eval
    harnesses still run against the Phase 1 path.
    """

    claim: str
    source: Literal[None] = None


class NutritionAnswer(BaseModel):
    """The Phase 1 structured-output contract (architecture.md §5).

    Still used by `eval/run_regression.py` and `eval/run_failure_log.py`, which are the
    recorded baseline Phase 2.8 compares its outcome-type flips against.
    """

    answer: str
    claims: list[ClaimSchema]


class ModelCitedClaim(BaseModel):
    """One claim as the *model* writes it: the statement, and which chunk it came from.

    Deliberately **not** a full `Citation`. The model never writes a publisher, a year or
    a URL, so it cannot fabricate one — the backend expands `chunk_id` into the full
    citation from the chunk it already retrieved.

    `chunk_id` is a plain `str`, not `uuid.UUID`, on purpose. Whether the id is real is
    `services/citation_validator.py`'s question, not Pydantic's: typing it as a UUID here
    would turn a fabricated id into a schema error, which reports as "the model broke the
    contract" when the actual finding is "the model cited something it was never given".
    Both end in a 502, but only one of them is diagnostic.
    """

    claim: str
    chunk_id: str


class DocumentAnswer(BaseModel):
    """The structured output of one per-document generation call (architecture.md §7.2).

    `answers_question` is a schema-forced field so that "this document has nothing to say"
    is a first-class output rather than something inferred from a similarity score. It is
    gate 2 of the not-in-corpus refusal, and since the Phase 2.4 quarantine narrowed the
    floor's separation band to 0.02 it is the *stronger* of the two gates.
    """

    answers_question: bool
    answer: str
    claims: list[ModelCitedClaim]


# --- Citations (architecture.md §8.2) ---------------------------------------------------


class Citation(BaseModel):
    """A claim's source, expanded by the backend from a chunk id the model chose.

    `page_from`/`page_to` are additive to architecture.md §8.2's field list: without them
    a citation into the 23-page FSANZ document names a section but not where to look.
    Page `0` means *not paginated* — the HTML source — not page one.
    """

    chunk_id: uuid.UUID
    document: DocumentRef
    section_heading: str | None
    page_from: int | None
    page_to: int | None
    quote: str  # the chunk text backing this claim, verbatim


class CitedClaim(BaseModel):
    """`source` is required and non-nullable — the inverse of Phase 1's `Literal[None]`.

    Phase 1 made "always null" a type-level guarantee; this makes "always cited" one. Same
    technique, pointed the other way (architecture.md §8.2).
    """

    claim: str
    source: Citation


class DocumentAnswerOut(BaseModel):
    document: DocumentRef
    answer: str
    claims: list[CitedClaim]


# --- Requests ---------------------------------------------------------------------------


class ConversationCreateResponse(BaseModel):
    id: uuid.UUID


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID
    message: str = Field(min_length=1)
    # The brief's second retrieval mode: scope the question to one named document
    # (architecture.md §10.1). When set, it is also what the coverage refusal reports as
    # having been searched -- a filtered question that finds nothing has genuinely only
    # searched that one document.
    document_filter: uuid.UUID | None = None


# --- Chat responses: three distinct wire types (architecture.md §9.2) -------------------


class ChatAnswerResponse(BaseModel):
    """One block per document, never merged (architecture.md §7.2, §8.1).

    A single top-level `answer` string cannot represent "two documents, answered
    separately" without blending them or picking a winner, and the brief forbids both. The
    shape has to change; this is that change.
    """

    type: Literal["answer"] = "answer"
    document_answers: list[DocumentAnswerOut]
    searched: list[DocumentRef]


class ChatRefusedResponse(BaseModel):
    """The **policy** refusal: the assistant will not. Persisted to `scope_refusals`."""

    type: Literal["refused"] = "refused"
    reason: ScopeCategory
    message: str


class ChatNotInCorpusResponse(BaseModel):
    """The **coverage** refusal: the corpus does not. Persisted to `retrievals`.

    A separate discriminated type rather than a `reason` code on `refused`, because the two
    mean opposite things and the brief requires a user to tell them apart without parsing
    prose.

    `searched` is always the **full** corpus (or the single filtered document), never just
    the documents that scored above the floor: a user cannot judge the gap from a list that
    silently omits everything that scored zero.
    """

    type: Literal["not_in_corpus"] = "not_in_corpus"
    message: str
    searched: list[DocumentRef]


class ChatAnswerResponseV1(BaseModel):
    """Phase 1's flat answer contract — one answer, uncited claims.

    Superseded by `ChatAnswerResponse` as of Phase 2.6, which is when `POST /chat` stopped
    returning it. Kept because the Phase 1 eval harnesses still describe their results in
    this shape, and because architecture.md §8.1 is a note about a contract that changed —
    which is easier to read with both shapes in front of you.
    """

    type: Literal["answer"] = "answer"
    answer: str
    claims: list[ClaimSchema]


# --- Read models: history and corpus ----------------------------------------------------


class ClaimRead(BaseModel):
    id: uuid.UUID
    claim_text: str
    # Nullable on the *read* path only, and only for Phase 1 rows, which have no
    # `chunk_id`. On the write path `CitedClaim.source` is required: nothing created from
    # Phase 2 onward can be uncited (architecture.md §6.1).
    source: Citation | None = None
    # The Phase 1 text column, always null. Kept visible so a legacy row reads as "no
    # citation" rather than silently losing a field.
    legacy_source: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MessageRead(BaseModel):
    """One stored message. For an assistant message, **one document's** answer.

    `document` is derived from the message's claims rather than stored on the row -- see
    `db.models.Message.content` for why. It is null for user messages and for Phase 1
    assistant rows, which have no citations to derive it from.
    """

    id: uuid.UUID
    role: str
    content: str
    ordinal: int
    created_at: datetime
    document: DocumentRef | None = None
    claims: list[ClaimRead] = []

    model_config = ConfigDict(from_attributes=True)


class ConversationRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    messages: list[MessageRead] = []

    model_config = ConfigDict(from_attributes=True)


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
