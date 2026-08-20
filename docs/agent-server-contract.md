# Agent Server Contract (v1 - FROZEN, Phase 4)

The HTTP surface the Flutter UI (P2) calls. Server: FastAPI on port 8000,
module `api/agent_server.py`. Endpoints follow the same conventions as
`docs/api-contract.md` (error shape `{"error": {"code", "detail"}}`, identity via
`X-Agent-*` headers).

## Endpoints

### `GET /health`

```json
{ "status": "ok", "provider": "OllamaClient", "model": "qwen3:8b" }
```

### `POST /chat`

**Headers**

| Header | Required | Meaning |
|---|---|---|
| `X-Agent-School` | yes | 403 `missing_identity` when absent |
| `X-Agent-Role` | no | defaults `teacher`; determines offered tools |
| `X-Agent-User` | no | defaults `teacher.ahmed@school-a.edu` |

**Body**

```json
{ "message": "Is Ahmed absent today?", "session_id": "optional-any-string" }
```

`message`: non-empty string, max 4000 chars (whitespace-only → 400).

`session_id`: optional; when omitted the server creates and returns one.
Identity headers are bound to the session on creation - a session that later
changes school/role/user returns 403 `identity_mismatch`.

**Response `200`**

```json
{
  "session_id": "...",
  "answer": "Ahmed is present today.",
  "status": "answered",
  "iterations": 3,
  "steps": [
    { "iter": 0, "tool": "get_student", "params": {"name": "Ahmed"},
      "status": "ok", "http": 200 }
  ],
  "error": null,
  "elapsed_s": 12.5
}
```

- `status`: `answered` | `max_iterations` | `repeated_call` | `error`
- `steps`: one entry per tool call `{iter, tool, params, status, http, detail?}`
- `error`: `null` normally. When `status == "error"`, an object
  `{ "kind", "detail" }` with kind:
  - `rate_limited` - free-tier quota (429)
  - `auth` - bad API key
  - `not_found` - bad model or base URL
  - `bad_request` - schema/parameter rejection
  - `unreachable` - backend down / timeout

**Errors (transport level)**

| Status | Code | Meaning |
|---|---|---|
| `400` | `invalid_params` | body invalid, message missing/empty/too long |
| `403` | `missing_identity` | no `X-Agent-School` |
| `403` | `identity_mismatch` | headers changed mid-session |
| `500` | `internal_error` | unexpected agent failure (no details leaked) |

## Session semantics

- In-memory, per-`session_id` message history (capped at 8 turns, matching the
  loop's context window)
- Server keeps at most 100 sessions; oldest is evicted when full
- History is `[{role, content}]` turns - the loop re-derives system prompt and
  identity on every request
- Sessions are not persisted - process restart clears them

## Run

```powershell
# terminal 1 - mock API
.\.venv\Scripts\python.exe -m uvicorn api.mock_api:app --port 8001 --host 127.0.0.1
# terminal 2 - agent server (uses .env LLM config)
.\.venv\Scripts\python.exe -m uvicorn api.agent_server:app --port 8000 --host 127.0.0.1
```

`MOCK_API_URL` env var overrides the default mock base URL.