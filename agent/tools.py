"""Tool registry + strict schemas (FROZEN v1 - see plan.md Phase 0).

Each tool maps 1:1 to an endpoint in docs/api-contract.md.
Descriptions are written to guide the LLM's tool selection.
Enforcement (exist? role-allowlist? schema bounds?) happens in executor.py -
never in the model.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI-style JSON Schema, as consumed by Ollama's `tools`)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_students",
            "description": (
                "Use for ANY question about students (lists, counts, names, "
                "grades, classrooms). Returns students matching the optional "
                "filters. Examples: 'How many students are in Grade 5?' -> "
                'grade=5; "Show Grade 5 students" -> grade=5; "students in '
                'classroom 5B" -> classroom="5B". Returns an empty list when '
                "nothing matches - never invent students."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Filter by grade level, e.g. 5 for Grade 5.",
                    },
                    "classroom": {
                        "type": "string",
                        "description": "Filter by classroom code, e.g. '5A'.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Case-insensitive substring filter on the student name.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_student",
"description": (
                "Use to look up ONE specific student by exact name or id, e.g. "
                'for "Is Ahmed in Grade 5?". Provide EXACTLY ONE of '
                '"id" or "name". Use get_students when you need lists or counts.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "The student's numeric id.",
                    },
                    "name": {
                        "type": "string",
                        "description": "The student's full name, case-insensitive.",
                    },
                },
                "oneOf": [
                    {"required": ["id"]},
                    {"required": ["name"]},
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_teachers",
            "description": (
                "Use for ANY question about teachers (lists, counts, subjects). "
                "Optional grade/classroom filters. Example: 'Grade 5 teachers' -> "
                "grade=5."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Filter by grade the teacher teaches.",
                    },
                    "classroom": {
                        "type": "string",
                        "description": "Filter by classroom code, e.g. '5A'.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attendance",
            "description": (
                "Use for ANY question about attendance: present/absent/late "
                "status or counts. For ONE student pass studentId (look the "
                "student up first with get_student to learn their id). For a "
                "WHOLE grade pass grade (e.g. grade=5) to get every student's "
                "status in one call. Optional ISO date (YYYY-MM-DD); omit it "
                "for today - do not invent dates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "studentId": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "The student's numeric id (from get_student/get_students).",
                    },
                    "grade": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Fetch attendance for an entire grade at once.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional ISO date YYYY-MM-DD. Omit for today.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]

TOOL_REGISTRY: dict[str, dict[str, Any]] = {t["function"]["name"]: t for t in TOOLS}

# ---------------------------------------------------------------------------
# Role allowlist (app-level enforcement - Person 3's contract)
# ---------------------------------------------------------------------------

ALLOWED_TOOLS_BY_ROLE: dict[str, list[str]] = {
    "teacher": ["get_students", "get_student", "get_teachers", "get_attendance"],
    "admin": ["get_students", "get_student", "get_teachers", "get_attendance"],
}

DEFAULT_ROLE = "teacher"


def allowed_tools_for(role: str | None) -> list[str]:
    """Return the tool names a role may call. Unknown roles get NOTHING."""
    return ALLOWED_TOOLS_BY_ROLE.get(role or DEFAULT_ROLE, [])


def tool_names() -> list[str]:
    return list(TOOL_REGISTRY)