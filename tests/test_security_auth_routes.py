import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from security.auth_passwords import hash_password
from security.auth_routes import create_auth_router
from security.auth_store import SecurityStore


EMAIL = "teacher.ahmed@school-a.edu"
PASSWORD = "temporary-http-auth-test-password"


def _seed_active_account(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_user(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            role="teacher",
            tenant_id="school-a",
        )
    finally:
        store.close()


def _create_client(db_path):
    app = FastAPI()
    app.include_router(create_auth_router(db_path))
    return TestClient(app)


def _login(client):
    response = client.post(
        "/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_http_valid_login_returns_bearer_token(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)

    response = client.post(
        "/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["access_token"], str)
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_http_login_failures_are_publicly_indistinguishable(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)
    unknown_email = "unknown@school-a.edu"

    responses = [
        client.post(
            "/auth/login",
            json={"email": EMAIL, "password": "incorrect-password"},
        ),
        client.post(
            "/auth/login",
            json={"email": unknown_email, "password": PASSWORD},
        ),
    ]

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE users SET status = ? WHERE email = ?",
            ("disabled", EMAIL),
        )
        connection.commit()
    responses.append(
        client.post(
            "/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE users SET status = ? WHERE email = ?",
            ("active", EMAIL),
        )
        connection.execute(
            "UPDATE tenants SET status = ? WHERE id = ?",
            ("disabled", "school-a"),
        )
        connection.commit()
    responses.append(
        client.post(
            "/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
    )

    bodies = [response.json() for response in responses]
    assert all(response.status_code == 401 for response in responses)
    assert all(body == bodies[0] for body in bodies)

    public_body = json.dumps(bodies[0]).casefold()
    for forbidden in (
        EMAIL.casefold(),
        unknown_email.casefold(),
        "school-a",
        "teacher",
        "tenant",
        "role",
        "disabled",
        "password",
    ):
        assert forbidden not in public_body


def test_http_email_rate_limit_returns_429(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)

    for _ in range(5):
        response = client.post(
            "/auth/login",
            json={"email": EMAIL, "password": "incorrect-password"},
        )
        assert response.status_code == 401

    response = client.post(
        "/auth/login",
        json={"email": EMAIL, "password": "incorrect-password"},
    )

    assert response.status_code == 429


def test_http_ip_rate_limit_returns_429(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)

    for attempt in range(30):
        response = client.post(
            "/auth/login",
            json={
                "email": f"unknown-{attempt}@school-a.edu",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 401

    response = client.post(
        "/auth/login",
        json={"email": "attempt-31@school-a.edu", "password": PASSWORD},
    )

    assert response.status_code == 429


def test_http_me_returns_server_side_identity(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)
    token = _login(client)

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["role"] == "teacher"
    assert body["tenant_id"] == "school-a"


def test_http_me_rejects_invalid_bearer(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)

    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer fake-unknown-token"},
    )

    assert response.status_code == 401
    public_body = json.dumps(response.json()).casefold()
    for failure_reason in ("unknown", "revoked", "expired"):
        assert failure_reason not in public_body


def test_http_me_ignores_client_identity_headers(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)
    token = _login(client)

    response = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Agent-User": "attacker@example.com",
            "X-Agent-Role": "admin",
            "X-Agent-School": "school-b",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["role"] == "teacher"
    assert body["tenant_id"] == "school-a"


def test_http_logout_revokes_current_session(tmp_path):
    db_path = tmp_path / "security.db"
    _seed_active_account(db_path)
    client = _create_client(db_path)
    token = _login(client)
    authorization = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=authorization).status_code == 200

    logout_response = client.post("/auth/logout", headers=authorization)

    assert logout_response.status_code == 200
    assert client.get("/auth/me", headers=authorization).status_code == 401
