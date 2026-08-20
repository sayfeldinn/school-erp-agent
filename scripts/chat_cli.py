"""Interactive terminal chat for the agent (demo surface).

Starts the mock API automatically if it isn't already running, then opens
a REPL. Every step of the agent loop is printed so you SEE the real data flow.

Usage:  python scripts/chat_cli.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MOCK_URL = "http://127.0.0.1:8001"


def ensure_mock_running() -> None:
    try:
        import httpx

        if httpx.get(f"{MOCK_URL}/health", timeout=1.5).status_code == 200:
            return
    except Exception:
        pass
    print("mock API not running - starting it in-process...")
    import uvicorn

    config = uvicorn.Config("api.mock_api:app", host="127.0.0.1", port=8001, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()


def main() -> None:
    ensure_mock_running()
    import time

    from agent.core import AgentLoop

    loop = AgentLoop(
        mock_base_url=MOCK_URL,
        role="teacher",
        user="teacher.ahmed@school-a.edu",
        school="school-a",
    )
    history: list[dict] = []

    print("School ERP agent (qwen3:8b, native tool calling). 'exit' to quit.\n")
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