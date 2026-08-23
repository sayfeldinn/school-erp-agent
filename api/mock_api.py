"""Mock ERP API - temporary stub serving data/seed.json per docs/api-contract.md.

Run:  uvicorn api.mock_api:app --port 8001 --host 127.0.0.1
Contract rules implemented exactly:
- all responses JSON; 403 when requested rows' schoolId != X-Agent-School
- "today" resolves to metadata.lastSchoolDay (never hardcoded dates)
- friends of the contract: missing identity header -> 403 (no data leaks)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from security.auth_registration import generated_roster_identities

DATA = Path(__file__).resolve().parent.parent / "data" / "seed.json"
SEED: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))

STUDENTS = {s["id"]: s for s in SEED["students"]}
TEACHERS = SEED["teachers"]
ATTENDANCE = SEED["attendance"]  # str id -> [{date, status}]
LAST_SCHOOL_DAY = SEED["metadata"]["lastSchoolDay"]
_GENERATED_ROSTER_IDENTITIES = generated_roster_identities()
TRUSTED_SCHOOL_BY_USER = {
    **{
        identity["email"]: identity["tenant_id"]
        for identity in _GENERATED_ROSTER_IDENTITIES
        if identity["role"] == "teacher"
    },
    "teacher.ahmed@school-a.edu": "school-a",
}
TRUSTED_STUDENT_BY_USER = {
    **{
        identity["email"]: {
            "student_id": identity["erp_person_id"],
            "school": identity["tenant_id"],
        }
        for identity in _GENERATED_ROSTER_IDENTITIES
        if identity["role"] == "student"
    },
    "student.sara@school-a.edu": {
        "student_id": 2,
        "school": "school-a",
    },
}

app = FastAPI(title="School ERP Mock API")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_, exc: StarletteHTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"error": "http_error", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_params", "detail": str(exc.errors()[0].get("msg", "invalid"))},
    )


def _school_scope(user_header: str | None, school_header: str | None) -> str:
    """Verify the claimed school against the caller's trusted server-side scope."""
    if not user_header or not school_header:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "reason": "missing_identity"})
    trusted_school = TRUSTED_SCHOOL_BY_USER.get(user_header)
    if trusted_school is None or school_header != trusted_school:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "reason": "school_scope"})
    return trusted_school


def _student_self_scope(
    user_header: str | None,
    school_header: str | None,
    role_header: str | None,
) -> dict[str, Any]:
    forbidden = HTTPException(
        status_code=403,
        detail={"error": "forbidden", "reason": "school_scope"},
    )
    if not user_header or not school_header or role_header != "student":
        raise forbidden

    trusted_identity = TRUSTED_STUDENT_BY_USER.get(user_header)
    if trusted_identity is None or trusted_identity["school"] != school_header:
        raise forbidden

    student = STUDENTS.get(trusted_identity["student_id"])
    if student is None or student["schoolId"] != trusted_identity["school"]:
        raise forbidden
    return student


def _in_scope(school: str, row_school: str) -> bool:
    return school == row_school


def _grade_students(grade: int) -> list[dict]:
    return [s for s in STUDENTS.values() if s["grade"] == grade]


def _latest_status(sid: int) -> dict | None:
    recs = ATTENDANCE.get(str(sid), [])
    return max(recs, key=lambda r: r["date"]) if recs else None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "data": SEED["metadata"]["version"],
        "students": len(STUDENTS),
        "teachers": len(TEACHERS),
    }


@app.get("/students")
def students(
    grade: int | None = Query(None, ge=1, le=12),
    classroom: str | None = None,
    name: str | None = None,
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
) -> dict:
    school = _school_scope(x_agent_user, x_agent_school)
    out = [
        s
        for s in STUDENTS.values()
        if _in_scope(school, s["schoolId"])
        and (grade is None or s["grade"] == grade)
        and (classroom is None or s["classroom"] == classroom)
        and (name is None or name.lower() in s["name"].lower())
    ]
    return {"students": out}


@app.get("/students/me")
def own_student(
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
    x_agent_role: str | None = Header(default=None),
) -> dict:
    return _student_self_scope(x_agent_user, x_agent_school, x_agent_role)


@app.get("/students/{sid}")
def student(
    sid: int,
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
) -> dict:
    school = _school_scope(x_agent_user, x_agent_school)
    s = STUDENTS.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if not _in_scope(school, s["schoolId"]):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "reason": "school_scope"})
    return s


@app.get("/teachers")
def teachers(
    grade: int | None = Query(None, ge=1, le=12),
    classroom: str | None = None,
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
) -> dict:
    school = _school_scope(x_agent_user, x_agent_school)
    out = [
        t
        for t in TEACHERS
        if _in_scope(school, t["schoolId"])
        and (grade is None or t["grade"] == grade)
        and (classroom is None or t["classroom"] == classroom)
    ]
    return {"teachers": out}


@app.get("/attendance/me")
def own_attendance(
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
    x_agent_role: str | None = Header(default=None),
) -> dict:
    student = _student_self_scope(x_agent_user, x_agent_school, x_agent_role)
    row = _latest_status(student["id"])
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {
        "studentId": student["id"],
        "name": student["name"],
        "date": row["date"],
        "status": row["status"],
    }


@app.get("/attendance")
def attendance(
    studentId: int | None = Query(None, alias="studentId", ge=1),
    date: str | None = None,
    grade: int | None = Query(None, ge=1, le=12),
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
) -> dict:
    school = _school_scope(x_agent_user, x_agent_school)
    target = date or LAST_SCHOOL_DAY

    if grade is not None:  # daily status for a whole grade (ONE call)
        records = []
        for s in _grade_students(grade):
            if not _in_scope(school, s["schoolId"]):
                continue
            recs = {r["date"]: r["status"] for r in ATTENDANCE.get(str(s["id"]), [])}
            if target in recs:
                records.append({"studentId": s["id"], "name": s["name"], "status": recs[target]})
        return {"date": target, "grade": grade, "records": records}

    if studentId is None:  # no selector -> invalid
        raise HTTPException(status_code=400, detail={"error": "invalid_params", "reason": "studentId or grade required"})
    s = STUDENTS.get(studentId)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if not _in_scope(school, s["schoolId"]):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "reason": "school_scope"})
    recs = ATTENDANCE.get(str(studentId), [])
    row = None
    if date:
        row = next((r for r in recs if r["date"] == date), None)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
    else:
        row = _latest_status(studentId)
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"studentId": s["id"], "name": s["name"], "date": row["date"], "status": row["status"]}


@app.get("/attendance/summary")
def attendance_summary(
    grade: int = Query(..., ge=1, le=12),
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    x_agent_user: str | None = Header(default=None),
    x_agent_school: str | None = Header(default=None),
) -> dict:
    school = _school_scope(x_agent_user, x_agent_school)
    month = month or LAST_SCHOOL_DAY[:7]
    out, rates = [], []
    for s in _grade_students(grade):
        if not _in_scope(school, s["schoolId"]):
            continue
        recs = [r for r in ATTENDANCE.get(str(s["id"]), []) if r["date"].startswith(month)]
        if not recs:
            continue  # no records -> no rate (cannot divide by zero)
        present = sum(1 for r in recs if r["status"] == "present")
        rate = round(present / len(recs) * 100, 1)
        rates.append(rate)
        out.append({"studentId": s["id"], "name": s["name"], "rate": rate})
    overall = round(sum(rates) / len(rates), 1) if rates else 0.0
    return {"grade": grade, "month": month, "attendanceRate": overall, "students": out}
