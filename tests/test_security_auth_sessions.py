import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from security.auth_passwords import hash_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, **delta):
        self.current += timedelta(**delta)


def _create_account(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_user(
            email="teacher.ahmed@school-a.edu",
            password_hash=hash_password("temporary-session-test-password"),
            role="teacher",
            tenant_id="school-a",
        )
        return store.get_user_by_email("teacher.ahmed@school-a.edu")
    finally:
        store.close()


def test_session_token_is_opaque_and_raw_token_is_not_persisted(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])

        assert isinstance(token, str)
        assert token
        assert token != user["email"]
    finally:
        service.close()

    database_contents = db_path.read_bytes()
    token_digest = sha256(token.encode("utf-8"))
    assert token.encode("utf-8") not in database_contents
    assert (
        token_digest.digest() in database_contents
        or token_digest.hexdigest().encode("ascii") in database_contents
    )


def test_valid_session_resolves_server_side_identity(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])

        identity = service.resolve_session(token)

        assert identity["user_id"] == user["id"]
        assert identity["email"] == "teacher.ahmed@school-a.edu"
        assert identity["role"] == "teacher"
        assert identity["tenant_id"] == "school-a"
    finally:
        service.close()


def test_disabled_user_invalidates_existing_session(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])
        assert service.resolve_session(token) is not None

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE users SET status = ? WHERE id = ?",
                ("disabled", user["id"]),
            )
            connection.commit()

        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_disabled_tenant_invalidates_existing_session(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])
        assert service.resolve_session(token) is not None

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE tenants SET status = ? WHERE id = ?",
                ("disabled", user["tenant_id"]),
            )
            connection.commit()

        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_role_change_invalidates_existing_session(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])
        identity = service.resolve_session(token)
        assert identity["role"] == "teacher"

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = ?",
                (user["id"],),
            )
            connection.commit()

        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_tenant_change_invalidates_existing_session(tmp_path):
    db_path = tmp_path / "security.db"
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_tenant(tenant_id="school-b", name="Future School")
        store.create_user(
            email="teacher.ahmed@school-a.edu",
            password_hash=hash_password("temporary-session-test-password"),
            role="teacher",
            tenant_id="school-a",
        )
        user = store.get_user_by_email("teacher.ahmed@school-a.edu")
    finally:
        store.close()

    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])
        identity = service.resolve_session(token)
        assert identity["tenant_id"] == "school-a"

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE users SET tenant_id = 'school-b' WHERE id = ?",
                (user["id"],),
            )
            connection.commit()

        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_unknown_session_token_is_rejected(tmp_path):
    db_path = tmp_path / "security.db"
    _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()

        assert service.resolve_session("unknown-random-bearer-token") is None
    finally:
        service.close()


def test_revoked_session_is_rejected(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        token = service.create_session(user["id"])
        assert service.resolve_session(token) is not None

        service.revoke_session(token)

        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_idle_timeout_is_enforced_server_side(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    clock = FakeClock()
    service = SessionService(db_path, clock=clock)
    try:
        service.initialize()
        token = service.create_session(user["id"])

        clock.advance(minutes=14)
        assert service.resolve_session(token) is not None

        clock.advance(minutes=16)
        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_absolute_timeout_is_enforced_even_with_activity(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    clock = FakeClock()
    service = SessionService(db_path, clock=clock)
    try:
        service.initialize()
        token = service.create_session(user["id"])

        for _ in range(34):
            clock.advance(minutes=14)
            assert service.resolve_session(token) is not None

        clock.advance(minutes=5)
        assert service.resolve_session(token) is None
    finally:
        service.close()


def test_revoke_all_for_user_invalidates_all_user_sessions(tmp_path):
    db_path = tmp_path / "security.db"
    user = _create_account(db_path)
    service = SessionService(db_path)
    try:
        service.initialize()
        first_token = service.create_session(user["id"])
        second_token = service.create_session(user["id"])
        assert service.resolve_session(first_token) is not None
        assert service.resolve_session(second_token) is not None

        service.revoke_all_for_user(user["id"])

        assert service.resolve_session(first_token) is None
        assert service.resolve_session(second_token) is None
    finally:
        service.close()
