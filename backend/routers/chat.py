import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import ScopeRefusal
from db.schemas import (
    ChatAnswerResponse,
    ChatRefusedResponse,
    ChatRequest,
    ConversationCreateResponse,
    ConversationRead,
    ScopeCategory,
)
from db.session import get_db
from services.conversation import append_message, create_conversation, load_history
from services.model_client import ChatMessage, GroqModelClient, ModelClient, ModelResponseError
from services.scope_guard import REFUSAL_MESSAGES, check_request, check_response

logger = logging.getLogger(__name__)

router = APIRouter()

_SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.md").read_text()


def get_model_client() -> ModelClient:
    return GroqModelClient()


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
    return ConversationRead.model_validate(conversation)


@router.post("/chat")
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    model_client: ModelClient = Depends(get_model_client),
) -> ChatAnswerResponse | ChatRefusedResponse:
    conversation = load_history(db, request.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Cheap, pre-model gate (architecture.md §8.1) -- runs before any model call.
    pre_verdict = check_request(request.message)
    if pre_verdict.blocked:
        _persist_refusal(db, request.conversation_id, request.message, pre_verdict.reason, "pre_model")
        return ChatRefusedResponse(reason=pre_verdict.reason, message=REFUSAL_MESSAGES[pre_verdict.reason])

    history: list[ChatMessage] = [
        {"role": m.role, "content": m.content} for m in conversation.messages
    ]

    try:
        answer = model_client.get_structured_answer(_SYSTEM_PROMPT, history, request.message)
    except ModelResponseError as exc:
        # Full DB-backed eval logging lands in Phase 7/8; for now this is a hard
        # failure surfaced as a 502, never patched around with prose extraction.
        logger.error(
            "model_response_validation_failure",
            extra={"failure_type": "model_response_error", "description": str(exc)},
        )
        raise HTTPException(status_code=502, detail="Model response failed schema validation") from exc

    # Post-model gate: catches a model that volunteers a prohibited category
    # unprompted. The answer is discarded, never returned -- it's never a bypass path.
    post_verdict = check_response(answer)
    if post_verdict.blocked:
        _persist_refusal(db, request.conversation_id, request.message, post_verdict.reason, "post_model")
        return ChatRefusedResponse(reason=post_verdict.reason, message=REFUSAL_MESSAGES[post_verdict.reason])

    append_message(db, request.conversation_id, "user", request.message)
    append_message(db, request.conversation_id, "assistant", answer.answer, claims=answer.claims)

    return ChatAnswerResponse(answer=answer.answer, claims=answer.claims)
