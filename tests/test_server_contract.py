"""Phase 4 - agent server contract tests (no LLM, no network).

Uses Starlette's TestClient (ASGI, in-process) with a scripted FakeLLM injected
into app.state so every shape/error/session path is exercised without a model.
One real-provider integration test lives at the bottom (auto-skipped by
conftest when no provider is reachable).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.agent_server as agent_server
from api.agent_server import app, store
from agent.llm_client import LLMError
from security.auth_passwords import hash_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore

APP = TestClient(app)

HEADERS = {}
LEGACY_IDENTITY_HEADERS = {
    "X-Agent-School": "school-a",
    "X-Agent-Role": "teacher",
    "X-Agent-User": "teacher.ahmed@school-a.edu",
}
AUTHENTICATED_IDENTITY = {
    "school": "school-a",
    "role": "teacher",
    "user": "teacher.ahmed@school-a.edu",
}


class FakeLLM:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        self._message_count = []

    def chat(self, messages, tools=None, num_predict=512):
        self.calls.append(messages)
        self._message_count.append(len(messages))
        msg = self._script.pop(0) if self._script else {"role": "assistant", "content": "done"}
        return {"message": msg}, 0.01


def install_llm(script):
    fake = FakeLLM(script)
    app.state.agent_llm = fake
    return fake


def _create_authenticated_user(db_path, email):
    auth_store = SecurityStore(db_path)
    try:
        auth_store.initialize()
        if auth_store.get_tenant("school-a") is None:
            auth_store.create_tenant(tenant_id="school-a", name="Al Noor School")
        auth_store.create_user(
            email=email,
            password_hash=hash_password("temporary-server-contract-password"),
            role="teacher",
            tenant_id="school-a",
        )
        user = auth_store.get_user_by_email(email)
    finally:
        auth_store.close()

    sessions = SessionService(db_path)
    try:
        sessions.initialize()
        return sessions.create_session(user["id"])
    finally:
        sessions.close()


@pytest.fixture(autouse=True)
def reset(tmp_path, monkeypatch):
    store._store.clear()
    db_path = tmp_path / "security.db"
    token = _create_authenticated_user(
        db_path, "teacher.ahmed@school-a.edu"
    )
    monkeypatch.setattr(agent_server, "AUTH_DB_PATH", db_path)
    monkeypatch.setitem(HEADERS, "Authorization", f"Bearer {token}")
    yield
    app.state.agent_llm = None
    store._store.clear()


def _tool_msg(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


@pytest.fixture(scope="session")
def mock_up():
    """In-process mock API on 8097 so real tool execution happens."""
    import os
    import threading
    import time as _time

    import httpx
    import uvicorn

    from api.mock_api import app as mock_app

    config = uvicorn.Config(mock_app, host="127.0.0.1", port=8097, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    os.environ["MOCK_API_URL"] = "http://127.0.0.1:8097"
    deadline = _time.time() + 10
    while _time.time() < deadline:
        try:
            if httpx.get("http://127.0.0.1:8097/health", timeout=1).status_code == 200:
                break
        except Exception:
            _time.sleep(0.2)
    yield
    server.should_exit = True
    os.environ.pop("MOCK_API_URL", None)


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------

def test_health():
    r = APP.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_answered_no_session_id_given():
    install_llm([{"role": "assistant", "content": "There are 14 students in Grade 5."}])
    r = APP.post("/chat", json={"message": "How many students are in Grade 5?"}, headers=HEADERS)
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "answered"
    assert "14" in body["answer"]
    assert body["session_id"]
    assert body["error"] is None
    assert body["iterations"] >= 1


def test_chat_keeps_existing_session_id():
    install_llm([{"role": "assistant", "content": "first"}])
    sid = APP.post("/chat", json={"message": "hi"}, headers=HEADERS).json()["session_id"]
    install_llm([{"role": "assistant", "content": "second"}])
    r = APP.post("/chat", json={"message": "again", "session_id": sid}, headers=HEADERS)
    assert r.json()["session_id"] == sid


def test_history_grows_across_turns_in_session():
    fake = install_llm([{"role": "assistant", "content": "one"}, {"role": "assistant", "content": "two"}])
    sid = APP.post("/chat", json={"message": "first question"}, headers=HEADERS).json()["session_id"]
    APP.post("/chat", json={"message": "second question", "session_id": sid}, headers=HEADERS)
    assert len(fake.calls) == 2
    second_messages = fake.calls[1]
    contents = [m["content"] for m in second_messages if m["role"] == "user"]
    assert contents == ["first question", "second question"]


def test_chat_tool_chain_steps_recorded(mock_up):
    install_llm([
        _tool_msg("get_student", {"name": "Ahmed"}),
        _tool_msg("get_attendance", {"studentId": 1}),
        {"role": "assistant", "content": "Ahmed is present today."},
    ])
    r = APP.post("/chat", json={"message": "Is Ahmed absent today?"}, headers=HEADERS)
    body = r.json()
    assert body["status"] == "answered"
    tools = [s["tool"] for s in body["steps"]]
    assert tools == ["get_student", "get_attendance"]
    assert all(s["status"] == "ok" for s in body["steps"])
    assert all("http" in s and "params" in s for s in body["steps"])


# ---------------------------------------------------------------------------
# error / guard paths
# ---------------------------------------------------------------------------

def test_register_non_object_body_uses_registration_validation_detail():
    r = APP.post("/auth/register", json=[])

    assert r.status_code == 400
    assert r.json() == {
        "error": {
            "code": "invalid_params",
            "detail": "registration_failed",
        }
    }
    assert "message" not in r.text


def test_missing_school_header_does_not_override_authenticated_identity():
    fake = install_llm([{"role": "assistant", "content": "authenticated"}])
    headers = {**HEADERS, **LEGACY_IDENTITY_HEADERS}
    headers.pop("X-Agent-School")
    r = APP.post("/chat", json={"message": "hello"}, headers=headers)
    assert r.status_code == 200
    assert len(fake.calls) == 1
    assert store._store[r.json()["session_id"]]["identity"] == AUTHENTICATED_IDENTITY


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Agent-School", ""),
        ("X-Agent-School", "   "),
        ("X-Agent-Role", None),
        ("X-Agent-Role", ""),
        ("X-Agent-Role", "   "),
        ("X-Agent-User", None),
        ("X-Agent-User", ""),
        ("X-Agent-User", "   "),
    ],
    ids=[
        "school-empty",
        "school-whitespace",
        "role-missing",
        "role-empty",
        "role-whitespace",
        "user-missing",
        "user-empty",
        "user-whitespace",
    ],
)
def test_legacy_identity_header_does_not_override_authenticated_identity(header, value):
    fake = install_llm([{"role": "assistant", "content": "authenticated"}])
    headers = {**HEADERS, **LEGACY_IDENTITY_HEADERS}
    if value is None:
        headers.pop(header)
    else:
        headers[header] = value

    r = APP.post("/chat", json={"message": "hello"}, headers=headers)

    assert r.status_code == 200
    assert len(fake.calls) == 1
    assert store._store[r.json()["session_id"]]["identity"] == AUTHENTICATED_IDENTITY


def test_identity_mismatch_same_session_403(tmp_path):
    install_llm([{"role": "assistant", "content": "first"}])
    mona_token = _create_authenticated_user(
        tmp_path / "security.db", "teacher.mona@school-a.edu"
    )
    sid = APP.post(
        "/chat",
        json={"message": "hi"},
        headers={"Authorization": HEADERS["Authorization"]},
    ).json()["session_id"]
    r = APP.post(
        "/chat",
        json={"message": "hi again", "session_id": sid},
        headers={"Authorization": f"Bearer {mona_token}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "identity_mismatch"
    assert store._store[sid]["identity"] == AUTHENTICATED_IDENTITY


def test_empty_message_400():
    r = APP.post("/chat", json={"message": "   "}, headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_params"


def test_missing_message_400():
    r = APP.post("/chat", json={}, headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_params"


def test_message_too_long_400():
    r = APP.post("/chat", json={"message": "x" * 4001}, headers=HEADERS)
    assert r.status_code == 400


def test_not_json_body_400():
    r = APP.post("/chat", content=b"not json", headers={**HEADERS, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_rate_limited_surfaces_error_kind():
    def boom(messages, tools=None, num_predict=512):
        raise LLMError("rate limited", status=429)

    app.state.agent_llm = FakeLLM([])
    app.state.agent_llm.chat = boom
    r = APP.post("/chat", json={"message": "hi"}, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["error"]["kind"] == "rate_limited"


def test_bad_key_surfaces_auth_kind():
    def boom(messages, tools=None, num_predict=512):
        raise LLMError("bad key", status=401)

    app.state.agent_llm = FakeLLM([])
    app.state.agent_llm.chat = boom
    r = APP.post("/chat", json={"message": "hi"}, headers=HEADERS)
    assert r.json()["error"]["kind"] == "auth"


def test_unexpected_exception_becomes_500_without_stack(mock_up):
    from unittest import mock

    install_llm([{"role": "assistant", "content": "ignored"}])
    with mock.patch("api.agent_server.AgentLoop.run", side_effect=RuntimeError("boom")):
        r = APP.post("/chat", json={"message": "hi"}, headers=HEADERS)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "boom" not in r.text  # no stack/details leaked


def test_cors_preflight_allowed():
    r = APP.options("/chat", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# integration (real configured provider + mock API) - auto-skipped when offline
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.slow
def test_live_chat_with_real_provider(monkeypatch):
    import threading
    import time as _time

    import httpx
    import uvicorn

    # Pin the provider so a bad key left by other tests can't leak into this one
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)

    from api.mock_api import app as mock_app

    config = uvicorn.Config(mock_app, host="127.0.0.1", port=8098, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = _time.time() + 10
    while _time.time() < deadline:
        try:
            if httpx.get("http://127.0.0.1:8098/health", timeout=1).status_code == 200:
                break
        except Exception:
            _time.sleep(0.2)
    try:
        monkeypatch.setenv("MOCK_API_URL", "http://127.0.0.1:8098")
        body = None
        for attempt in range(2):  # provider may be busy from other integration tests
            r = APP.post("/chat", json={"message": "How many students are in Grade 5?"}, headers=HEADERS)
            assert r.status_code == 200
            body = r.json()
            if body["status"] == "answered":
                break
            _time.sleep(3)
        assert body is not None and body["status"] == "answered", body
        assert "14" in body["answer"], body
        assert any(s["tool"] == "get_students" for s in body["steps"])
    finally:
        server.should_exit = True
        monkeypatch.undo()
