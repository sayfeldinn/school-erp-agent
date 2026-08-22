# Architecture (FROZEN v2 — aligns with plan.md §5 + §9)

This is the reference the whole team uses. The agent is graded; Flutter/API/data are tools.

## 1. Data flow

```
User (Chat UI / CLI / curl)
        |
        v
FastAPI Agent Server  (port 8000, sessions, CORS)   — api/agent_server.py
        |  X-Agent-School / Role / User  (bound to session)
        v
LLM — Ollama qwen3:8b native tools (think:false, temp 0, 8192 ctx)
      or any OpenAI-compatible backend via .env      — agent/llm_client.py + agent/config.py
        |  decides tool + params
        v
Validation Layer (app-level, never the model)        — agent/executor.py + agent/tools.py
  1. Tool exists?  2. Role allowlist  3. JSON Schema bounds  4. Normalize  5. Identity
        |  HTTP GET + identity headers
        v
Mock API — JSON seed (dumb data layer)               — api/mock_api.py (port 8001)
        |  tool result
        v
UNTRUSTED data block  [BEGIN TOOL RESULT ... END]    — agent/executor.py + agent/core.py
        |
        v
LLM analyzes -> final answer (or next tool call)
```

Two boundaries, intentionally separated:
- **AI decides** what it *wants*.
- **Application enforces** what is *allowed*. The Mock API stays a dumb data layer; the only enforcement gate is the executor + server (see security-model.md).

## 2. Sequence — multi-tool question

```
User                      Agent Server                  LLM                     Executor                Mock API
 |  POST /chat {message}        |                         |                         |                      |
 |----------------------------->|  AgentLoop.run()        |                         |                      |
 |                             |------------------------>|  chat(messages, tools)  |                      |
 |                             |                         |--tool_call: get_student-->|                    |
 |                             |                         |  {name:"Ahmed"}          |--GET /students?name |
 |                             |                         |                         |--------------------->|
 |                             |                         |                         |<--{id:1, grade:5}----|
 |                             |  tool result as         |<-- [UNTRUSTED DATA] -----|                      |
 |                             |  role:"tool" block      |                         |                      |
 |                             |                         |--tool_call: get_attendance {studentId:1}       |
 |                             |                         |                         |--GET /attendance?... |
 |                             |                         |                         |<--{status:present}---|
 |                             |                         |<-- [UNTRUSTED DATA] -----|                      |
 |                             |                         |  final answer: "Ahmed is present today."        |
 |                             |<------------------------|                         |                      |
 |<-- {answer, steps, status}--|                         |                         |                      |
```

Guards (all in `agent/core.py`, never the model): max **5 iterations**, **repeated identical call abort**, unknown tool → hard reject, empty → honest "no records", per-call timeouts, `LLMError` → typed user message.

Session memory: `POST /chat {session_id}` — server keeps last **8 turns** per session (in-memory, max 100 sessions, oldest evicted). Identity is bound on session creation; switching `X-Agent-School/Role/User` mid-session → `403 identity_mismatch`.

## 3. Modules

| Path | Owns |
|---|---|
| `agent/tools.py` | 4 strict tool schemas (OpenAI-style) + `ALLOWED_TOOLS_BY_ROLE` + `allowed_tools_for()` |
| `agent/executor.py` | `ToolExecutor`: validate (exist/allowlist/schema) → normalize → HTTP GET with `X-Agent-*` → `ToolResult` |
| `agent/core.py` | `AgentLoop`: decide→validate→execute→feed back→answer loop, `max_iterations=5`, `load_system_prompt()` |
| `agent/llm_client.py` | `OllamaClient` (native `/api/chat`) + `OpenAICompatClient` (Groq/LM Studio), schema shim, `Retry-After` on 429/5xx |
| `agent/config.py` | `load_env()` + `resolve_config()` + `create_llm()` + `describe()` — one `.env` switches backends |
| `agent/prompts/system.json` | System prompt as **data** (v1) — edit without touching code; P4 iterates here |
| `api/mock_api.py` | Contract-faithful mock of `docs/api-contract.md` (port 8001), `TRUSTED_SCHOOL_BY_USER` + `_school_scope()` on all endpoints |
| `api/agent_server.py` | `POST /chat` + `GET /health`, CORS, sessions, error shapes — contract `docs/agent-server-contract.md` |
| `data/seed.json` | Frozen v1 (26 students, 2 schools, grades 5+6, 15 days, poisoned row id 23, edge cases) |
| `scripts/chat_cli.py` | Interactive demo with per-step trace + `--check` health gate |
| `scripts/calibrate.py` | Bench + native vs JSON accuracy harness |
| `scripts/smoke_eval.py` | **Phase 6** local smoke eval (8 cases × 2 surfaces) — template for P4's full dataset |
| `chat_ui/` | Flutter chat UI (`lib/main.dart` talks to `:8000` with identity headers) |

## 4. The 4 tools (only 4 — by design)

| Tool | Params | Use |
|---|---|---|
| `get_students` | `grade` 1–12?, `classroom`?, `name`? | lists, counts, searches |
| `get_student` | `id` **or** `name` (exactly one) | one specific student |
| `get_teachers` | `grade`?, `classroom`? | teacher questions |
| `get_attendance` | `studentId` **or** `grade` + `date`? | single-student **or** whole-grade status (ONE call) |

All schemas: `additionalProperties: false`, numeric bounds, `oneOf` for `get_student`. Descriptions guide tool selection; the app enforces it.

## 5. LLM backends (one `.env` switches all)

| Provider | `.env` | Default model |
|---|---|---|
| `ollama` (default) | `LLM_PROVIDER=ollama` | `qwen3:8b` |
| `groq` / `lm studio` / `jan` | `LLM_PROVIDER=openai` + `LLM_BASE_URL` + `LLM_API_KEY` | `llama-3.3-70b-versatile` |

Switch requires no code change — `agent/config.py` + `agent/llm_client.py` handle tool-schema shimming and 429/5xx retries.

## 6. Failure modes

- **No provider reachable** → `conftest.py` skips every `integration` test; `chat_cli --check` and `POST /chat` return `status:error` with a typed `kind` (`rate_limited`/`auth`/`not_found`/`unreachable`).
- **Tool validation fails** → `ToolResult(status=kind, http_status=400)` fed back as untrusted data; loop continues.
- **HTTP 403/4xx** → `forbidden`/`error` result block; LLM is told "outside your access scope".
- **Loop spins** → `repeated_call` abort after the same tool+params twice.
