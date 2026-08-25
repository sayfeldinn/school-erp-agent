from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from agent.executor import ToolCall, ToolExecutor
from agent.tools import TOOL_REGISTRY, allowed_tools_for
from api.mock_api import ATTENDANCE, app


SARA_IDENTITY = {
    "user": "student.sara@school-a.edu",
    "role": "student",
    "school": "school-a",
}

SARA_PROFILE = {
    "id": 2,
    "name": "Sara Mohamed",
    "grade": 5,
    "classroom": "5A",
    "schoolId": "school-a",
}

FORBIDDEN_TARGET_ARGUMENTS = {
    "id": 24,
    "student_id": 24,
    "studentId": 24,
    "email": "other.student@school-b.edu",
    "user": "other.student@school-b.edu",
    "school": "school-b",
    "tenant": "school-b",
    "role": "admin",
}


@pytest.fixture
def executor_factory(monkeypatch):
    resources = []

    def create(*, role: str, user: str, school: str):
        executor = ToolExecutor("http://mock-api.test", role, user, school)
        client = TestClient(app)
        requests = []

        def routed_get(url, params=None, headers=None):
            path = urlsplit(url).path
            requests.append(
                {
                    "path": path,
                    "params": params,
                    "headers": dict(headers or {}),
                }
            )
            return client.get(path, params=params, headers=headers)

        monkeypatch.setattr(executor._http, "get", routed_get)
        resources.append((executor, client))
        return executor, requests

    yield create

    for executor, client in reversed(resources):
        executor.close()
        client.close()


def test_role_matrix_distinguishes_student_teacher_admin():
    assert {
        "student": allowed_tools_for("student"),
        "teacher": allowed_tools_for("teacher"),
        "admin": allowed_tools_for("admin"),
        "missing": allowed_tools_for(None),
        "unknown": allowed_tools_for("unknown"),
    } == {
        "student": [
            "get_my_profile",
            "get_my_attendance",
        ],
        "teacher": [
            "get_students",
            "get_student",
            "get_attendance",
            "get_teachers",
        ],
        "admin": [
            "get_students",
            "get_student",
            "get_attendance",
            "get_teachers",
        ],
        "missing": [],
        "unknown": [],
    }


def test_student_self_tool_schemas_are_parameterless():
    expected_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    parameters_by_tool = {
        name: (TOOL_REGISTRY.get(name) or {})
        .get("function", {})
        .get("parameters")
        for name in ("get_my_profile", "get_my_attendance")
    }

    assert parameters_by_tool == {
        "get_my_profile": expected_schema,
        "get_my_attendance": expected_schema,
    }
    for parameters in parameters_by_tool.values():
        assert parameters is not None
        assert set(FORBIDDEN_TARGET_ARGUMENTS).isdisjoint(parameters["properties"])


def test_student_get_my_profile_returns_only_authenticated_student(executor_factory):
    executor, requests = executor_factory(**SARA_IDENTITY)

    result = executor.execute(ToolCall("get_my_profile", {}))

    assert result.status == "ok"
    assert result.http_status == 200
    assert result.payload == SARA_PROFILE
    assert len(requests) == 1


def test_student_get_my_attendance_returns_only_authenticated_student(executor_factory):
    latest_attendance = max(
        ATTENDANCE[str(SARA_PROFILE["id"])],
        key=lambda row: row["date"],
    )
    expected = {
        "studentId": SARA_PROFILE["id"],
        "name": SARA_PROFILE["name"],
        "date": latest_attendance["date"],
        "status": latest_attendance["status"],
    }
    executor, requests = executor_factory(**SARA_IDENTITY)

    result = executor.execute(ToolCall("get_my_attendance", {}))

    assert result.status == "ok"
    assert result.http_status == 200
    assert result.payload == expected
    assert len(requests) == 1


def test_student_cannot_use_broad_school_tools(executor_factory):
    executor, requests = executor_factory(**SARA_IDENTITY)
    calls = {
        "get_students": {},
        "get_student": {"id": 2},
        "get_attendance": {"studentId": 2},
        "get_teachers": {},
    }

    observed = {
        name: (
            result.status,
            result.http_status,
        )
        for name, arguments in calls.items()
        for result in [executor.execute(ToolCall(name, arguments))]
    }

    assert observed == {
        name: ("not_allowed", 400)
        for name in calls
    }
    assert requests == []


def test_teacher_can_get_teachers(executor_factory):
    executor, requests = executor_factory(
        role="teacher",
        user="teacher.ahmed@school-a.edu",
        school="school-a",
    )

    result = executor.execute(ToolCall("get_teachers", {}))

    assert (result.status, result.http_status, len(requests)) == (
        "ok",
        200,
        1,
    )


def test_admin_can_get_teachers(executor_factory):
    executor, requests = executor_factory(
        role="admin",
        user="teacher.ahmed@school-a.edu",
        school="school-a",
    )

    result = executor.execute(ToolCall("get_teachers", {}))

    assert result.status == "ok"
    assert result.http_status == 200
    assert len(requests) == 1
    assert requests[0]["path"] == "/teachers"
    assert requests[0]["headers"]["X-Agent-Role"] == "admin"
    assert result.payload["teachers"]
    assert all(teacher["schoolId"] == "school-a" for teacher in result.payload["teachers"])


def test_student_cannot_inject_target_identity_into_self_tools(executor_factory):
    executor, requests = executor_factory(**SARA_IDENTITY)
    observed = {}

    for tool_name in ("get_my_profile", "get_my_attendance"):
        for argument_name, value in FORBIDDEN_TARGET_ARGUMENTS.items():
            result = executor.execute(ToolCall(tool_name, {argument_name: value}))
            observed[(tool_name, argument_name)] = (
                result.status,
                result.http_status,
            )

    assert observed == {
        (tool_name, argument_name): ("invalid_params", 400)
        for tool_name in ("get_my_profile", "get_my_attendance")
        for argument_name in FORBIDDEN_TARGET_ARGUMENTS
    }
    assert requests == []


def test_student_self_scope_is_bound_to_tenant(executor_factory):
    executor, requests = executor_factory(
        role="student",
        user="student.sara@school-a.edu",
        school="school-b",
    )

    results = [
        executor.execute(ToolCall(tool_name, {}))
        for tool_name in ("get_my_profile", "get_my_attendance")
    ]

    assert [
        (result.status, result.http_status, result.payload)
        for result in results
    ] == [
        ("forbidden", 403, None),
        ("forbidden", 403, None),
    ]
    assert len(requests) == 2
    assert all(request["headers"]["X-Agent-School"] == "school-b" for request in requests)
