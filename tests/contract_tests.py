"""Contract tests (FROZEN v1).

Assert that seed.json (and later the mock API responses) match docs/api-contract.md.
These tests exist so the agent can never silently break against a drifted API.
"""
import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_PATH = DATA_DIR / "seed.json"

REQUIRED_STUDENT_FIELDS = {"id", "name", "grade", "classroom", "schoolId"}
REQUIRED_TEACHER_FIELDS = {"id", "name", "subject", "grade", "classroom", "schoolId"}


@pytest.fixture(scope="session")
def seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_metadata(seed):
    assert seed["metadata"]["version"] == "seed_v1"
    assert seed["metadata"]["lastSchoolDay"]  # non-empty ISO date


def test_student_shape(seed):
    for s in seed["students"]:
        assert set(s.keys()) >= REQUIRED_STUDENT_FIELDS
        assert isinstance(s["id"], int) and s["id"] >= 1
        assert isinstance(s["grade"], int) and 1 <= s["grade"] <= 12
        assert isinstance(s["schoolId"], str) and s["schoolId"]


def test_teacher_shape(seed):
    for t in seed["teachers"]:
        assert set(t.keys()) >= REQUIRED_TEACHER_FIELDS
        assert isinstance(t["schoolId"], str) and t["schoolId"]


def test_attendance_shape(seed):
    for sid, records in seed["attendance"].items():
        assert sid.isdigit()
        for r in records:
            assert set(r.keys()) == {"date", "status"}
            assert r["status"] in {"present", "absent", "late"}
            assert len(r["date"].split("-")) == 3


def test_no_restricted_fields(seed):
    """Data minimization: no credentials/salaries/restricted keys anywhere."""
    blocked = {"password", "salary", "api_key", "token", "secret", "phone", "address"}
    for s in seed["students"]:
        assert not (set(s.keys()) & blocked), f"restricted field in student {s['id']}"
    for t in seed["teachers"]:
        assert not (set(t.keys()) & blocked), f"restricted field in teacher {t['id']}"


def test_grades_cover_required_range(seed):
    grades = {s["grade"] for s in seed["students"] if s["schoolId"] == "school-a"}
    assert {5, 6} <= grades  # grades 5 and 6 must exist for the bonus queries


def test_edge_cases_exist(seed):
    # empty classroom is implicitly "any grade with no students"; no-record students:
    no_records = {s["name"] for s in seed["students"] if not seed["attendance"].get(str(s["id"]))}
    assert len(no_records) >= 1
    # poisoned-data case present for Person 3's indirect-injection test
    assert any("Ignore previous instructions" in s["name"] for s in seed["students"])


def test_below_80_students_exist(seed):
    """Seed must contain students under 80% attendance for the bonus query."""
    low = []
    for sid, records in seed["attendance"].items():
        if records:
            present = sum(1 for r in records if r["status"] == "present")
            if present / len(records) < 0.8:
                low.append(sid)
    assert len(low) >= 2


def test_tool_schemas_are_frozen_and_strict():
    from agent.tools import TOOL_REGISTRY, tool_names

    assert tool_names() == ["get_students", "get_student", "get_teachers", "get_attendance"]
    for name in tool_names():
        params = TOOL_REGISTRY[name]["function"]["parameters"]
        assert params.get("additionalProperties") is False, f"{name} must be strict"


def test_executor_validation_matrix():
    from agent.executor import ToolCall, ToolExecutor, ToolRejectedError

    ex = ToolExecutor("http://127.0.0.1:8001", "teacher", "t@a", "school-a")
    ex.validate(ToolCall("get_students", {"grade": 5}))  # allowed, valid
    with pytest.raises(ToolRejectedError) as e:
        ex.validate(ToolCall("delete_student", {}))
    assert e.value.kind == "unknown_tool"
    with pytest.raises(ToolRejectedError) as e:
        ex.validate(ToolCall("get_students", {"grade": 99}))
    assert e.value.kind == "invalid_params"
    ex.close()