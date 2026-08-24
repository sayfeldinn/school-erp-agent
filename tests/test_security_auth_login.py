import sqlite3

import security.auth_login as auth_login
from security.auth_passwords import hash_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore


EMAIL = "teacher.ahmed@school-a.edu"
PASSWORD = "temporary-login-test-password"


def _create_active_account(db_path):
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
        user = store.get_user_by_email(EMAIL)
    finally:
        store.close()

    sessions = SessionService(db_path)
    try:
        sessions.initialize()
    finally:
        sessions.close()

    return user


def test_valid_credentials_return_session_token(tmp_path):
    db_path = tmp_path / "security.db"
    _create_active_account(db_path)
    service = auth_login.LoginService(db_path)

    token = service.authenticate("Teacher.Ahmed@School-A.edu", PASSWORD)

    assert isinstance(token, str)
    assert token

    sessions = SessionService(db_path)
    try:
        identity = sessions.resolve_session(token)
    finally:
        sessions.close()

    assert identity["email"] == EMAIL
    assert identity["role"] == "teacher"
    assert identity["tenant_id"] == "school-a"


def test_wrong_password_is_rejected(tmp_path):
    db_path = tmp_path / "security.db"
    _create_active_account(db_path)
    service = auth_login.LoginService(db_path)

    assert service.authenticate(EMAIL, "incorrect-password") is None


def test_unknown_email_is_rejected(tmp_path):
    db_path = tmp_path / "security.db"
    _create_active_account(db_path)
    service = auth_login.LoginService(db_path)

    assert service.authenticate("unknown@school-a.edu", PASSWORD) is None


def test_unknown_email_still_performs_password_verification(tmp_path, monkeypatch):
    db_path = tmp_path / "security.db"
    user = _create_active_account(db_path)
    verification_calls = []

    def track_verification(password, encoded_hash):
        verification_calls.append((password, encoded_hash))
        return False

    monkeypatch.setattr(auth_login, "verify_password", track_verification)
    service = auth_login.LoginService(db_path)
    attempted_password = "caller-controlled-unknown-account-password"

    result = service.authenticate("unknown@school-a.edu", attempted_password)

    assert result is None
    assert len(verification_calls) == 1
    verified_password, dummy_hash = verification_calls[0]
    assert verified_password == attempted_password
    assert isinstance(dummy_hash, str)
    assert dummy_hash.startswith("$argon2id$")
    assert dummy_hash != attempted_password
    assert dummy_hash != user["password_hash"]


def test_disabled_user_cannot_login(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_active_account(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE users SET status = ? WHERE id = ?",
            ("disabled", user["id"]),
        )
        connection.commit()

    service = auth_login.LoginService(db_path)

    assert service.authenticate(EMAIL, PASSWORD) is None


def test_disabled_tenant_cannot_login(tmp_path):
    db_path = tmp_path / "security.db"
    _create_active_account(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE tenants SET status = ? WHERE id = ?",
            ("disabled", "school-a"),
        )
        connection.commit()

    service = auth_login.LoginService(db_path)

    assert service.authenticate(EMAIL, PASSWORD) is None
