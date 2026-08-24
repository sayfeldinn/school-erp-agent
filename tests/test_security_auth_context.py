import security.auth_context as auth_context
from security.auth_passwords import hash_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore


EXPECTED_IDENTITY = {
    "school": "school-a",
    "role": "teacher",
    "user": "teacher.ahmed@school-a.edu",
}


def _create_bearer_token(db_path):
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_user(
            email="teacher.ahmed@school-a.edu",
            password_hash=hash_password("temporary-auth-context-test-password"),
            role="teacher",
            tenant_id="school-a",
        )
        user = store.get_user_by_email("teacher.ahmed@school-a.edu")
    finally:
        store.close()

    sessions = SessionService(db_path)
    try:
        sessions.initialize()
        return sessions.create_session(user["id"])
    finally:
        sessions.close()


def test_missing_authorization_returns_none(tmp_path):
    db_path = tmp_path / "security.db"

    assert auth_context.resolve_authenticated_identity(None, db_path) is None


def test_malformed_or_non_bearer_authorization_returns_none(tmp_path):
    db_path = tmp_path / "security.db"
    malformed_values = (
        "",
        "   ",
        "Basic abc",
        "Bearer",
        "Bearer ",
        "Bearer token extra",
        "one two three",
    )

    for authorization in malformed_values:
        assert (
            auth_context.resolve_authenticated_identity(authorization, db_path)
            is None
        )


def test_bearer_scheme_is_case_insensitive(tmp_path):
    db_path = tmp_path / "security.db"
    token = _create_bearer_token(db_path)

    identity = auth_context.resolve_authenticated_identity(
        f"bEaReR {token}", db_path
    )

    assert identity == EXPECTED_IDENTITY


def test_unknown_or_revoked_bearer_returns_none(tmp_path):
    db_path = tmp_path / "security.db"
    token = _create_bearer_token(db_path)

    assert (
        auth_context.resolve_authenticated_identity(
            "Bearer fake-unknown-token", db_path
        )
        is None
    )

    sessions = SessionService(db_path)
    try:
        sessions.revoke_session(token)
    finally:
        sessions.close()

    assert (
        auth_context.resolve_authenticated_identity(f"Bearer {token}", db_path)
        is None
    )


def test_valid_bearer_maps_server_side_identity_to_agent_shape(tmp_path):
    db_path = tmp_path / "security.db"
    token = _create_bearer_token(db_path)

    identity = auth_context.resolve_authenticated_identity(
        f"Bearer {token}", db_path
    )

    assert identity == EXPECTED_IDENTITY


def test_incomplete_resolved_identity_fails_closed(tmp_path, monkeypatch):
    incomplete_identities = iter(
        (
            {
                "user_id": 1,
                "email": "",
                "role": "teacher",
                "tenant_id": "school-a",
            },
            {
                "user_id": 1,
                "email": "teacher.ahmed@school-a.edu",
                "role": "",
                "tenant_id": "school-a",
            },
            {
                "user_id": 1,
                "email": "teacher.ahmed@school-a.edu",
                "role": "teacher",
                "tenant_id": "",
            },
        )
    )
    created_services = []

    class FakeSessionService:
        def __init__(self, db_path):
            self.identity = next(incomplete_identities)
            self.initialized = False
            self.closed = False
            created_services.append(self)

        def initialize(self):
            self.initialized = True

        def resolve_session(self, token):
            assert self.initialized is True
            return self.identity

        def close(self):
            self.closed = True

    monkeypatch.setattr(auth_context, "SessionService", FakeSessionService)
    db_path = tmp_path / "unused.db"

    for _ in range(3):
        assert (
            auth_context.resolve_authenticated_identity(
                "Bearer valid-looking-token", db_path
            )
            is None
        )

    assert len(created_services) == 3
    assert all(service.initialized for service in created_services)
    assert all(service.closed for service in created_services)
