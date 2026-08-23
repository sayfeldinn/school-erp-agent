"""Phase 4 - agent server with sessions (port 8000).

The HTTP surface the Flutter UI (P2) calls. Each POST /chat runs the agent
loop (question -> tool -> mock ERP API -> answer) with per-session message
history and per-request identity headers.

Contract: docs/agent-server-contract.md (frozen).

Run:
  python -m uvicorn api.agent_server:app --port 8000 --host 127.0.0.1
(requires the mock API on MOCK_API_URL, default http://127.0.0.1:8001)
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent.config import create_llm, load_env
from agent.core import AgentLoop
from agent.llm_client import LLMError
from security.auth_context import resolve_authenticated_identity
from security.auth_routes import create_auth_router

DEFAULT_MOCK_URL = "http://127.0.0.1:8001"
DEFAULT_ROLE = "teacher"
DEFAULT_USER = "teacher.ahmed@school-a.edu"
MAX_HISTORY_TURNS = 8  # matches AgentLoop's history[-8:] window
MAX_SESSIONS = 100

app = FastAPI(title="School ERP agent server", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev: Flutter localhost; tighten before production
    allow_methods=["*"],
    allow_headers=["*"],
)

# LLM override for tests (app.state.agent_llm). None = configured provider.
app.state.agent_llm = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


# ---------------------------------------------------------------------------
# sessions (in-memory). History only - identity is bound per request below.
# ---------------------------------------------------------------------------

class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self.max_sessions = max_sessions
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str, identity: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
        """Return (sid, history). Creates or reuses the session.

        Identity is bound on creation: a session that switches school/role/user
        is rejected (app-level guard against privilege escalation).
        """
        with self._lock:
            sess = self._store.get(session_id)
            if sess is None:
                if len(self._store) >= self.max_sessions:
                    oldest = min(self._store, key=lambda k: self._store[k]["last"])
                    del self._store[oldest]
                sess = {"identity": identity, "history": [], "created": time.time(), "last": time.time()}
                self._store[session_id] = sess
            if sess["identity"] != identity:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "identity_mismatch", "detail": "session was created with different identity headers"},
                )
            sess["last"] = time.time()
            return session_id, list(sess["history"])

    def push(self, session_id: str, user_message: str, assistant_answer: str) -> None:
        with self._lock:
            sess = self._store.get(session_id)
            if sess is None:
                return
            sess["history"].append({"role": "user", "content": user_message})
            sess["history"].append({"role": "assistant", "content": assistant_answer})
            sess["history"] = sess["history"][-2 * MAX_HISTORY_TURNS:]
            sess["last"] = time.time()


store = SessionStore()


# ---------------------------------------------------------------------------
# error shapes (mirror docs/agent-server-contract.md)
# ---------------------------------------------------------------------------

def _error(code: str, detail: str) -> dict[str, Any]:
    return {"error": {"code": code, "detail": detail}}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    detail = (
        "registration_failed"
        if request.url.path == "/auth/register"
        else "request body is invalid: message must be a non-empty string"
    )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error("invalid_params", detail),
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": body})


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    llm = app.state.agent_llm or create_llm()
    return {"status": "ok", "provider": llm.__class__.__name__, "model": llm.model}


def _identity(school: str | None, role: str | None, user: str | None) -> dict[str, str]:
    if not school:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "missing_identity", "detail": "X-Agent-School header is required"},
        )
    return {
        "school": school.strip(),
        "role": (role or DEFAULT_ROLE).strip() or DEFAULT_ROLE,
        "user": (user or DEFAULT_USER).strip() or DEFAULT_USER,
    }


def _run_loop(message: str, identity: dict[str, str], history: list[dict[str, str]]) -> tuple[dict[str, Any], float]:
    """Run the agent loop once. Returns the response payload prefix + elapsed."""
    llm = app.state.agent_llm  # None -> real provider
    loop = AgentLoop(
        mock_base_url=os.getenv("MOCK_API_URL", DEFAULT_MOCK_URL),
        role=identity["role"],
        user=identity["user"],
        school=identity["school"],
        llm=llm or create_llm(),
    )
    try:
        t0 = time.perf_counter()
        result = loop.run(message, history)
        elapsed = time.perf_counter() - t0
    finally:
        loop.close()

    payload = {
        "answer": result.answer,
        "status": result.status,
        "iterations": result.iterations,
        "steps": result.steps,
        "elapsed_s": round(elapsed, 2),
    }
    if result.status == "error":
        payload["error"] = {
            "kind": "rate_limited" if "rate-limited" in result.answer
            else "auth" if "API key" in result.answer
            else "not_found" if "not found" in result.answer
            else "bad_request" if "rejected the request" in result.answer
            else "unreachable",
            "detail": result.answer,
        }
    else:
        payload["error"] = None
    return payload, elapsed


@app.post("/chat")
def chat(
    body: ChatRequest,
    x_agent_school: str | None = Header(default=None, alias="X-Agent-School"),
    x_agent_role: str | None = Header(default=None, alias="X-Agent-Role"),
    x_agent_user: str | None = Header(default=None, alias="X-Agent-User"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    message = body.message.strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_params", "detail": "message must be a non-empty string"},
        )
    identity = resolve_authenticated_identity(authorization, AUTH_DB_PATH)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authentication_required",
                "detail": "authentication is required",
            },
        )
    sid = body.session_id or uuid.uuid4().hex
    sid, history = store.get(sid, identity)
    try:
        payload, _elapsed = _run_loop(message, identity, history)
    except Exception as exc:  # unexpected - never leak details
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "internal_error", "detail": "agent run failed unexpectedly"},
        ) from exc
    store.push(sid, message, payload["answer"])
    return {"session_id": sid, **payload}


load_env()
AUTH_DB_PATH = os.getenv("AUTH_DB_PATH", "runtime/security.db")
app.include_router(create_auth_router(AUTH_DB_PATH))
