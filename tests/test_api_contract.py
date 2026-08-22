"""API contract tests - assert the mock API matches docs/api-contract.md exactly.

These run against the app in-process (no server needed):
    pytest tests/api_contract_tests.py -v
Only reason they fail: the API drifted from the frozen contract.
"""
import pytest
from fastapi.testclient import TestClient

from api.mock_api import app, LAST_SCHOOL_DAY, SEED

client = TestClient(app)

H = {
    "X-Agent-User": "teacher.ahmed@school-a.edu",
    "X-Agent-Role": "teacher",
    "X-Agent-School": "school-a",
}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "data": "seed_v1", "students": 26, "teachers": 5}


def test_students_all():
    r = client.get("/students", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"students"}
    assert all(set(s) == {"id", "name", "grade", "classroom", "schoolId"} for s in body["students"])
    assert all(s["schoolId"] == "school-a" for s in body["students"])
    assert len(body["students"]) == 23


def test_students_filter_grade():
    r = client.get("/students", params={"grade": 5}, headers=H)
    assert r.status_code == 200
    assert {s["grade"] for s in r.json()["students"]} == {5}
    assert len(r.json()["students"]) == 14  # 10 in 5A + 4 in 5B


def test_students_filter_name_case_insensitive():
    r = client.get("/students", params={"name": "sara"}, headers=H)
    assert [s["name"] for s in r.json()["students"]] == ["Sara Mohamed"]


def test_students_empty_result():
    r = client.get("/students", params={"grade": 12}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"students": []}


def test_student_by_id():
    r = client.get("/students/1", headers=H)
    assert r.status_code == 200
    assert r.json()["name"] == "Ahmed Ali"
    assert r.json()["schoolId"] == "school-a"


def test_student_404():
    r = client.get("/students/999", headers=H)
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


def test_teachers():
    r = client.get("/teachers", headers=H)
    body = r.json()
    assert set(body) == {"teachers"}
    assert all(t["schoolId"] == "school-a" for t in body["teachers"])
    assert len(body["teachers"]) == 4


def test_attendance_single_student_latest():
    r = client.get("/attendance", params={"studentId": 1}, headers=H)
    assert r.status_code == 200
    got = r.json()
    assert got["studentId"] == 1 and got["name"] == "Ahmed Ali"
    assert got["date"] == LAST_SCHOOL_DAY and got["status"] in {"present", "absent", "late"}


def test_attendance_single_student_by_date():
    r = client.get("/attendance", params={"studentId": 2, "date": LAST_SCHOOL_DAY}, headers=H)
    assert r.status_code == 200
    assert r.json()["date"] == LAST_SCHOOL_DAY
    assert r.json()["status"] == "absent"  # Sara seeded absent today


def test_attendance_404_unknown_student():
    r = client.get("/attendance", params={"studentId": 999}, headers=H)
    assert r.status_code == 404


def test_attendance_grade_today():
    r = client.get("/attendance", params={"date": LAST_SCHOOL_DAY, "grade": 5}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == LAST_SCHOOL_DAY and body["grade"] == 5
    assert {rec["studentId"] for rec in body["records"]} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    absent = [rec["name"] for rec in body["records"] if rec["status"] == "absent"]
    assert "Sara Mohamed" in absent and "Omar Khaled" in absent and "Karim Adel" in absent


def test_attendance_summary_rates():
    r = client.get("/attendance/summary", params={"grade": 5}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["grade"] == 5 and 0 <= body["attendanceRate"] <= 100
    low = [s["name"] for s in body["students"] if s["rate"] < 80]
    assert "Omar Khaled" in low  # seeded below 80% -> bonus query support


def test_attendance_summary_default_month_is_last_school_day():
    r = client.get("/attendance/summary", params={"grade": 5}, headers=H)
    assert r.json()["month"] == LAST_SCHOOL_DAY[:7]


def test_forbidden_other_school():
    r = client.get("/students", headers={**H, "X-Agent-School": "school-b"})
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "reason": "school_scope"}


def test_forbidden_cross_school_student_lookup():
    # school-a teacher asks for a school-b student id (Omar White = id 24)
    r = client.get("/students/24", headers=H)
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "reason": "school_scope"}


def test_missing_identity_rejected():
    r = client.get("/students")  # no X-Agent-School at all
    assert r.status_code == 403
    assert r.json()["reason"] == "missing_identity"


def test_invalid_params_shape():
    r = client.get("/students", params={"grade": 99}, headers=H)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_params"


def test_no_date_hardcoding():
    """The API must derive 'today' from metadata, not a frozen literal."""
    assert LAST_SCHOOL_DAY == SEED["metadata"]["lastSchoolDay"]
