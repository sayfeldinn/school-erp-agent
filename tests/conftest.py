"""Session-wide gating: integration tests need a REACHABLE LLM provider.

A no-Ollama teammate's plain `pytest` stays green-or-clean-skip: if the
configured provider (from .env / env vars) can't be reached within ~2 s,
every test carrying the `integration` marker is skipped at collection.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def llm_provider_ready() -> bool:
    """Probe the configured provider once (cached). ~1-2 s worst case."""
    if not hasattr(llm_provider_ready, "_cached"):
        llm_provider_ready._cached = _probe()
    return llm_provider_ready._cached


def _probe() -> bool:
    from agent.config import load_env, resolve_config

    load_env()
    try:
        cfg = resolve_config()
    except Exception:
        return False
    try:
        if cfg["provider"] == "ollama":
            resp = httpx.get(f"{cfg['base_url']}/api/tags", timeout=2.5)
            if resp.status_code != 200:
                return False
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            name = cfg["model"]
            return any(name in m for m in models)
        resp = httpx.get(
            f"{cfg['base_url']}/models",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            timeout=2.5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def pytest_collection_modifyitems(session, config, items) -> None:  # noqa: ANN001
    if llm_provider_ready():
        return
    skip = pytest.mark.skip(reason="no reachable LLM provider - set .env (Ollama/Groq/LM Studio) to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def provider_ready() -> bool:
    """For tests that want to assert (not assume) provider availability."""
    return llm_provider_ready()