import pytest
from fastapi.testclient import TestClient

import api.agent_server as agent_server
from api.agent_server import app, store
from security.auth_passwords import hash_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore


APP = TestClient(app)
IDENTITY_HEADERS = {
    "X-Agent-User": "teacher.ahmed@school-a.edu",
    "X-Agent-Role": "teacher",
    "X-Agent-School": "school-a",
}
AUTHENTICATED_IDENTITY = {
    "school": "school-a",
    "role": "teacher",
    "user": "teacher.ahmed@school-a.edu",
}


class RecordingLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None, num_predict=512):
        self.calls.append(messages)
        return {"message": {"role": "assistant", "content": "unexpected"}}, 0.0


def _install_recording_llm():
    llm = RecordingLLM()
    app.state.agent_llm = llm
    return llm


def _create_bearer_token(db_path):
    auth_store = SecurityStore(db_path)
    try:
        auth_store.initialize()
        auth_store.create_tenant(tenant_id="school-a", name="Al Noor School")
        auth_store.create_user(
            email="teacher.ahmed@school-a.edu",
            password_hash=hash_password("temporary-bridge-test-password"),
            role="teacher",
            tenant_id="school-a",
        )
        user = auth_store.get_user_by_email("teacher.ahmed@school-a.edu")
    finally:
        auth_store.close()

    sessions = SessionService(db_path)
    try:
        sessions.initialize()
        return sessions.create_session(user["id"])
    finally:
        sessions.close()


def _chat_state():
    if len(store._store) != 1:
        return {
            "session_count": len(store._store),
            "identity": None,
            "history_turns": 0,
        }
    session = next(iter(store._store.values()))
    return {
        "session_count": 1,
        "identity": session["identity"],
        "history_turns": len(session["history"]) // 2,
    }


@pytest.fixture(autouse=True)
def reset_agent_server_state():
    app.state.agent_llm = None
    store._store.clear()
    yield
    app.state.agent_llm = None
    store._store.clear()


def test_chat_without_authenticated_session_is_rejected():
    llm = _install_recording_llm()

    response = APP.post("/chat", json={"message": "hello"})

    assert {
        "status": response.status_code,
        "llm_calls": len(llm.calls),
        **_chat_state(),
    } == {
        "status": 401,
        "llm_calls": 0,
        "session_count": 0,
        "identity": None,
        "history_turns": 0,
    }


def test_client_identity_headers_do_not_authenticate_caller():
    llm = _install_recording_llm()

    response = APP.post("/chat", json={"message": "hello"}, headers=IDENTITY_HEADERS)

    assert {
        "status": response.status_code,
        "llm_calls": len(llm.calls),
        **_chat_state(),
    } == {
        "status": 401,
        "llm_calls": 0,
        "session_count": 0,
        "identity": None,
        "history_turns": 0,
    }


def test_valid_bearer_session_allows_chat(tmp_path, monkeypatch):
    db_path = tmp_path / "security.db"
    token = _create_bearer_token(db_path)
    monkeypatch.setattr(agent_server, "AUTH_DB_PATH", db_path, raising=False)
    llm = _install_recording_llm()

    response = APP.post(
        "/chat",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert {
        "status": response.status_code,
        "has_session_id": bool(response.json().get("session_id")),
        "llm_calls": len(llm.calls),
        **_chat_state(),
    } == {
        "status": 200,
        "has_session_id": True,
        "llm_calls": 1,
        "session_count": 1,
        "identity": AUTHENTICATED_IDENTITY,
        "history_turns": 1,
    }


def test_valid_bearer_identity_overrides_forged_client_headers(tmp_path, monkeypatch):
    db_path = tmp_path / "security.db"
    token = _create_bearer_token(db_path)
    monkeypatch.setattr(agent_server, "AUTH_DB_PATH", db_path, raising=False)
    llm = _install_recording_llm()
    authorization = {"Authorization": f"Bearer {token}"}

    first_response = APP.post(
        "/chat",
        json={"message": "first"},
        headers=authorization,
    )
    first_session_id = first_response.json().get("session_id")
    second_response = APP.post(
        "/chat",
        json={
            "message": "second",
            "session_id": first_session_id or "current-server-fallback-session",
        },
        headers={
            **authorization,
            "X-Agent-User": "attacker@example.com",
            "X-Agent-Role": "admin",
            "X-Agent-School": "school-b",
        },
    )

    assert {
        "first_status": first_response.status_code,
        "second_status": second_response.status_code,
        "same_session": (
            first_session_id is not None
            and second_response.json().get("session_id") == first_session_id
        ),
        "llm_calls": len(llm.calls),
        **_chat_state(),
    } == {
        "first_status": 200,
        "second_status": 200,
        "same_session": True,
        "llm_calls": 2,
        "session_count": 1,
        "identity": AUTHENTICATED_IDENTITY,
        "history_turns": 2,
    }


def test_auth_login_endpoint_exists():
    response = APP.post(
        "/auth/login",
        json={"email": "nobody@example.invalid", "password": "invalid-password"},
    )

    assert response.status_code == 401
    public_error = response.text.lower()
    assert "auth" in public_error or "credential" in public_error


def test_public_auth_error_does_not_disclose_account_state():
    response = APP.post(
        "/auth/login",
        json={"email": "nobody@example.invalid", "password": "invalid-password"},
    )

    assert response.status_code == 401
    public_error = response.text.lower()
    for disclosure in (
        "email exists",
        "email does not exist",
        "unknown email",
        "tenant exists",
        "tenant does not exist",
        "unknown tenant",
        "wrong password",
        "incorrect password",
        "account disabled",
    ):
        assert disclosure not in public_error
