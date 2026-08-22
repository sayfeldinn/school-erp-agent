import pytest

from agent.executor import ToolCall, ToolExecutor
from agent.tools import allowed_tools_for


@pytest.mark.parametrize("role", [None, "", "unknown-role"])
def test_missing_or_unknown_roles_have_no_tool_access(role):
    assert allowed_tools_for(role) == []


def test_teacher_retains_intended_tool_access():
    assert allowed_tools_for("teacher") == [
        "get_students",
        "get_student",
        "get_teachers",
        "get_attendance",
    ]


def test_admin_retains_intended_tool_access():
    assert allowed_tools_for("admin") == [
        "get_students",
        "get_student",
        "get_teachers",
        "get_attendance",
    ]


@pytest.mark.parametrize("role", [None, "", "unknown-role"])
def test_denied_roles_are_rejected_before_http_request(role, monkeypatch):
    executor = ToolExecutor("http://unused", role, "user@example.com", "school-a")

    def unexpected_request(*args, **kwargs):
        pytest.fail("denied-role request reached the HTTP client")

    monkeypatch.setattr(executor._http, "get", unexpected_request)
    try:
        result = executor.execute(ToolCall("get_students", {"grade": 5}))
    finally:
        executor.close()

    assert result.status == "not_allowed"
    assert result.http_status == 400
