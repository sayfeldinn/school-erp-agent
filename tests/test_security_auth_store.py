import pytest

from security.auth_passwords import hash_password
from security.auth_store import SecurityStore


def test_tenant_creation_is_persistent(tmp_path):
    db_path = tmp_path / "security.db"
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
    finally:
        store.close()

    reopened = SecurityStore(db_path)
    try:
        reopened.initialize()
        tenant = reopened.get_tenant("school-a")

        assert tenant["id"] == "school-a"
        assert tenant["name"] == "Al Noor School"
        assert tenant["status"] == "active"
    finally:
        reopened.close()


def test_duplicate_tenant_id_is_rejected(tmp_path):
    store = SecurityStore(tmp_path / "security.db")
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")

        with pytest.raises(ValueError):
            store.create_tenant(tenant_id="school-a", name="Duplicate School")
    finally:
        store.close()


def test_user_identity_persists_role_and_tenant(tmp_path):
    db_path = tmp_path / "security.db"
    temporary_password = "temporary-password-for-store-test"
    password_hash = hash_password(temporary_password)
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_user(
            email="teacher.ahmed@school-a.edu",
            password_hash=password_hash,
            role="teacher",
            tenant_id="school-a",
        )
    finally:
        store.close()

    reopened = SecurityStore(db_path)
    try:
        reopened.initialize()
        user = reopened.get_user_by_email("teacher.ahmed@school-a.edu")

        assert user["email"] == "teacher.ahmed@school-a.edu"
        assert user["role"] == "teacher"
        assert user["tenant_id"] == "school-a"
        assert user["status"] == "active"
        assert user["password_hash"]
        assert temporary_password not in str(user)
    finally:
        reopened.close()


def test_user_cannot_reference_unknown_tenant(tmp_path):
    store = SecurityStore(tmp_path / "security.db")
    try:
        store.initialize()

        with pytest.raises(ValueError):
            store.create_user(
                email="teacher.ahmed@school-a.edu",
                password_hash=hash_password("temporary-password"),
                role="teacher",
                tenant_id="school-does-not-exist",
            )
    finally:
        store.close()


def test_duplicate_user_email_is_rejected_case_insensitively(tmp_path):
    store = SecurityStore(tmp_path / "security.db")
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        password_hash = hash_password("temporary-password")
        store.create_user(
            email="Teacher.Ahmed@school-a.edu",
            password_hash=password_hash,
            role="teacher",
            tenant_id="school-a",
        )

        with pytest.raises(ValueError):
            store.create_user(
                email="teacher.ahmed@school-a.edu",
                password_hash=password_hash,
                role="teacher",
                tenant_id="school-a",
            )
    finally:
        store.close()


def test_user_lookup_is_case_insensitive(tmp_path):
    store = SecurityStore(tmp_path / "security.db")
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_user(
            email="Teacher.Ahmed@school-a.edu",
            password_hash=hash_password("temporary-password"),
            role="teacher",
            tenant_id="school-a",
        )

        original_case = store.get_user_by_email("Teacher.Ahmed@school-a.edu")
        lower_case = store.get_user_by_email("teacher.ahmed@school-a.edu")

        assert original_case is not None
        assert lower_case is not None
        assert lower_case["id"] == original_case["id"]
    finally:
        store.close()
