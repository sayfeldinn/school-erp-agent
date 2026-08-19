# API Contract (FROZEN v1 — Day 1)

> This document is frozen after Day 1. Changes require a team decision (merge request + standup note).
> Owner: **Person 2** (living doc) . Base URL: `http://127.0.0.1:8001` (mock API runs on port 8001; agent server on 8000).

## Conventions

- All responses are JSON. All endpoints are `GET`.
- Every data row carries `schoolId` — used for identity/scope authorization (Person 3).
- Dates are ISO `YYYY-MM-DD`. "Today" in queries is resolved **by the mock API** relative to runtime (never hardcoded).
- Query params are optional unless marked **required**.
- Errors: see "Error Shapes" below. No error message ever reveals internal policy/tool names.

## Identity headers (sent by the agent executor on every request)

| Header | Value | Meaning |
|---|---|---|
| `X-Agent-User` | e.g. `teacher.ahmed@school-a.edu` | who is calling |
| `X-Agent-Role` | `teacher` / `admin` | role (server-derived only) |
| `X-Agent-School` | `school-a` | school scope this user may access |

The mock API **must** return 403 when the requested rows' `schoolId` differs from `X-Agent-School`.

## Endpoints

### 1. `GET /health`

```json
{ "status": "ok", "data": "seed_v1", "students": 32, "teachers": 8 }
```

### 2. `GET /students`

Filters (all optional): `grade` (int 1-12), `classroom` (str), `name` (str, case-insensitive substring).

```json
{
  "students": [
    { "id": 1, "name": "Ahmed Ali", "grade": 5, "classroom": "5A", "schoolId": "school-a" }
  ]
}
```

- Empty result: `{ "students": [] }` (never an error).

### 3. `GET /students/:id`

```json
{ "id": 1, "name": "Ahmed Ali", "grade": 5, "classroom": "5A", "schoolId": "school-a" }
```

- Unknown id → 404: `{ "error": "not_found" }`

### 4. `GET /teachers`

Filters (all optional): `grade` (int 1-12), `classroom` (str).

```json
{
  "teachers": [
    { "id": 1, "name": "Ms. Nour Hassan", "subject": "Math", "grade": 5, "classroom": "5A", "schoolId": "school-a" }
  ]
}
```

### 5. `GET /attendance` (per student)

Params: `studentId` **required** (int >= 1), `date` (ISO, optional).

```json
{ "studentId": 1, "name": "Ahmed Ali", "date": "2026-08-19", "status": "present" }
```

- Without `date`: latest daily record. Unknown studentId → 404 `{ "error": "not_found" }`.

### 6. `GET /attendance?date=&grade=` (daily status for a whole grade — ONE call)

Params: `date` **required** (ISO), `grade` **required** (int 1-12).

```json
{
  "date": "2026-08-19",
  "grade": 5,
  "records": [
    { "studentId": 1, "name": "Ahmed Ali", "status": "present" },
    { "studentId": 2, "name": "Sara Mohamed", "status": "absent" }
  ]
}
```

- Supports "Which Grade 5 students were absent today?" without N calls.

### 7. `GET /attendance/summary` (API-computed percentages)

Params: `grade` **required** (int 1-12), `month` (optional, `YYYY-MM`, default: current month).

```json
{
  "grade": 5,
  "month": "2026-08",
  "attendanceRate": 91.2,
  "students": [
    { "studentId": 1, "name": "Ahmed Ali", "rate": 96.0 },
    { "studentId": 7, "name": "Omar Khaled", "rate": 74.0 }
  ]
}
```

- `rate` = present / (present + absent + late) * 100, computed by the API. **The LLM never computes percentages.**

## Error Shapes

| Code | Body |
|---|---|
| 400 | `{ "error": "invalid_params", "detail": "studentId must be >= 1" }` |
| 403 | `{ "error": "forbidden", "reason": "school_scope" }` (generic — no internal info) |
| 404 | `{ "error": "not_found" }` |

## Mutable vs frozen

- **Frozen:** all endpoint paths, response shapes above, identity headers, error shapes.
- **Mutable (Person 2):** seed data contents (must keep the same shape), extra filter params (must not break existing ones).