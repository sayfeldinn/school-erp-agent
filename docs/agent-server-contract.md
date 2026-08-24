# Agent Server Contract — current implementation

The public FastAPI service runs on port 8000 from `api/agent_server.py`. It
serves authentication routes and the authenticated agent chat surface. Handled
contract errors use the envelope
`{"error": {"code": "...", "detail": "..."}}`.
HTTPExceptions raised by the authentication routes use `code: "http_error"`
with the stable auth detail, while request-schema validation failures use
`code: "invalid_params"`. For example:

```json
{
  "error": {
    "code": "http_error",
    "detail": "authentication_failed"
  }
}
```

## Public authentication

### `POST /auth/register`

Request:

```json
{
  "full_name": "Ms. Nour Hassan",
  "password": "a-password-between-15-and-128-characters",
  "confirm_password": "a-password-between-15-and-128-characters",
  "school_code": "<demo-enrollment-code>"
}
```

The request rejects extra fields and enforces a password length of 15–128
characters. The server derives the roster person, role,
tenant, and email from `full_name` plus the static demo code. There is no public
admin registration.

Response `201`:

```json
{ "status": "created", "email": "ms.nour.hassan@school-a.edu" }
```

Registration does not return an access token and does not log the user in.
Registration failures return `400` with a stable detail such as
`registration_failed`, `password_policy_failed`, or
`password_confirmation_failed`; roster and tenant internals are not disclosed.
The static codes are demo-grade roster binding, not production identity proof.

### `POST /auth/login`

Request:

```json
{ "email": "ms.nour.hassan@school-a.edu", "password": "<password>" }
```

Response `200`:

```json
{ "access_token": "<opaque-token>", "token_type": "bearer" }
```

Unknown account, wrong password, disabled account, and disabled tenant all
return the same `401 authentication_failed` detail. Throttled attempts return
`429 too_many_attempts`.

### `GET /auth/me`

Requires `Authorization: Bearer <token>` and returns the server-resolved
identity:

```json
{
  "email": "ms.nour.hassan@school-a.edu",
  "role": "teacher",
  "tenant_id": "school-a"
}
```

Missing, malformed, unknown, revoked, or expired tokens return
`401 authentication_required`. Public `X-Agent-*` headers do not alter this
response.

### `POST /auth/logout`

Requires `Authorization: Bearer <token>`, revokes that authentication session,
and returns:

```json
{ "status": "ok" }
```

The revoked token is rejected by later authenticated requests.

## Agent endpoints

### `GET /health`

This development health endpoint does not require authentication. The provider
class and model values reflect the current LLM configuration; for example:

```json
{ "status": "ok", "provider": "OllamaClient", "model": "qwen3:8b" }
```

### `POST /chat`

Required headers:

| Header | Meaning |
|---|---|
| `Authorization: Bearer <token>` | resolves the public caller's email, role, and tenant |
| `Content-Type: application/json` | JSON request body |

`X-Agent-User`, `X-Agent-Role`, and `X-Agent-School` are **not** public
authentication headers. Supplying them without a valid Bearer token still
returns `401`; supplying forged values with a valid token does not override the
server-resolved identity.

Body:

```json
{ "message": "Is Ahmed absent today?", "session_id": "optional-chat-history-id" }
```

- `message` is a non-empty string with a maximum length of 4000 characters;
  whitespace-only input returns `400 invalid_params`.
- `session_id` is optional. When omitted or empty, the server generates one.
  It identifies chat history, not authentication.

Response `200`:

```json
{
  "session_id": "...",
  "answer": "Ahmed is present today.",
  "status": "answered",
  "iterations": 3,
  "steps": [
    {
      "iter": 0,
      "tool": "get_student",
      "params": {"name": "Ahmed"},
      "status": "ok",
      "http": 200
    }
  ],
  "error": null,
  "elapsed_s": 12.5
}
```

- `status` is `answered`, `max_iterations`, `repeated_call`, or `error`.
- `steps` contains one trace entry per proposed tool call. Failed entries may
  contain a server-owned `detail`.
- `error` is normally `null`. For LLM-provider failures it is an object with
  `kind` equal to `rate_limited`, `auth`, `not_found`, `bad_request`, or
  `unreachable`, plus a stable `detail`.

Transport-level errors:

| Status | Code/detail | Meaning |
|---|---|---|
| `400` | `invalid_params` | invalid request body or empty/oversized message |
| `401` | `authentication_required` | no valid Bearer authentication session |
| `403` | `identity_mismatch` | another Bearer-resolved identity reused the same chat `session_id` |
| `500` | `internal_error` | unexpected agent failure; exception details are not returned |

The current `identity_mismatch` response detail retains the legacy phrase
"different identity headers". The actual comparison is between server-resolved
Bearer identities; public identity headers are ignored.

## Authentication and chat-session semantics

Authentication sessions are persisted in the SQLite database configured by
`AUTH_DB_PATH` (default `runtime/security.db`). Raw tokens are not stored; their
hashes are persisted with the user id, issued role/tenant, timestamps, expiry,
and revocation metadata. Sessions expire after 15 minutes idle or 8 hours
absolute and may be revoked.

Chat history is held separately in process memory. Each `session_id` is bound to
the Bearer-resolved identity, retains at most eight user/assistant turn pairs,
and is one of at most 100 chat sessions. `AgentLoop` consumes only the last
eight history messages (four complete pairs) for a run. The least recently
active chat session is evicted when the limit is reached; all chat history is
cleared on process restart.

## Run

```powershell
# terminal 1 - loopback Mock ERP
.\.venv\Scripts\python.exe -m uvicorn api.mock_api:app --port 8001 --host 127.0.0.1

# terminal 2 - public agent/auth server
.\.venv\Scripts\python.exe -m uvicorn api.agent_server:app --port 8000 --host 127.0.0.1
```

`MOCK_API_URL` overrides the default Mock ERP URL. `AUTH_DB_PATH` overrides the
default SQLite path. `python -m security.auth_admin_cli` can provision tenants
and users directly, including admins. The public self-registration route is
separate: after its tenant is provisioned, it accepts only roster-bound student
and teacher enrollment codes and never creates an admin. With the current Mock
ERP, a CLI admin email that is not in its static trusted school map can
authenticate, but school-scoped Mock calls return a downstream `403`. The
executor turns that into a sanitized scope-denial tool result; `/chat` may still
return a normal `200` agent response.
