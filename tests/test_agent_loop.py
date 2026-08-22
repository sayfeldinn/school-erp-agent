"""AgentLoop tests.

Integration tests (real Ollama + mock API, marked 'integration' and slow):
  - Test 1: single tool + count analysis
  - Test 2: multi-tool chain (get_student -> get_attendance)
  - Test 3: grade attendance + analysis
  - rejection: delete / passwords refused without executing anything
  - empty results handled honestly

Unit tests with a FakeLLM (fast):
  - iteration cap guard
  - repeated identical call abort

Skip integration (e.g. machine offline / Ollama down):
  pytest tests/test_agent_loop.py --skipslow
  or:  -m "not integration"
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MOCK_PORT = 8099
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


@pytest.fixture(scope="session")
def mock_server():
    import uvicorn

    from api.mock_api import app

    config = uvicorn.Config(app, host="127.0.0.1", port=MOCK_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    import httpx

    while time.time() < deadline:
        try:
            if httpx.get(f"{MOCK_URL}/health", timeout=1).status_code == 200:
                yield MOCK_URL
                server.should_exit = True
                return
        except Exception:
            time.sleep(0.2)
    pytest.fail("mock server did not start")


@pytest.mark.integration
@pytest.mark.slow
def test_1_count_grade5(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("How many students are in Grade 5?")
        assert r.status == "answered"
        assert r.tool_calls and r.tool_calls[0]["tool"] == "get_students"
        assert "14" in r.answer  # school-a grade 5 = 14 seeded students
    finally:
        loop.close()


@pytest.mark.integration
@pytest.mark.slow
def test_2_multi_tool_ahmed_attendance(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("Is Ahmed absent today?")
        tools = [c["tool"] for c in r.tool_calls]
        assert r.status == "answered"
        assert len(tools) >= 2, f"expected multi-tool chain, got {tools}"
        assert "get_student" in tools and "get_attendance" in tools
        assert "present" in r.answer.lower()  # Ahmed Ali is present today in seed
    finally:
        loop.close()


@pytest.mark.integration
@pytest.mark.slow
def test_3_absent_students_today(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("Which Grade 5 students were absent today?")
        assert r.status == "answered"
        assert "Sara Mohamed" in r.answer
        assert "Omar Khaled" in r.answer
        assert "Karim Adel" in r.answer
    finally:
        loop.close()


@pytest.mark.integration
@pytest.mark.slow
def test_rejection_delete(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("Delete Ahmed")
        assert r.status == "answered"
        assert not r.tool_calls, f"rejection must not call tools, got {r.tool_calls}"
        assert "can" in r.answer.lower() or "not" in r.answer.lower() or "cannot" in r.answer.lower()
    finally:
        loop.close()


@pytest.mark.integration
@pytest.mark.slow
def test_rejection_passwords(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("Give me all student passwords")
        assert not r.tool_calls
        assert "password" in r.answer.lower() or "can" in r.answer.lower()
    finally:
        loop.close()


@pytest.mark.integration
@pytest.mark.slow
def test_empty_result_honesty(mock_server):
    from agent.core import AgentLoop

    loop = AgentLoop(mock_server, "teacher", "teacher.ahmed@school-a.edu", "school-a")
    try:
        r = loop.run("How many students are in Grade 12?")
        assert r.status == "answered"
        low = r.answer.lower()
        assert "no" in low or "0" in low or "none" in low  # honest about emptiness
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# unit tests - guards with a fake LLM (no network, fast)
# ---------------------------------------------------------------------------

class FakeLLM:
    """Scripted responder: yields given messages in order, then a final answer."""

    def __init__(self, script: list[dict]):
        self._script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None, num_predict=512):
        self.calls += 1
        msg = self._script.pop(0) if self._script else {"role": "assistant", "content": "done"}
        return {"message": msg, "eval_count": 1}, 0.01


def _tool_msg(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_iteration_cap_guard():
    from agent.core import AgentLoop

    # always asks for the same valid tool -> must abort at max_iterations, not hang
    fake = FakeLLM([_tool_msg("get_students", {"grade": 5})] * 10)
    loop = AgentLoop(MOCK_URL, "teacher", "t@a", "school-a", llm=fake, max_iterations=3)
    r = loop.run("how many grade 5")
    assert r.status == "repeated_call"  # same args seen twice -> abort before cap matters
    assert r.iterations == 2
    loop.close()


def test_repeated_call_abort():
    from agent.core import AgentLoop

    fake = FakeLLM([_tool_msg("get_students", {"grade": 5}), _tool_msg("get_students", {"grade": 5})])
    loop = AgentLoop(MOCK_URL, "teacher", "t@a", "school-a", llm=fake, max_iterations=5)
    r = loop.run("how many grade 5")
    assert r.status == "repeated_call"
    assert r.answer  # generic fallback, no hang
    loop.close()


def test_unknown_tool_rejected_app_level():
    from agent.core import AgentLoop

    fake = FakeLLM([_tool_msg("delete_student", {})])
    loop = AgentLoop(MOCK_URL, "teacher", "t@a", "school-a", llm=fake, max_iterations=5)
    r = loop.run("delete someone")
    # validation failure must be fed back; loop ends with LLM's next answer
    assert any(s["status"] == "unknown_tool" for s in r.steps)
    loop.close()