from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.executor import ToolCall, ToolExecutor
from api.mock_api import ATTENDANCE, app as mock_app
from security.auth_context import resolve_authenticated_identity
from security.auth_routes import create_auth_router
from security.auth_store import SecurityStore


PASSWORD = "registered-agent-integration-password"
SARA_PROFILE = {
    "id": 2,
    "name": "Sara Mohamed",
    "grade": 5,
    "classroom": "5A",
    "schoolId": "school-a",
}
OMAR_PROFILE = {
    "id": 24,
    "name": "Omar White",
    "grade": 5,
    "classroom": "5A",
    "schoolId": "school-b",
}


@dataclass
class RegisteredAccount:
    email: str
    role: str
    school: str
    token: str
    identity: dict[str, str]
    me: dict[str, str]
    executor: ToolExecutor
    requests: list[dict]
    auth_client: TestClient


def _latest_attendance(profile):
    latest = max(
        ATTENDANCE[str(profile["id"])],
        key=lambda row: row["date"],
    )
    return {
        "studentId": profile["id"],
        "name": profile["name"],
        "date": latest["date"],
        "status": latest["status"],
    }


@pytest.fixture
def registered_account_factory(tmp_path, monkeypatch):
    resources = []
    account_number = 0

    def create(*, full_name, school_code, email, role, school):
        nonlocal account_number
        account_number += 1
        db_path = tmp_path / f"account-{account_number}" / "security.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        store = SecurityStore(db_path)
        try:
            store.initialize()
            store.create_tenant(tenant_id="school-a", name="Al Noor School")
            store.create_tenant(
                tenant_id="school-b",
                name="Green Valley School",
            )
        finally:
            store.close()

        auth_app = FastAPI()
        auth_app.include_router(create_auth_router(db_path))
        auth_client = TestClient(auth_app)

        registration = auth_client.post(
            "/auth/register",
            json={
                "full_name": full_name,
                "password": PASSWORD,
                "confirm_password": PASSWORD,
                "school_code": school_code,
            },
        )
        assert registration.status_code == 201
        assert registration.json() == {"status": "created", "email": email}

        login = auth_client.post(
            "/auth/login",
            json={"email": email, "password": PASSWORD},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        authorization = {"Authorization": f"Bearer {token}"}

        me_response = auth_client.get("/auth/me", headers=authorization)
        assert me_response.status_code == 200
        identity = resolve_authenticated_identity(
            authorization["Authorization"],
            db_path,
        )
        assert identity is not None

        executor = ToolExecutor(
            "http://mock-api.test",
            identity["role"],
            identity["user"],
            identity["school"],
        )
        mock_client = TestClient(mock_app)
        requests = []

        # These X-Agent headers are the existing internal ToolExecutor -> Mock
        # boundary. Public authentication above uses only the Bearer token.
        def routed_get(url, params=None, headers=None):
            path = urlsplit(url).path
            requests.append(
                {
                    "path": path,
                    "params": params,
                    "headers": dict(headers or {}),
                }
            )
            return mock_client.get(path, params=params, headers=headers)

        monkeypatch.setattr(executor._http, "get", routed_get)
        resources.append((executor, auth_client, mock_client))
        return RegisteredAccount(
            email=email,
            role=role,
            school=school,
            token=token,
            identity=identity,
            me=me_response.json(),
            executor=executor,
            requests=requests,
            auth_client=auth_client,
        )

    yield create

    for executor, auth_client, mock_client in reversed(resources):
        executor.close()
        auth_client.close()
        mock_client.close()


def test_generated_accounts_propagate_bearer_derived_identity_internally(
    registered_account_factory,
):
    cases = [
        {
            "full_name": "Sara Mohamed",
            "school_code": "ANS-A-S",
            "email": "sara.mohamed@school-a.edu",
            "role": "student",
            "school": "school-a",
            "tool": "get_my_profile",
        },
        {
            "full_name": "Ms. Nour Hassan",
            "school_code": "ANS-A-T",
            "email": "ms.nour.hassan@school-a.edu",
            "role": "teacher",
            "school": "school-a",
            "tool": "get_students",
        },
        {
            "full_name": "Omar White",
            "school_code": "FS-B-S",
            "email": "omar.white@school-b.edu",
            "role": "student",
            "school": "school-b",
            "tool": "get_my_profile",
        },
        {
            "full_name": "Ms. Clara Smith",
            "school_code": "FS-B-T",
            "email": "ms.clara.smith@school-b.edu",
            "role": "teacher",
            "school": "school-b",
            "tool": "get_students",
        },
    ]
    observed = []

    for case in cases:
        tool_name = case.pop("tool")
        account = registered_account_factory(**case)
        account.executor.execute(ToolCall(tool_name, {}))
        observed.append(
            {
                "identity": account.identity,
                "me": account.me,
                "internal_headers": account.requests[0]["headers"],
            }
        )

    assert observed == [
        {
            "identity": {
                "user": case["email"],
                "role": case["role"],
                "school": case["school"],
            },
            "me": {
                "email": case["email"],
                "role": case["role"],
                "tenant_id": case["school"],
            },
            "internal_headers": {
                "X-Agent-User": case["email"],
                "X-Agent-Role": case["role"],
                "X-Agent-School": case["school"],
            },
        }
        for case in cases
    ]


def test_registered_identity_ignores_forged_public_x_agent_headers(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Ms. Nour Hassan",
        school_code="ANS-A-T",
        email="ms.nour.hassan@school-a.edu",
        role="teacher",
        school="school-a",
    )

    response = account.auth_client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {account.token}",
            "X-Agent-User": "attacker@example.com",
            "X-Agent-Role": "admin",
            "X-Agent-School": "school-b",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "email": account.email,
        "role": "teacher",
        "tenant_id": "school-a",
    }


def test_registered_school_a_teacher_can_use_teacher_erp_tools(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Ms. Nour Hassan",
        school_code="ANS-A-T",
        email="ms.nour.hassan@school-a.edu",
        role="teacher",
        school="school-a",
    )
    results = {
        "get_students": account.executor.execute(
            ToolCall("get_students", {"grade": 5})
        ),
        "get_student": account.executor.execute(
            ToolCall("get_student", {"id": 2})
        ),
        "get_attendance": account.executor.execute(
            ToolCall("get_attendance", {"studentId": 2})
        ),
    }

    assert len(account.requests) == 3
    assert {
        name: (result.status, result.http_status)
        for name, result in results.items()
    } == {
        "get_students": ("ok", 200),
        "get_student": ("ok", 200),
        "get_attendance": ("ok", 200),
    }
    assert results["get_students"].payload["students"]
    assert all(
        student["schoolId"] == "school-a"
        for student in results["get_students"].payload["students"]
    )
    assert results["get_student"].payload == SARA_PROFILE
    assert results["get_attendance"].payload == _latest_attendance(SARA_PROFILE)


def test_registered_teacher_still_cannot_use_admin_teacher_tool(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Ms. Nour Hassan",
        school_code="ANS-A-T",
        email="ms.nour.hassan@school-a.edu",
        role="teacher",
        school="school-a",
    )

    result = account.executor.execute(ToolCall("get_teachers", {}))

    assert (result.status, result.http_status, account.requests) == (
        "not_allowed",
        400,
        [],
    )


def test_registered_school_a_student_self_tools_return_only_sara(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Sara Mohamed",
        school_code="ANS-A-S",
        email="sara.mohamed@school-a.edu",
        role="student",
        school="school-a",
    )
    profile = account.executor.execute(ToolCall("get_my_profile", {}))
    attendance = account.executor.execute(ToolCall("get_my_attendance", {}))

    assert len(account.requests) == 2
    assert (profile.status, profile.http_status, profile.payload) == (
        "ok",
        200,
        SARA_PROFILE,
    )
    assert (attendance.status, attendance.http_status, attendance.payload) == (
        "ok",
        200,
        _latest_attendance(SARA_PROFILE),
    )


def test_registered_student_still_cannot_use_broad_school_tools(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Sara Mohamed",
        school_code="ANS-A-S",
        email="sara.mohamed@school-a.edu",
        role="student",
        school="school-a",
    )
    calls = {
        "get_students": {},
        "get_student": {"id": 2},
        "get_attendance": {"studentId": 2},
        "get_teachers": {},
    }

    observed = {
        name: (result.status, result.http_status)
        for name, arguments in calls.items()
        for result in [account.executor.execute(ToolCall(name, arguments))]
    }

    assert observed == {
        name: ("not_allowed", 400)
        for name in calls
    }
    assert account.requests == []


def test_registered_school_b_teacher_sees_only_school_b_data(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Ms. Clara Smith",
        school_code="FS-B-T",
        email="ms.clara.smith@school-b.edu",
        role="teacher",
        school="school-b",
    )
    students = account.executor.execute(ToolCall("get_students", {"grade": 5}))
    omar = account.executor.execute(ToolCall("get_student", {"id": 24}))
    attendance = account.executor.execute(
        ToolCall("get_attendance", {"studentId": 24})
    )

    assert [
        (result.status, result.http_status)
        for result in (students, omar, attendance)
    ] == [("ok", 200), ("ok", 200), ("ok", 200)]
    assert students.payload["students"]
    assert all(
        student["schoolId"] == "school-b"
        for student in students.payload["students"]
    )
    assert omar.payload == OMAR_PROFILE
    assert attendance.payload == _latest_attendance(OMAR_PROFILE)


def test_registered_school_b_student_self_tools_return_only_omar(
    registered_account_factory,
):
    account = registered_account_factory(
        full_name="Omar White",
        school_code="FS-B-S",
        email="omar.white@school-b.edu",
        role="student",
        school="school-b",
    )
    profile = account.executor.execute(ToolCall("get_my_profile", {}))
    attendance = account.executor.execute(ToolCall("get_my_attendance", {}))

    assert (profile.status, profile.http_status, profile.payload) == (
        "ok",
        200,
        OMAR_PROFILE,
    )
    assert (attendance.status, attendance.http_status, attendance.payload) == (
        "ok",
        200,
        _latest_attendance(OMAR_PROFILE),
    )
    assert profile.payload["schoolId"] == "school-b"
    assert profile.payload["id"] != SARA_PROFILE["id"]
