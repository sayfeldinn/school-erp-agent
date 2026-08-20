"""LLM clients - provider abstraction (v2).

Two backends, one contract:
  chat(messages, tools=None, num_predict=512) -> ({"message": <normalized msg>}, elapsed)

- OllamaClient: native POST /api/chat (qwen3:8b, think:false) - the default.
- OpenAICompatClient: POST {base}/chat/completions - covers Groq, LM Studio,
  Jan (all OpenAI-compatible). Requires Bearer api_key for remote services.

Both return a dict with a "message" key so the loop in core.py never sees a
provider-specific envelope. Tool schemas are filtered for OpenAI-compatible
providers (oneOf/anyOf/$ref are rejected there) - the app-level executor stays
the real enforcer.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
NUM_CTX = 8192
TIMEOUT = 60.0
MAX_RETRIES = 2


class LLMError(Exception):
    """Typed LLM failure so the loop can surface the RIGHT message.

    status: HTTP status or None for connection errors.
    retry_after: seconds from the Retry-After header (429 only).
    """

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _strip_openai_incompatible(schema: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a JSON schema without constructs OpenAI-compatible APIs reject.

    oneOf/anyOf/$ref are removed (executor.py still enforces "exactly one");
    everything else is kept verbatim.
    """
    if isinstance(schema, dict):
        out = {k: _strip_openai_incompatible(v) for k, v in schema.items()
               if k not in ("oneOf", "anyOf", "$ref")}
        if "properties" in out and "required" in out:
            required = [r for r in out["required"] if r in out["properties"]]
            out["required"] = required or out["required"]
        return out
    if isinstance(schema, list):
        return [_strip_openai_incompatible(item) for item in schema]
    return schema


def filter_tools_for_provider(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Sanitize OpenAI-style tool definitions for OpenAI-compatible endpoints."""
    if not tools:
        return tools
    out = []
    for tool in tools:
        copy = dict(tool)
        fn = dict(copy.get("function", {}))
        if "parameters" in fn:
            fn["parameters"] = _strip_openai_incompatible(fn["parameters"])
        copy["function"] = fn
        out.append(copy)
    return out


class LLMClient:
    """Common interface + shared helpers. Subclasses implement chat()."""

    BASE_PATH = ""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = TIMEOUT,
        transport: Any = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _post_json(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        """POST with retry on 429 (honoring Retry-After) and transient 5xx.

        Never retries 400/401/403 - those are permanent (bad params / bad key).
        """
        url = f"{self.base_url}{path}"
        attempts = 0
        while True:
            try:
                resp = self._client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise LLMError(f"connection error: {exc.__class__.__name__}") from exc
            if resp.status_code in (429, 500, 502, 503, 504) and attempts < MAX_RETRIES:
                attempts += 1
                retry_after = float(resp.headers.get("retry-after", 1)) if resp.status_code == 429 else 1.0
                time.sleep(min(retry_after, 10.0))
                continue
            if resp.status_code != 200:
                reason = resp.text[:300]
                raise LLMError(
                    f"AI service error {resp.status_code}: {reason}",
                    status=resp.status_code,
                    retry_after=float(resp.headers["retry-after"]) if "retry-after" in resp.headers else None,
                )
            return resp.json()

    @staticmethod
    def extract_tool_call(msg: dict[str, Any]) -> tuple[str | None, dict | None, str | None, str]:
        """Return (tool_name, arguments, tool_call_id, raw) from an assistant message.

        arguments: dict if the provider sent an object (Ollama native) or a
        parsed JSON string (OpenAI-compatible). tool_call_id: the provider id,
        or None when the provider omitted it (older Ollama) - the loop
        synthesizes one in that case.
        """
        raw = (msg.get("content") or "").strip()
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
            if not isinstance(args, dict):
                args = {"_raw": args}
            return name, args, calls[0].get("id"), json.dumps({"tool_calls": calls})
        if not raw:
            return None, None, None, ""
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and ("name" in obj or "tool" in obj) and "arguments" in obj:
                return obj.get("name") or obj.get("tool"), obj.get("arguments"), None, raw
        except Exception:
            pass
        return None, None, None, raw


class OllamaClient(LLMClient):
    """Native Ollama /api/chat - qwen3 tool calling with think:false."""

    BASE_PATH = "/api/chat"

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        num_ctx: int = NUM_CTX,
        temperature: float = 0.0,
        seed: int = 42,
        num_predict: int = 512,
        timeout: float = TIMEOUT,
        transport: Any = None,
    ):
        super().__init__(base_url=base_url, model=model, timeout=timeout, transport=transport)
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.seed = seed
        self.num_predict = num_predict

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        num_predict: int | None = None,
    ) -> tuple[dict[str, Any], float]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # qwen3: thinking mode doubles latency
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
                "num_predict": num_predict or self.num_predict,
                "seed": self.seed,
            },
        }
        if tools:
            body["tools"] = tools
        t0 = time.perf_counter()
        data = self._post_json(self.BASE_PATH, body)
        message = data.get("message") or {}
        return {"message": message}, time.perf_counter() - t0


class OpenAICompatClient(LLMClient):
    """Chat Completions for Groq / LM Studio / Jan (and Ollama's /v1 shim).

    Body is exactly OpenAI-shaped: model, messages, tools, temperature,
    max_tokens. No options/think/seed - unknown fields are rejected by some
    providers. max_tokens (not max_completion_tokens) keeps LM Studio happy;
    Groq still accepts it.
    """

    BASE_PATH = "/chat/completions"

    def __init__(
        self,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "llama-3.3-70b-versatile",
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = TIMEOUT,
        transport: Any = None,
    ):
        super().__init__(base_url=base_url, model=model, api_key=api_key, timeout=timeout, transport=transport)
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        num_predict: int | None = None,
    ) -> tuple[dict[str, Any], float]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": num_predict or self.max_tokens,
        }
        if tools:
            body["tools"] = filter_tools_for_provider(tools)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        t0 = time.perf_counter()
        data = self._post_json(self.BASE_PATH, body, headers=headers)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        normalized = {
            "role": message.get("role", "assistant"),
            "content": message.get("content") or "",
        }
        calls = message.get("tool_calls")
        if calls:
            normalized["tool_calls"] = [
                {
                    "id": call.get("id", ""),
                    "function": call.get("function", {}),
                }
                for call in calls
            ]
        return {"message": normalized}, time.perf_counter() - t0