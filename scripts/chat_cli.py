"""Interactive terminal chat for the agent (demo surface).

Starts the mock API automatically if it isn't already running, then opens
a REPL. Every step of the agent loop is printed so you SEE the real data flow.

Usage:
  python scripts/chat_cli.py              # chat with the configured LLM
  python scripts/chat_cli.py --check      # verify the LLM provider works
  python scripts/chat_cli.py --mock-port 8099   # override mock API port
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.config import create_llm, load_env, resolve_config
from agent.llm_client import LLMError

DEFAULT_MOCK_PORT = 8001


def ensure_mock_running(port: int) -> str:
    import httpx

    mock_url = f"http://127.0.0.1:{port}"
    try:
        health = httpx.get(f"{mock_url}/health", timeout=1.5).json()
        if health.get("status") == "ok":
            return mock_url
    except Exception:
        pass
    print("mock API not running - starting it in-process...")
    import uvicorn

    config = uvicorn.Config("api.mock_api:app", host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{mock_url}/health", timeout=1).json().get("status") == "ok":
                return mock_url
        except Exception:
            time.sleep(0.2)
    print("ERROR: mock API did not become healthy on port", port)
    sys.exit(1)


def run_check() -> int:
    """Verify the configured provider: config -> direct chat -> full tool loop."""
    cfg = resolve_config()
    llm = create_llm()
    key = f" key={cfg['api_key'][:7]}..." if cfg["api_key"] else ""
    print(f"provider : {cfg['provider']}")
    print(f"model    : {cfg['model']}")
    print(f"base_url : {cfg['base_url']}")
    print(f"max_tokens: {cfg['max_tokens']}{key}\n")

    print("step 1/2  direct chat probe...")
    try:
        data, secs = llm.chat([{"role": "user", "content": "Reply with exactly: OK"}], num_predict=16)
        reply = (data.get("message", {}).get("content") or "").strip()
        print(f"  -> {reply[:60] or '<empty>'}  ({secs:.1f}s)")
        if "OK" not in reply.upper():
            print("WARNING: probe replied, but not 'OK' - model may be misconfigured.")
    except LLMError as exc:
        print(f"  FAILED: {exc}  (status={exc.status})")
        return 1

    print("step 2/2  full tool loop ('How many students are in Grade 5?')...")
    mock = ensure_mock_running(DEFAULT_MOCK_PORT)
    from agent.core import AgentLoop

    loop = AgentLoop(mock, "teacher", "teacher.ahmed@school-a.edu", "school-a", llm=llm)
    try:
        r = loop.run("How many students are in Grade 5?")
        for s in r.steps:
            kind = f"{s['tool']}({s['params']})".replace("'", "")
            print(f"  -> {kind}  HTTP {s['http']}  [{s['status']}]")
        print(f"  -> {r.answer}")
        if r.status != "answered":
            print(f"  LOOP UNUSUAL: status={r.status}")
            return 1
    finally:
        loop.close()
    print("\nOK - provider works.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="School ERP agent demo CLI")
    parser.add_argument("--check", action="store_true", help="verify the LLM provider and exit")
    parser.add_argument("--mock-port", type=int, default=DEFAULT_MOCK_PORT, help="mock API port")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_env()
    if args.check:
        sys.exit(run_check())

    cfg = resolve_config()
    llm = create_llm()
    mock = ensure_mock_running(args.mock_port)

    from agent.core import AgentLoop

    loop = AgentLoop(
        mock_base_url=mock,
        role="teacher",
        user="teacher.ahmed@school-a.edu",
        school="school-a",
        llm=llm,
    )
    history: list[dict] = []

    print(f"School ERP agent ({cfg['provider']}: {cfg['model']}). 'exit' to quit.\n")
    try:
        while True:
            user_msg = input("You > ").strip()
            if user_msg.lower() in {"exit", "quit"}:
                break

            t0 = time.perf_counter()
            result = loop.run(user_msg, history)
            elapsed = time.perf_counter() - t0

            if result.steps:
                print("  trace:")
                for s in result.steps:
                    kind = f"{s['tool']}({s['params']})".replace("'", "")
                    detail = f" - {s['detail']}" if s.get("detail") else ""
                    print(f"    -> {kind}  HTTP {s['http']}  [{s['status']}]{detail}")
            print(f"Agent > {result.answer}")
            print(f"  [{result.status}, {result.iterations} iter, {elapsed:.0f}s]\n")

            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": result.answer})
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()