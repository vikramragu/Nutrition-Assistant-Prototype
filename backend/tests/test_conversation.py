import pytest
from pydantic import ValidationError

from db.models import Conversation
from db.schemas import ClaimSchema
from db.session import SessionLocal
from services.conversation import append_message, create_conversation, load_history


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_create_conversation_append_message_and_load_history(db):
    conversation = create_conversation(db)
    try:
        assert conversation.id is not None
        assert conversation.created_at is not None

        user_msg = append_message(db, conversation.id, "user", "How much protein do I need?")
        assert user_msg.role == "user"
        assert user_msg.conversation_id == conversation.id

        assistant_msg = append_message(
            db,
            conversation.id,
            "assistant",
            "About 0.8g of protein per kg of body weight for most adults.",
            claims=[ClaimSchema(claim="RDA for protein is 0.8g/kg body weight.")],
        )
        assert len(assistant_msg.claims) == 1
        assert assistant_msg.claims[0].claim_text == "RDA for protein is 0.8g/kg body weight."
        assert assistant_msg.claims[0].source is None

        loaded = load_history(db, conversation.id)
        assert loaded is not None
        assert len(loaded.messages) == 2
        assert loaded.messages[0].role == "user"
        assert loaded.messages[1].role == "assistant"
        assert loaded.messages[1].claims[0].source is None
    finally:
        # This conversation is not part of the model call/eval pipeline, so
        # cascading delete (conversations -> messages -> claims) cleans up
        # everything the test wrote, independent of any model call.
        db.query(Conversation).filter_by(id=conversation.id).delete()
        db.commit()


def test_load_history_returns_none_for_unknown_conversation(db):
    import uuid

    assert load_history(db, uuid.uuid4()) is None


def test_claim_schema_rejects_non_null_source():
    with pytest.raises(ValidationError):
        ClaimSchema(claim="Vitamin C prevents colds.", source="https://example.com/study")


def test_claim_schema_accepts_null_source():
    claim = ClaimSchema(claim="Bananas contain potassium.")
    assert claim.source is None
