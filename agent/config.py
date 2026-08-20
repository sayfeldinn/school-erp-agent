"""LLM provider config: .env + environment variables.

One `.env` file at the repo root switches the whole project between backends:

  LLM_PROVIDER=ollama|openai     (default: ollama - zero-config regression)
  LLM_BASE_URL                   (ollama: http://127.0.0.1:11434
                                  openai: https://api.groq.com/openai/v1)
  LLM_MODEL                      (ollama: qwen3:8b
                                  openai: llama-3.3-70b-versatile)
  LLM_API_KEY                    (required when provider=openai; GROQ_API_KEY alias)
  LLM_MAX_TOKENS                 (generation cap; 512 ollama / 1024 openai defaults)

Precedence: real environment variables override .env values (python-dotenv
never overrides by default), so `$env:LLM_MODEL=...` in PowerShell wins.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.llm_client import LLMClient, OpenAICompatClient, OllamaClient

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "ollama": {"base_url": "http://127.0.0.1:11434", "model": "qwen3:8b", "max_tokens": 512},
    "openai": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "max_tokens": 1024},
}


def load_env(path: Path | None = None) -> None:
    """Load the repo .env once (process env wins; empty values ignored)."""
    load_dotenv(path or ENV_FILE, override=False)


def _getenv(name: str, default: str) -> str:
    """Env var with empty-string treated as unset (Open WebUI convention)."""
    return os.getenv(name, "").strip() or default


def resolve_config() -> dict[str, Any]:
    """Read and validate the active provider config from env/.env."""
    provider = _getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(
            f"LLM_PROVIDER must be one of {sorted(PROVIDER_DEFAULTS)} (got '{provider}')"
        )
    d = PROVIDER_DEFAULTS[provider]
    base_url = _getenv("LLM_BASE_URL", d["base_url"]).rstrip("/")
    if not base_url:
        raise ValueError("LLM_BASE_URL must not be empty")
    api_key = _getenv("LLM_API_KEY", "") or _getenv("GROQ_API_KEY", "")
    if provider == "openai" and not api_key:
        raise ValueError(
            "LLM_PROVIDER=openai requires LLM_API_KEY (or GROQ_API_KEY) in .env "
            "- get a free key at console.groq.com"
        )
    try:
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "").strip() or d["max_tokens"])
    except ValueError:
        raise ValueError("LLM_MAX_TOKENS must be an integer") from None
    return {
        "provider": provider,
        "base_url": base_url,
        "model": _getenv("LLM_MODEL", d["model"]),
        "api_key": api_key,
        "max_tokens": max_tokens,
    }


def create_llm() -> LLMClient:
    """Build the configured client (Ollama unless LLM_PROVIDER=openai)."""
    cfg = resolve_config()
    if cfg["provider"] == "ollama":
        return OllamaClient(
            base_url=cfg["base_url"],
            model=cfg["model"],
            num_predict=cfg["max_tokens"],
        )
    return OpenAICompatClient(
        base_url=cfg["base_url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        max_tokens=cfg["max_tokens"],
    )


def describe() -> str:
    """One-line resolved config (key masked) - for banners and --check."""
    cfg = resolve_config()
    key = f" key={cfg['api_key'][:7]}..." if cfg["api_key"] else " (no key)"
    return f"llm: {cfg['provider']} model={cfg['model']} base={cfg['base_url']}{key}"


def load_env_and_describe() -> str:
    load_env()
    return describe()