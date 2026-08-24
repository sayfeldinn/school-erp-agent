import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from agent.core import AgentLoop
from agent.executor import ToolCall, ToolExecutor
from api.mock_api import app


class ScriptedLLM:
    def __init__(self, messages):
        self._messages = list(messages)

    def chat(self, messages, tools=None, num_predict=512):
        return {"message": self._messages.pop(0)}, 0.0


def _tool_call(name, arguments):
    if isinstance(arguments, dict):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    return {"role": "assistant", "content": json.dumps({"name": name, "arguments": arguments})}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "http://attacker.invalid/steal"),
        ("host", "attacker.invalid"),
        ("path", "/teachers"),
        ("method", "POST"),
        ("headers", {"X-Agent-Role": "admin"}),
        ("school", "school-b"),
    ],
    ids=["url", "host", "path", "method", "headers", "query-school"],
)
def test_endpoint_escape_field_is_rejected_before_http(field, value, monkeypatch):
    executor = ToolExecutor(
        "http://mock-api.test",
        "teacher",
        "teacher.ahmed@school-a.edu",
        "school-a",
    )
    http_calls = []

    def unexpected_http(*args, **kwargs):
        http_calls.append((args, kwargs))
        pytest.fail("transport override reached HTTP execution")

    monkeypatch.setattr(executor._http, "get", unexpected_http)
    try:
        result = executor.execute(
            ToolCall("get_student", {"id": 24, field: value})
        )
    finally:
        executor.close()

    assert result.status == "invalid_params"
    assert result.http_status == 400
    assert len(http_calls) == 0


def test_valid_get_student_baseline_reaches_http_stub(monkeypatch):
    executor = ToolExecutor(
        "http://mock-api.test",
        "teacher",
        "teacher.ahmed@school-a.edu",
        "school-a",
    )
    http_calls = []

    class StubResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": 24}

    def recording_get(url, params=None, headers=None):
        http_calls.append({"url": url, "params": params, "headers": headers})
        return StubResponse()

    monkeypatch.setattr(executor._http, "get", recording_get)
    try:
        result = executor.execute(ToolCall("get_student", {"id": 24}))
    finally:
        executor.close()

    assert result.status == "ok"
    assert result.http_status == 200
    assert http_calls == [
        {
            "url": "http://mock-api.test/students/24",
            "params": None,
            "headers": {
                "X-Agent-User": "teacher.ahmed@school-a.edu",
                "X-Agent-Role": "teacher",
                "X-Agent-School": "school-a",
            },
        }
    ]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_student", {"id": "24"}),
        ("get_student", {"id": -1}),
        ("get_students", {"grade": 999999999}),
        ("get_students", {"grade": 5, "admin": True}),
        ("get_student", {"id": 1, "name": "Ahmed"}),
        ("get_student", "not-an-object"),
    ],
    ids=["wrong-type", "negative-id", "huge-grade", "extra-property", "both-selectors", "non-object"],
)
def test_malformed_tool_arguments_are_rejected_before_http(tool, arguments, monkeypatch):
    llm = ScriptedLLM([_tool_call(tool, arguments), {"role": "assistant", "content": "done"}])
    loop = AgentLoop("http://unused", "teacher", "teacher.ahmed@school-a.edu", "school-a", llm=llm)
    http_calls = []

    def unexpected_http(*args, **kwargs):
        http_calls.append((args, kwargs))
        raise AssertionError("invalid tool arguments must be rejected before HTTP execution")

    monkeypatch.setattr(loop.executor._http, "get", unexpected_http)
    try:
        result = loop.run("Perform the requested action.")
    finally:
        loop.close()

    assert result.status == "answered"
    assert result.tool_calls[0] == {"tool": tool, "arguments": arguments}
    assert result.steps[0]["tool"] == tool
    assert result.steps[0]["params"] == arguments
    assert result.steps[0]["status"] == "invalid_params"
    assert result.steps[0]["http"] == 400
    assert not http_calls


def _execute_attendance(arguments, monkeypatch):
    executor = ToolExecutor(
        "http://mock-api.test",
        "teacher",
        "teacher.ahmed@school-a.edu",
        "school-a",
    )
    client = TestClient(app)
    requests = []

    def routed_get(url, params=None, headers=None):
        path = urlsplit(url).path
        requests.append({"path": path, "params": params})
        return client.get(path, params=params, headers=headers)

    monkeypatch.setattr(executor._http, "get", routed_get)
    try:
        result = executor.execute(ToolCall("get_attendance", arguments))
    finally:
        executor.close()
        client.close()
    return result, requests


@pytest.mark.parametrize(
    "arguments",
    [
        {"studentId": 2, "grade": 5},
        {},
    ],
    ids=["student-and-grade", "no-selector"],
)
def test_attendance_invalid_selector_shape_is_rejected_before_http(
    arguments,
    monkeypatch,
):
    result, requests = _execute_attendance(arguments, monkeypatch)
    grade_record_count = (
        len(result.payload.get("records", []))
        if isinstance(result.payload, dict)
        else 0
    )

    assert {
        "status": result.status,
        "http_status": result.http_status,
        "http_calls": len(requests),
        "grade_record_count": grade_record_count,
    } == {
        "status": "invalid_params",
        "http_status": 400,
        "http_calls": 0,
        "grade_record_count": 0,
    }


@pytest.mark.parametrize(
    ("arguments", "payload_selector"),
    [
        ({"studentId": 2}, ("studentId", 2)),
        ({"grade": 5}, ("grade", 5)),
    ],
    ids=["student-id-only", "grade-only"],
)
def test_attendance_single_selector_remains_valid(
    arguments,
    payload_selector,
    monkeypatch,
):
    result, requests = _execute_attendance(arguments, monkeypatch)
    selector_name, selector_value = payload_selector

    assert result.status == "ok"
    assert result.http_status == 200
    assert result.payload[selector_name] == selector_value
    assert requests == [{"path": "/attendance", "params": arguments}]
