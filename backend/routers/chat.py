"""`POST /chat` — the full architecture.md §10 request sequence.

Three response types, and which one you get is decided by gates in this order:

```
check_request            -> refused        (policy, before anything is embedded)
retrieve / apply_floor   -> not_in_corpus  (gate 1: nothing cleared the floor)
synthesise + validate    -> 502            (schema or citation failure)
every document declines  -> not_in_corpus  (gate 2: the model read it and said no)
check_document_answer    -> refused        (policy, post-model, incl. personalisation)
                         -> answer
```

**Two properties are load-bearing and easy to break by moving a line:**

1. **The pre-model gate runs before retrieval.** An out-of-scope question never costs an
   embedding call and never pulls a calorie figure out of a guidance document. The corpus
   *does* contain calorie figures, so a calorie-target question that reached retrieval
   would be a question the corpus could answer -- this gate is what makes "the code is the
   guarantee" true rather than dependent on what happens to be indexed.
2. **Nothing is persisted until every gate has passed.** One commit, at the end. There is
   no state in which a user message exists without its answer.
"""

import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import ScopeRefusal
from db.schemas import (
    ChatAnswerResponse,
    ChatNotInCorpusResponse,
    ChatRefusedResponse,
    ChatRequest,
    ConversationCreateResponse,
    ConversationRead,
    ScopeCategory,
)
from db.session import get_db
from services.answer_synthesiser import group_by_document, synthesise
from services.citation_validator import CitationError
from services.conversation import (
    RetrievalEvidence,
    create_conversation,
    load_history,
    model_history,
    persist_coverage_refusal,
    persist_turn,
    to_conversation_read,
)
from services.corpus_catalog import NOT_IN_CORPUS_MESSAGE, document_refs
from services.model_client import GroqModelClient, ModelClient, ModelResponseError
from services.retriever import RETRIEVAL_FLOOR, RETRIEVAL_K, ScoredChunk, apply_floor, search
from services.scope_guard import REFUSAL_MESSAGES, check_document_answer, check_request

logger = logging.getLogger(__name__)

router = APIRouter()

_SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.md").read_text()

SearchFn = Callable[..., list[ScoredChunk]]


def get_model_client() -> ModelClient:
    return GroqModelClient()


def get_searcher() -> SearchFn:
    """The retrieval seam.

    Injected rather than called directly so a test can prove the pre-model gate runs
    *before* retrieval by spying on this, rather than by reading the code and trusting the
    order. It also keeps the 279 MB embedding model out of the test process entirely.
    """
    return search


def _persist_refusal(
    db: Session,
    conversation_id: uuid.UUID,
    message_text: str,
    category: ScopeCategory,
    stage: str,
) -> None:
    db.add(
        ScopeRefusal(
            conversation_id=conversation_id,
            message_text=message_text,
            category=category,
            stage=stage,
        )
    )
    db.commit()


@router.post("/conversations", response_model=ConversationCreateResponse, status_code=201)
def create_conversation_endpoint(db: Session = Depends(get_db)) -> ConversationCreateResponse:
    conversation = create_conversation(db)
    return ConversationCreateResponse(id=conversation.id)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db)) -> ConversationRead:
    conversation = load_history(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return to_conversation_read(conversation)


@router.post("/chat")
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    model_client: ModelClient = Depends(get_model_client),
    searcher: SearchFn = Depends(get_searcher),
) -> ChatAnswerResponse | ChatRefusedResponse | ChatNotInCorpusResponse:
    conversation = load_history(db, request.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Gate: policy, pre-model. Deliberately the first thing after the 404 and before any
    # embedding or retrieval call -- see this module's docstring, property 1.
    pre_verdict = check_request(request.message)
    if pre_verdict.blocked:
        _persist_refusal(db, request.conversation_id, request.message, pre_verdict.reason, "pre_model")
        return ChatRefusedResponse(
            reason=pre_verdict.reason, message=REFUSAL_MESSAGES[pre_verdict.reason]
        )

    # The full corpus, or the one filtered document -- never only the documents that scored
    # well. A user cannot judge the gap from a list that omits the misses (§7.3).
    searched = document_refs(db, document_id=request.document_filter)

    # `RETRIEVAL_K` / `RETRIEVAL_FLOOR` default to the Phase 2.4 measured values and are
    # overridable on Railway (architecture.md §12.1). An override is logged loudly at import
    # -- the floor is the refusal, not a tuning knob.
    hits = searcher(db, request.message, k=RETRIEVAL_K, document_id=request.document_filter)
    contexts = group_by_document(apply_floor(hits, RETRIEVAL_FLOOR, query=request.message))

    evidence = RetrievalEvidence(
        query_text=request.message,
        k=RETRIEVAL_K,
        floor=RETRIEVAL_FLOOR,
        document_filter=request.document_filter,
        hits=hits,
        used_chunk_ids=(
            frozenset().union(*(c.chunk_ids for c in contexts)) if contexts else frozenset()
        ),
    )

    # Gate 1: nothing cleared the floor. No generation call is made at all.
    if not contexts:
        persist_coverage_refusal(db, request.conversation_id, evidence)
        return ChatNotInCorpusResponse(message=NOT_IN_CORPUS_MESSAGE, searched=searched)

    try:
        result = synthesise(
            contexts,
            model_client=model_client,
            system_prompt=_SYSTEM_PROMPT,
            history=model_history(conversation),
            question=request.message,
        )
    except CitationError as exc:
        # A claim cited a chunk its call was never given, or prose shipped with no claims.
        # Not repairable: dropping the claim leaves an uncited assertion, and rewriting the
        # citation means the backend choosing a source for a claim it did not write.
        logger.error(
            "citation_validation_failure",
            extra={"failure_type": "citation_error", "description": str(exc)},
        )
        raise HTTPException(status_code=502, detail="Answer failed citation validation") from exc
    except ModelResponseError as exc:
        logger.error(
            "model_response_validation_failure",
            extra={"failure_type": "model_response_error", "description": str(exc)},
        )
        raise HTTPException(
            status_code=502, detail="Model response failed schema validation"
        ) from exc

    # Gate 2: every document read its own passages and said they do not answer this. The
    # stronger of the two gates since the quarantine narrowed the floor's separation band
    # to 0.02 -- retrieval-calibration.md §4.
    if not result.answered:
        persist_coverage_refusal(db, request.conversation_id, evidence)
        return ChatNotInCorpusResponse(message=NOT_IN_CORPUS_MESSAGE, searched=searched)

    # Gate: policy, post-model, now including personalisation (architecture.md §9.1). One
    # tripped answer discards the **whole** turn, every document's answer with it: a
    # blocked answer must never be a bypass path, and shipping the rest of a turn that
    # produced a prohibited answer leaks the context that made it prohibited.
    for answer in result.document_answers:
        post_verdict = check_document_answer(answer.answer)
        if post_verdict.blocked:
            _persist_refusal(
                db, request.conversation_id, request.message, post_verdict.reason, "post_model"
            )
            return ChatRefusedResponse(
                reason=post_verdict.reason, message=REFUSAL_MESSAGES[post_verdict.reason]
            )

    # Every gate has passed. One commit: user message, one assistant message per document,
    # every claim with its citation, and the retrieval evidence.
    persist_turn(
        db,
        request.conversation_id,
        user_message=request.message,
        document_answers=result.document_answers,
        evidence=evidence,
    )

    return ChatAnswerResponse(document_answers=result.document_answers, searched=searched)
