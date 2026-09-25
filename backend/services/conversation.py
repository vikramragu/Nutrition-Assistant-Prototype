import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db.models import Claim, Conversation, Message
from db.schemas import ClaimSchema


def create_conversation(db: Session) -> Conversation:
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def append_message(
    db: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    claims: list[ClaimSchema] | None = None,
) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    if claims:
        message.claims = [Claim(claim_text=c.claim, source=c.source) for c in claims]
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def load_history(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages).selectinload(Message.claims))
    )
    return db.scalars(stmt).first()
