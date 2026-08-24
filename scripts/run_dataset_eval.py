"""Person 4 (P4) Test Dataset Evaluator.

Reads cases from tests/test_dataset.json and evaluates:
1. Tool Sequence: Did the AI call the right tool(s)?
2. Rejection Check: Did the AI refuse disallowed requests?
3. Fact Check: Does the answer contain the required facts?
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.config import create_llm, load_env
from api.agent_server import app as agent_app
from api.mock_api import app as mock_app

load_env()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATASET_FILE = REPO / "tests" / "test_dataset.json"
MOCK_PORT = 8097
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"


def ensure_mock_running(port: int = MOCK_PORT) -> str:
    mock_url = f"http://127.0.0.1:{port}"
    try:
        if httpx.get(f"{mock_url}/health", timeout=1).json().get("status") == "ok":
            return mock_url
    except Exception:
        pass
    config = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{mock_url}/health", timeout=1).json().get("status") == "ok":
                return mock_url
        except Exception:
            time.sleep(0.2)
    return mock_url


def normalize_text(text: str) -> str:
    """Normalize unicode spaces and hyphens for clean string comparisons."""
    return (
        text.replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("'", "")
        .replace("’", "")
        .lower()
    )


def evaluate_dataset() -> int:
    if not DATASET_FILE.exists():
        print(f"Error: {DATASET_FILE} not found.")
        return 1

    mock_url = ensure_mock_running()
    os.environ["MOCK_API_URL"] = mock_url
    agent_app.state.agent_llm = create_llm()

    cases = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    print(f"==================================================")
    print(f"  P4 Evaluation Benchmark — {len(cases)} Cases")
    print(f"==================================================\n")

    client = TestClient(agent_app)
    passed = 0
    failed = 0

    headers = {
        "X-Agent-School": "school-a",
        "X-Agent-Role": "teacher",
        "X-Agent-User": "teacher.ahmed@school-a.edu",
    }

    for case in cases:
        case_id = case["id"]
        category = case["category"]
        user_msg = case["user_message"]
        expected = case["expected"]

        resp = client.post("/chat", json={"message": user_msg}, headers=headers)
        if resp.status_code != 200:
            print(f"[{case_id}] FAIL — HTTP {resp.status_code}")
            failed += 1
            continue

        data = resp.json()
        answer = data.get("answer", "")
        steps = data.get("steps", [])
        called_tools = [s["tool"] for s in steps]

        expected_tools = [t["tool"] for t in expected.get("tool_sequence", [])]
        expected_rejection = expected.get("rejection", False)
        required_facts = expected.get("required_facts", [])

        # Check 1: Rejection
        rejection_passed = True
        if expected_rejection:
            rejection_passed = len(called_tools) == 0

        # Check 2: Tool Calling
        tools_passed = True
        for exp_tool in expected_tools:
            if exp_tool not in called_tools:
                tools_passed = False
                break

        # Check 3: Facts Check
        norm_answer = normalize_text(answer)
        facts_passed = True
        missing_facts = []
        for fact in required_facts:
            norm_fact = normalize_text(fact)
            if norm_fact == "0" or norm_fact == "no students":
                if not any(syn in norm_answer for syn in ["0", "zero", "no students", "none", "not recorded"]):
                    facts_passed = False
                    missing_facts.append(fact)
            elif norm_fact not in norm_answer:
                facts_passed = False
                missing_facts.append(fact)

        case_passed = rejection_passed and tools_passed and facts_passed

        status_str = "PASS" if case_passed else "FAIL"
        print(f"[{case_id}] [{category.upper()}] -> {status_str}")
        print(f"  Query        : \"{user_msg}\"")
        print(f"  Called Tools : {called_tools} (Expected: {expected_tools})")
        print(f"  AI Answer    : \"{answer.strip()}\"")
        if missing_facts:
            print(f"  ! Missing Facts: {missing_facts}")
        print("-" * 50)

        if case_passed:
            passed += 1
        else:
            failed += 1

    print(f"\n==================================================")
    print(f"  SUMMARY: {passed}/{len(cases)} PASS ({passed/len(cases)*100:.1f}%)")
    print(f"==================================================")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(evaluate_dataset())
