# Mock ERP API Contract — current implementation

Base URL: `http://127.0.0.1:8001`. The FastAPI app in `api/mock_api.py`
serves frozen demo data from `data/seed.json` and performs tenant, resource, and
student-self re-checks.

This is an **internal loopback demo boundary**. Public clients authenticate to
the Agent Server on port 8000 with a Bearer token; they must not use these
headers as credentials or call port 8001 as a public API.

## Conventions

- Responses are JSON and data endpoints are `GET`.
- Student and teacher roster rows carry `schoolId` for tenant/resource
  filtering; attendance response rows identify the student but omit `schoolId`.
- The executor maps approved tool calls to fixed paths and query keys.
- Omitted grade-attendance dates use `metadata.lastSchoolDay` from the seed
  (`2026-08-19` in the current data). Omitted single-student and self-service
  dates return the latest available record for that student.
- Empty collection results are successful empty arrays.
- Scope and identity failures are `403` with generic reasons.

## Internal demo identity context

The Agent Server resolves these values from the authenticated public identity;
`ToolExecutor` serializes them on Agent-to-Mock requests:

| Header | Example | Use inside the Mock ERP |
|---|---|---|
| `X-Agent-User` | `ms.nour.hassan@school-a.edu` | looks up the local demo identity mapping |
| `X-Agent-Role` | `student`, `teacher`, or `admin` | required as `student` on self-service endpoints |
| `X-Agent-School` | `school-a` | claimed school compared with the mapped identity and row scope |

These headers are server-derived in the normal application flow. The Mock ERP
does not cryptographically authenticate or sign them. Its static, roster-based
identity maps are a defense-in-depth scope re-check for local/demo operation,
not production service-to-service authentication.

The school-scoped map is generated from roster teacher identities plus a legacy
demo teacher entry; it does not query the authentication database dynamically.
Consequently, a CLI-provisioned admin used with this Mock ERP must use an email
present in that demo map. Student self-service uses a separate generated roster
map plus a legacy `student.sara@school-a.edu` entry.

School-scoped endpoints reject a missing internal user or school, unknown
internal users, and a school that differs from the trusted mapping. They do not
use the role header; role authorization has already occurred in the executor.
Student self endpoints separately require a known student email, the `student`
role, and the matching school.

## Fixed tool mapping

| Agent tool | Mock request |
|---|---|
| `get_students` | `GET /students` with optional `grade`, `classroom`, `name` |
| `get_student` by id | `GET /students/{id}` |
| `get_student` by name | `GET /students?name=...` |
| `get_teachers` | `GET /teachers` with optional `grade`, `classroom` |
| `get_attendance` | `GET /attendance` with one selector and optional `date` |
| `get_my_profile` | `GET /students/me` with no query parameters |
| `get_my_attendance` | `GET /attendance/me` with no query parameters |

The executor's role allowlist controls which mapping is reachable. In
particular, `get_teachers` is admin-only and both `get_my_*` tools are
student-only.

## Endpoints

### `GET /health`

No identity context is required.

```json
{ "status": "ok", "data": "seed_v1", "students": 26, "teachers": 5 }
```

### `GET /students`

Filters are optional: `grade` (integer 1–12), `classroom` (string), and `name`
(case-insensitive substring).

```json
{
  "students": [
    {
      "id": 1,
      "name": "Ahmed Ali",
      "grade": 5,
      "classroom": "5A",
      "schoolId": "school-a"
    }
  ]
}
```

Only rows matching the internally verified school are returned. No match is
`{"students": []}`.

### `GET /students/me`

Requires a known internal student user, role `student`, and matching school.
The Mock ERP resolves the roster id from that identity; no selector is accepted
from the tool call.

```json
{
  "id": 2,
  "name": "Sara Mohamed",
  "grade": 5,
  "classroom": "5A",
  "schoolId": "school-a"
}
```

### `GET /students/{id}`

```json
{
  "id": 1,
  "name": "Ahmed Ali",
  "grade": 5,
  "classroom": "5A",
  "schoolId": "school-a"
}
```

An unknown id returns `404 {"error": "not_found"}`. A known id outside the
verified school returns `403` without the row.

### `GET /teachers`

Filters are optional: `grade` (integer 1–12) and `classroom` (string).

```json
{
  "teachers": [
    {
      "id": 1,
      "name": "Ms. Nour Hassan",
      "subject": "Math",
      "grade": 5,
      "classroom": "5A",
      "schoolId": "school-a"
    }
  ]
}
```

The endpoint filters to the verified school. The deterministic agent role
allowlist exposes its mapped tool only to admins.

### `GET /attendance/me`

Requires the same internal student-self context as `/students/me` and returns
that student's latest attendance row:

```json
{
  "studentId": 2,
  "name": "Sara Mohamed",
  "date": "2026-08-19",
  "status": "absent"
}
```

No attendance record returns `404 {"error": "not_found"}`.

### `GET /attendance`

The validated `get_attendance` tool sends exactly one selector:

- `studentId` (integer at least 1) for one student's attendance; or
- `grade` (integer 1–12) for daily attendance across the verified school and
  grade.

`date` is optional for either selector. With `grade`, omission uses the seed's
`metadata.lastSchoolDay`; with `studentId`, omission returns that student's
latest available record. The tool schema describes ISO `YYYY-MM-DD` text, but
the current JSON Schema does not enforce a date format.

Single-student response:

```json
{
  "studentId": 1,
  "name": "Ahmed Ali",
  "date": "2026-08-19",
  "status": "present"
}
```

Whole-grade response:

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

No selector returns `400`. The executor prevents both selectors from being sent
together; the direct Mock handler currently takes the grade branch if both are
present. A missing student/date row returns `404`, and a cross-school student id
returns `403`.

### `GET /attendance/summary`

This Mock-only endpoint takes required `grade` (integer 1–12) and optional
`month` (`YYYY-MM`, defaulting to the seed's last-school-day month). It computes
each student's rounded present-rate, excludes students with no records in that
month, and reports the unweighted mean of those per-student rates.

```json
{
  "grade": 5,
  "month": "2026-08",
  "attendanceRate": 94.1,
  "students": [
    { "studentId": 1, "name": "Ahmed Ali", "rate": 100.0 }
  ]
}
```

No current agent tool maps to `/attendance/summary`; it is not an LLM-visible
capability.

## Error shapes

| Status | Body | Meaning |
|---|---|---|
| `400` | `{"error": "invalid_params", "detail": "..."}` | FastAPI query validation failure |
| `400` | `{"error": "invalid_params", "reason": "studentId or grade required"}` | `/attendance` called without a selector |
| `403` | `{"error": "forbidden", "reason": "missing_identity"}` | internal user or school missing on a school-scoped non-self endpoint |
| `403` | `{"error": "forbidden", "reason": "school_scope"}` | unknown/mismatched identity, tenant, or resource; student-self endpoints also use this for missing or invalid self context |
| `404` | `{"error": "not_found"}` | requested row or attendance record absent |

The executor replaces these downstream bodies before LLM reinjection: `403`
becomes a stable scope denial, and other error bodies become a stable data-service
failure. The raw downstream body is not exposed to the LLM or final user.
