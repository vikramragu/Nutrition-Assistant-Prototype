import uuid

import pytest
from fastapi.testclient import TestClient

from db.models import Conversation
from db.schemas import NutritionAnswer
from db.session import SessionLocal
from main import app
from routers.chat import get_model_client
from services.model_client import ModelResponseError


class _FakeModelClient:
    def __init__(self, *, answer: NutritionAnswer | None = None, error: Exception | None = None):
        self._answer = answer
        self._error = error
        self.called = False

    def get_structured_answer(self, system_prompt, history, user_message):
        self.called = True
        if self._error is not None:
            raise self._error
        return self._answer


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def conversation_id(db):
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    cid = conversation.id
    yield cid
    db.query(Conversation).filter_by(id=cid).delete()
    db.commit()


@pytest.fixture()
def client():
    return TestClient(app)


def test_post_conversations_creates_a_conversation(client, db):
    response = client.post("/conversations")
    assert response.status_code == 201
    conv_id = uuid.UUID(response.json()["id"])
    try:
        assert db.get(Conversation, conv_id) is not None
    finally:
        db.query(Conversation).filter_by(id=conv_id).delete()
        db.commit()


def test_chat_returns_404_for_unknown_conversation(client):
    fake = _FakeModelClient()
    app.dependency_overrides[get_model_client] = lambda: fake
    response = client.post(
        "/chat", json={"conversation_id": str(uuid.uuid4()), "message": "How much protein do I need?"}
    )
    assert response.status_code == 404
    assert fake.called is False


def test_chat_returns_answer_for_in_scope_question(client, conversation_id):
    fake = _FakeModelClient(
        answer=NutritionAnswer(
            answer="Most adults need about 0.8g of protein per kg of body weight.",
            claims=[{"claim": "RDA for protein is 0.8g/kg body weight.", "source": None}],
        )
    )
    app.dependency_overrides[get_model_client] = lambda: fake

    response = client.post(
        "/chat", json={"conversation_id": str(conversation_id), "message": "How much protein do I need?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "answer"
    assert body["answer"].startswith("Most adults need")
    assert len(body["claims"]) == 1
    assert body["claims"][0]["source"] is None
    assert fake.called is True


def test_chat_returns_refused_without_calling_model_for_out_of_scope_question(client, conversation_id):
    fake = _FakeModelClient()  # would raise/return None if ever called
    app.dependency_overrides[get_model_client] = lambda: fake

    response = client.post(
        "/chat",
        json={
            "conversation_id": str(conversation_id),
            "message": "How many calories should I eat to lose 10 pounds?",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "refused"
    assert body["reason"] == "calorie_target"
    assert "registered dietitian" in body["message"] or "physician" in body["message"]
    # Pre-model gate: the model must never have been called.
    assert fake.called is False


def test_chat_refuses_response_that_volunteers_a_calorie_target(client, conversation_id):
    fake = _FakeModelClient(
        answer=NutritionAnswer(
            answer="You should eat about 1800 calories a day to reach your goals.",
            claims=[],
        )
    )
    app.dependency_overrides[get_model_client] = lambda: fake

    response = client.post(
        "/chat", json={"conversation_id": str(conversation_id), "message": "What's a healthy diet look like?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "refused"
    assert body["reason"] == "calorie_target"
    # The model *was* called (this is the post-model gate), but its answer must never leak out.
    assert fake.called is True
    assert "1800" not in response.text


def test_chat_returns_502_on_validation_failure_and_does_not_persist(client, conversation_id):
    fake = _FakeModelClient(error=ModelResponseError("Model response failed schema validation"))
    app.dependency_overrides[get_model_client] = lambda: fake

    response = client.post(
        "/chat", json={"conversation_id": str(conversation_id), "message": "How much protein do I need?"}
    )

    assert response.status_code == 502

    history = client.get(f"/conversations/{conversation_id}")
    assert history.status_code == 200
    assert history.json()["messages"] == []


def test_get_conversation_returns_full_history_after_reload(client, conversation_id):
    fake = _FakeModelClient(
        answer=NutritionAnswer(
            answer="Cooked chicken should not sit out for more than two hours.",
            claims=[{"claim": "Cooked chicken should not sit out for more than two hours.", "source": None}],
        )
    )
    app.dependency_overrides[get_model_client] = lambda: fake

    chat_response = client.post(
        "/chat",
        json={
            "conversation_id": str(conversation_id),
            "message": "How long can cooked chicken sit out?",
        },
    )
    assert chat_response.status_code == 200

    history_response = client.get(f"/conversations/{conversation_id}")
    assert history_response.status_code == 200
    body = history_response.json()
    assert body["id"] == str(conversation_id)
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "How long can cooked chicken sit out?"
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["claims"][0]["source"] is None


def test_get_conversation_404_for_unknown_conversation(client):
    response = client.get(f"/conversations/{uuid.uuid4()}")
    assert response.status_code == 404
