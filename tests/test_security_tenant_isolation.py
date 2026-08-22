import pytest
from fastapi.testclient import TestClient

from api.mock_api import app

SCHOOL_A_HEADERS = {
    "X-Agent-User": "teacher.ahmed@school-a.edu",
    "X-Agent-Role": "teacher",
    "X-Agent-School": "school-a",
}


def test_cross_school_header_spoofing_is_rejected():
    client = TestClient(app)

    school_a = client.get("/students", headers=SCHOOL_A_HEADERS)
    assert school_a.status_code == 200

    spoofed_school_b = client.get("/students", headers={**SCHOOL_A_HEADERS, "X-Agent-School": "school-b"})
    assert spoofed_school_b.status_code == 403, (
        f"cross-school spoofing leaked data: {spoofed_school_b.json()}"
    )


def test_unknown_user_is_rejected():
    response = TestClient(app).get(
        "/students",
        headers={
            "X-Agent-User": "unknown@example.com",
            "X-Agent-Role": "teacher",
            "X-Agent-School": "school-a",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "reason": "school_scope"}


def test_missing_user_is_rejected():
    response = TestClient(app).get(
        "/students",
        headers={"X-Agent-Role": "teacher", "X-Agent-School": "school-a"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "reason": "missing_identity"}


def test_school_a_user_cannot_access_school_b_student():
    response = TestClient(app).get("/students/24", headers=SCHOOL_A_HEADERS)

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "reason": "school_scope"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/students", None),
        ("/students/1", None),
        ("/teachers", None),
        ("/attendance", {"studentId": 1}),
        ("/attendance/summary", {"grade": 5}),
    ],
    ids=["students", "student", "teachers", "attendance", "attendance-summary"],
)
def test_spoofed_school_header_is_rejected_for_all_scoped_endpoints(path, params):
    response = TestClient(app).get(
        path,
        params=params,
        headers={**SCHOOL_A_HEADERS, "X-Agent-School": "school-b"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "reason": "school_scope"}
