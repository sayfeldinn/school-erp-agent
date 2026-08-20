"""Ollama LLM client - native tool-calling mode (calibration: default).

Thin wrapper over POST /api/chat. All loop logic lives in core.py.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
NUM_CTX = 8192
TIMEOUT = 300.0


class LLMClient:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        num_ctx: int = NUM_CTX,
        temperature: float = 0.0,
        seed: int = 42,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.seed = seed

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        num_predict: int = 512,
    ) -> tuple[dict[str, Any], float]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # qwen3: thinking mode doubles latency
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
                "num_predict": num_predict,
                "seed": self.seed,
            },
        }
        if tools:
            body["tools"] = tools
        t0 = time.perf_counter()
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{self.base_url}/api/chat", json=body)
            resp.raise_for_status()
            return resp.json(), time.perf_counter() - t0

    @staticmethod
    def extract_tool_call(msg: dict[str, Any]) -> tuple[str | None, dict | None, str]:
        """Return (tool_name, arguments, raw) from an assistant message."""
        raw = msg.get("content", "")
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function", {})
            name = fn.get("name")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            return name, args, json.dumps({"tool_calls": calls})
        text = raw.strip()
        if not text:
            return None, None, ""
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and ("name" in obj or "tool" in obj) and "arguments" in obj:
                return obj.get("name") or obj.get("tool"), obj.get("arguments"), text
        except Exception:
            pass
        return None, None, text