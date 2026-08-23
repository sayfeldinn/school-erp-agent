import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from security.auth_passwords import verify_password
from security.auth_routes import create_auth_router
from security.auth_store import SecurityStore


REGISTER_PATH = "/auth/register"
SCHOOL_A_STUDENT = "Sara Mohamed"
SCHOOL_A_TEACHER = "Ms. Nour Hassan"
SCHOOL_B_STUDENT = "Omar White"
SCHOOL_B_TEACHER = "Ms. Clara Smith"
VALID_PASSWORD = "registration-test-password"


def _create_client(db_path) -> TestClient:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SecurityStore(db_path)
    try:
        store.initialize()
        store.create_tenant(tenant_id="school-a", name="Al Noor School")
        store.create_tenant(tenant_id="school-b", name="Green Valley School")
    finally:
        store.close()

    app = FastAPI()
    app.include_router(create_auth_router(db_path))
    return TestClient(app)


def _payload(
    *,
    full_name=SCHOOL_A_STUDENT,
    password=VALID_PASSWORD,
    confirm_password=None,
    school_code="ANS-A-S",
):
    return {
        "full_name": full_name,
        "password": password,
        "confirm_password": password if confirm_password is None else confirm_password,
        "school_code": school_code,
    }


def _register(client, **overrides):
    return client.post(REGISTER_PATH, json=_payload(**overrides))


def _get_user(db_path, email):
    store = SecurityStore(db_path)
    try:
        return store.get_user_by_email(email)
    finally:
        store.close()


def _user_count(db_path) -> int:
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _assert_generic_registration_failure(response) -> None:
    assert response.status_code == 400
    assert response.json() == {"detail": "registration_failed"}
    public_body = json.dumps(response.json()).casefold()
    for forbidden in (
        "school-a",
        "school-b",
        "student",
        "teacher",
        "tenant",
        "roster",
        "already",
        "ambiguous",
        "sql",
    ):
        assert forbidden not in public_body


def test_all_demo_codes_derive_only_their_roster_role_and_tenant(tmp_path):
    cases = [
        ("ANS-A-S", SCHOOL_A_STUDENT, "student", "school-a"),
        ("ANS-A-T", SCHOOL_A_TEACHER, "teacher", "school-a"),
        ("FS-B-S", SCHOOL_B_STUDENT, "student", "school-b"),
        ("FS-B-T", SCHOOL_B_TEACHER, "teacher", "school-b"),
    ]
    registrations = []

    for index, (code, full_name, expected_role, expected_tenant) in enumerate(cases):
        db_path = tmp_path / f"code-{index}" / "security.db"
        client = _create_client(db_path)
        response = _register(client, full_name=full_name, school_code=code)
        registrations.append(
            (response, db_path, expected_role, expected_tenant)
        )

    assert [item[0].status_code for item in registrations] == [201, 201, 201, 201]
    for response, db_path, expected_role, expected_tenant in registrations:
        email = response.json()["email"]
        user = _get_user(db_path, email)
        assert user is not None
        assert user["role"] == expected_role
        assert user["tenant_id"] == expected_tenant
        assert user["role"] != "admin"


def test_school_a_student_registration_returns_only_canonical_email(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    response = _register(
        client,
        full_name="  sArA   mOhAmEd  ",
        school_code="ANS-A-S",
    )

    assert response.status_code == 201
    assert response.json() == {
        "status": "created",
        "email": "sara.mohamed@school-a.edu",
    }
    for forbidden in (
        "password",
        "password_hash",
        "access_token",
        "token",
        "session",
        "role",
        "tenant",
        "tenant_id",
        "student_id",
        "teacher_id",
        "erp_person_id",
    ):
        assert forbidden not in response.json()


def test_registration_does_not_create_a_session_or_log_the_user_in(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    response = _register(client)

    assert response.status_code == 201
    assert "access_token" not in response.json()
    assert "token_type" not in response.json()
    assert client.get("/auth/me").status_code == 401


def test_unknown_and_admin_style_codes_are_generic_failures(tmp_path):
    codes = ["UNKNOWN", "ANS-A-A", "FS-B-A", "ADMIN"]
    responses = []

    for index, code in enumerate(codes):
        client = _create_client(tmp_path / f"code-{index}" / "security.db")
        responses.append(_register(client, school_code=code))

    for response in responses:
        _assert_generic_registration_failure(response)


def test_existing_person_with_wrong_school_code_is_a_generic_failure(tmp_path):
    cases = [
        (SCHOOL_A_STUDENT, "FS-B-S"),
        (SCHOOL_B_STUDENT, "ANS-A-S"),
    ]
    responses = []

    for index, (full_name, code) in enumerate(cases):
        client = _create_client(tmp_path / f"school-{index}" / "security.db")
        responses.append(
            _register(client, full_name=full_name, school_code=code)
        )

    for response in responses:
        _assert_generic_registration_failure(response)


def test_person_with_wrong_role_code_is_a_generic_failure(tmp_path):
    cases = [
        (SCHOOL_A_STUDENT, "ANS-A-T"),
        (SCHOOL_A_TEACHER, "ANS-A-S"),
    ]
    responses = []

    for index, (full_name, code) in enumerate(cases):
        client = _create_client(tmp_path / f"role-{index}" / "security.db")
        responses.append(
            _register(client, full_name=full_name, school_code=code)
        )

    for response in responses:
        _assert_generic_registration_failure(response)


def test_unknown_roster_name_is_a_generic_failure(tmp_path):
    client = _create_client(tmp_path / "security.db")

    response = _register(client, full_name="Omar Ali")

    _assert_generic_registration_failure(response)


def test_password_length_boundaries_reuse_the_15_to_128_policy(tmp_path):
    cases = [
        (14, 400, "password_policy_failed"),
        (15, 201, None),
        (128, 201, None),
        (129, 400, "password_policy_failed"),
    ]
    responses = []

    for length, expected_status, expected_detail in cases:
        client = _create_client(tmp_path / f"password-{length}" / "security.db")
        password = "p" * length
        response = _register(client, password=password)
        responses.append((response, expected_status, expected_detail))

    assert [item[0].status_code for item in responses] == [
        item[1] for item in responses
    ]
    for response, _, expected_detail in responses:
        if expected_detail is not None:
            assert response.json() == {"detail": expected_detail}


def test_password_confirmation_mismatch_is_rejected(tmp_path):
    client = _create_client(tmp_path / "security.db")

    response = _register(
        client,
        confirm_password="a-different-registration-password",
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "password_confirmation_failed"}


def test_every_extra_authority_field_is_rejected(tmp_path):
    authority_fields = {
        "email": "attacker@school-b.edu",
        "role": "admin",
        "tenant": "school-b",
        "tenant_id": "school-b",
        "school": "school-b",
        "student_id": 24,
        "studentId": 24,
        "teacher_id": 5,
        "erp_person_id": 24,
    }
    responses = []

    for index, (field, value) in enumerate(authority_fields.items()):
        client = _create_client(tmp_path / f"authority-{index}" / "security.db")
        payload = _payload()
        payload[field] = value
        responses.append(client.post(REGISTER_PATH, json=payload))

    assert [response.status_code for response in responses] == [400] * len(
        authority_fields
    )
    for response in responses:
        assert response.json() == {"detail": "registration_failed"}


def test_registration_never_stores_the_raw_password(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    response = _register(client)

    assert response.status_code == 201
    user = _get_user(db_path, response.json()["email"])
    assert user is not None
    assert user["password_hash"] != VALID_PASSWORD
    assert VALID_PASSWORD not in user["password_hash"]
    assert verify_password(VALID_PASSWORD, user["password_hash"]) is True


def test_same_roster_person_cannot_register_twice_or_receive_dot_two(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    first = _register(client, full_name=SCHOOL_A_STUDENT)
    second = _register(client, full_name="  SARA   MOHAMED ")

    assert first.status_code == 201
    assert first.json()["email"] == "sara.mohamed@school-a.edu"
    _assert_generic_registration_failure(second)
    assert _user_count(db_path) == 1
    assert _get_user(db_path, "sara.mohamed.2@school-a.edu") is None


def test_failed_registration_leaves_no_login_capable_orphan(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    registration = _register(
        client,
        full_name=SCHOOL_A_STUDENT,
        school_code="FS-B-S",
    )
    possible_logins = [
        client.post(
            "/auth/login",
            json={
                "email": email,
                "password": VALID_PASSWORD,
            },
        )
        for email in (
            "sara.mohamed@school-a.edu",
            "sara.mohamed@school-b.edu",
        )
    ]

    assert registration.status_code == 400
    assert registration.json() == {"detail": "registration_failed"}
    assert _user_count(db_path) == 0
    assert [response.status_code for response in possible_logins] == [401, 401]


def test_generated_email_uniqueness_remains_case_insensitive(tmp_path):
    db_path = tmp_path / "security.db"
    client = _create_client(db_path)

    response = _register(client)

    assert response.status_code == 201
    email = response.json()["email"]
    user = _get_user(db_path, email.upper())
    assert user is not None
    store = SecurityStore(db_path)
    try:
        with pytest.raises(ValueError):
            store.create_user(
                email=email.upper(),
                password_hash=user["password_hash"],
                role=user["role"],
                tenant_id=user["tenant_id"],
            )
    finally:
        store.close()
