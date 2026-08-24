# Architecture — current implementation

## 1. End-to-end data flow

```text
Public client (Flutter / curl)
        |
        |  POST /auth/login {email, password}
        v
FastAPI Agent Server :8000                          api/agent_server.py
        |
        |  opaque access token
        v
Public client
        |
        |  POST /chat + Authorization: Bearer <token>
        v
Authentication/session resolution                  security/auth_*.py
        |  server-derived email + role + tenant
        v
AgentLoop offers only role-allowed tools            agent/core.py + agent/tools.py
        |
        v
LLM proposes a tool name and arguments              agent/llm_client.py
        |
        v
Deterministic executor checks                       agent/executor.py
  1. registry  2. role allowlist  3. strict schema
  4. fixed identity/resource context  5. fixed endpoint mapping
        |
        |  HTTP GET + internal server-derived X-Agent-* context
        v
Mock ERP :8001 re-checks tenant/resource/self scope api/mock_api.py
        |
        |  authorized result or sanitized failure
        v
UNTRUSTED tool-result block -> LLM -> final answer
```

**The LLM proposes. Deterministic security controls decide. The API enforces.**

This guarantees the enforced tool and data boundary, not the truth of every
word in final model-generated text. There is no general deterministic grounding
or response-redaction post-check.

The two HTTP boundaries have different trust models:

- Public clients authenticate to port 8000 with a Bearer token. Public
  `X-Agent-User`, `X-Agent-Role`, and `X-Agent-School` values neither
  authenticate nor override that identity.
- The executor sends those `X-Agent-*` names only as internal context to the
  loopback Mock ERP. Port 8001 re-checks scope but does not implement signed or
  production service-to-service authentication.

## 2. Authenticated multi-tool sequence

```text
Client               Agent Server             LLM              Executor             Mock ERP
  | POST /auth/login      |                     |                  |                     |
  |---------------------->| verify password     |                  |                     |
  |<-- Bearer token ------| create auth session |                  |                     |
  |                       |                     |                  |                     |
  | POST /chat            |                     |                  |                     |
  | Authorization: Bearer |                     |                  |                     |
  |---------------------->| resolve identity    |                  |                     |
  |                       |-- AgentLoop.run ---->| tools for role   |                     |
  |                       |                     |-- get_student --->| registry/role/schema|
  |                       |                     |                  |-- GET /students?name|
  |                       |                     |                  |  + internal identity|
  |                       |                     |                  |-------------------->|
  |                       |                     |                  |<-- scoped row -------|
  |                       |                     |<-- untrusted data-|                     |
  |                       |                     |-- get_attendance->| validate + fixed map|
  |                       |                     |                  |-- GET /attendance -->|
  |                       |                     |<-- untrusted data-|<-- scoped record ----|
  |                       |<-- final answer ----|                  |                     |
  |<-- answer + trace ----|                     |                  |                     |
```

For a student, the same sequence offers only `get_my_profile` and
`get_my_attendance`. Those parameterless calls map to `/students/me` and
`/attendance/me`, where the Mock ERP resolves the roster id from the internal
student identity and re-checks the school.

## 3. Deterministic enforcement and loop guards

`AgentLoop._offered_tools()` filters the registry before the LLM request. The
executor still re-checks the proposed tool, so a compromised or malformed model
cannot bypass the allowlist by naming a hidden tool.

Validation is performed on the raw arguments before normalization. As a result,
numeric strings do not satisfy integer schemas. Unexpected fields are rejected,
and `get_student` and `get_attendance` enforce their exclusive selectors with
`oneOf`. After successful validation, supported relative date strings are
translated and the tool maps to a hard-coded path and query-key set.

Loop guards include a five-iteration maximum, abort after the same tool and
arguments are executed twice, a 5-second default ERP HTTP timeout, and typed LLM
provider failures. Empty and failed tool results return to the LLM as marked
untrusted data. Unknown or role-restricted tools and invalid schemas never reach
ERP HTTP.

Unless an explicit `system_prompt` override is supplied, prompt selection
follows the authenticated role: `student` uses `agent/prompts/student.json`,
`teacher` uses `agent/prompts/teacher.json`, and admin or other roles use
`agent/prompts/system.json`. An explicit override still wins. Prompt text guides
model behavior but is not part of the authorization decision; the deterministic
registry, executor, and ERP boundary remain authoritative. See
`docs/security-model.md` for the enforced contract and known limitations.

## 4. Modules

| Path | Responsibility |
|---|---|
| `security/auth_store.py` | SQLite tenants and users; password-hash persistence |
| `security/auth_passwords.py` | Argon2id password hashing and verification |
| `security/auth_sessions.py` | Opaque Bearer session issuance, hashing, expiry, and revocation |
| `security/auth_login.py` | Credential verification and session creation |
| `security/auth_context.py` | Bearer parsing and server-side identity resolution |
| `security/auth_registration.py` | Demo enrollment-code and roster-bound registration |
| `security/auth_routes.py` | `/auth/register`, `/auth/login`, `/auth/me`, `/auth/logout` |
| `agent/tools.py` | Six strict schemas plus the role allowlist |
| `agent/executor.py` | Registry/role/schema checks, normalization, fixed HTTP mapping, internal identity context, sanitized results |
| `agent/core.py` | Role-filtered tool offering and decide/execute/repeat loop |
| `agent/prompts/student.json` | Student self-service and confidentiality guidance |
| `agent/prompts/teacher.json` | Teacher role-aligned model guidance |
| `agent/prompts/system.json` | Generic admin/other model guidance |
| `agent/llm_client.py` | Ollama and OpenAI-compatible provider adapters |
| `agent/config.py` | Provider configuration; diagnostic API-key presence only |
| `api/agent_server.py` | Public authenticated agent/auth API and in-memory chat history |
| `api/mock_api.py` | Loopback demo ERP data plus tenant/resource/self-scope re-checks |
| `chat_ui/lib/main.dart` | Registration, login, Bearer-authenticated chat, and logout UI |

## 5. Role-specific tool surface

| Tool | Parameters | Student | Teacher | Admin |
|---|---|:---:|:---:|:---:|
| `get_my_profile` | none | yes | no | no |
| `get_my_attendance` | none | yes | no | no |
| `get_students` | optional `grade`, `classroom`, `name` | no | yes | yes |
| `get_student` | exactly one of `id`, `name` | no | yes | yes |
| `get_attendance` | exactly one of `studentId`, `grade`; optional `date` | no | yes | yes |
| `get_teachers` | optional `grade`, `classroom` | no | no | yes |

This table is the executor authorization surface. The local Mock ERP separately
recognizes a static roster-teacher trusted-user map; an admin provisioned with an
unmapped email authenticates and receives these tools but gets a sanitized `403`
from school-scoped Mock calls.

There are no delete, salary, credential, arbitrary-URL, all-school, or role
administration tools in the registry.

## 6. State and persistence

Authentication data is persisted in the SQLite file configured by
`AUTH_DB_PATH` (default `runtime/security.db`). Passwords and raw session tokens
are not stored there; password/token hashes are persisted alongside tenant,
user, issued-authority, timestamp, expiry, and revocation metadata.

The Bearer authentication session has a 15-minute idle and 8-hour absolute
timeout. `/auth/logout` revokes it. Separately, `/chat` accepts an optional
`session_id` for in-memory conversation history. The server keeps at most 100
chat sessions, stores up to eight user/assistant turn pairs in each, and binds
each id to the Bearer-resolved identity. `AgentLoop` consumes only the last
eight stored messages (four complete pairs) on a run. Chat history does not
survive process restart.

## 7. Failure modes

- Missing or invalid Bearer authentication on `/chat` returns `401` before the
  LLM or chat session store is used.
- Reusing a chat `session_id` with a different authenticated identity returns
  `403 identity_mismatch`.
- Unknown, disallowed, or schema-invalid tool calls return a fail-closed tool
  result without ERP HTTP.
- A Mock ERP `403` becomes a sanitized scope denial. Other downstream error
  bodies are replaced with stable server-owned text before the LLM sees them.
- A repeated call or iteration cap ends with a generic retry/rephrase response.
- LLM provider failures become typed agent errors; unexpected agent failures
  become `500 internal_error` without exception details.
