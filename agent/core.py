"""AgentLoop v2 - the heart of the project.

User -> LLM decision -> VALIDATE & EXECUTE (app-level) -> tool result (untrusted)
-> LLM analysis -> final answer.

Guards (all enforced here, never by the model):
- max tool-call iterations per message
- repeated identical tool call abort
- validation failures / HTTP errors / empty results fed back as structured data
- only role-allowed tools are even offered to the model
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import create_llm
from agent.executor import ToolCall, ToolExecutor
from agent.llm_client import LLMClient, LLMError, OllamaClient
from agent.tools import TOOL_REGISTRY, allowed_tools_for

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_MAX_ITERATIONS = 5

_GENERIC_FALLBACK = "I couldn't complete that request. Please try rephrasing it."


@dataclass
class LoopResult:
    answer: str
    status: str  # "answered" | "max_iterations" | "repeated_call" | "error"
    iterations: int
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        mock_base_url: str,
        role: str,
        user: str,
        school: str,
        system_prompt: str | None = None,
        llm: LLMClient | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        self.executor = ToolExecutor(mock_base_url, role, user, school)
        self.llm = llm or create_llm()
        self.role = role
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()

    # -- the loop ------------------------------------------------------------
    def run(self, user_message: str, history: list[dict[str, Any]] | None = None) -> LoopResult:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history[-8:])  # keep context small (latency)
        messages.append({"role": "user", "content": user_message})

        offered_tools = self._offered_tools()
        repeated_seen: dict[str, int] = {}
        steps: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []

        for i in range(self.max_iterations):
            try:
                data, elapsed = self.llm.chat(messages, offered_tools)
            except LLMError as exc:
                return LoopResult(
                    answer=_llm_error_phrase(exc),
                    status="error",
                    iterations=i,
                    steps=steps,
                    tool_calls=tool_calls,
                )
            except Exception as exc:  # provider unreachable / unexpected failure
                return LoopResult(
                    answer="The AI service is unavailable right now. Please try again in a moment.",
                    status="error",
                    iterations=i,
                    steps=steps,
                    tool_calls=tool_calls,
                )

            msg = data.get("message", {})
            messages.append(_history_message(msg, tool_id_suffix=f"call_{i}"))  # provider-safe history

            tool_name, args, tool_call_id, _raw = LLMClient.extract_tool_call(msg)

            if tool_name is None:
                return LoopResult(
                    answer=(msg.get("content") or "").strip() or _GENERIC_FALLBACK,
                    status="answered",
                    iterations=i + 1,
                    steps=steps,
                    tool_calls=tool_calls,
                )

            # -- app-level validation + execution (never the AI decides safety)
            call = ToolCall(tool_name, args or {})
            result = self.executor.execute(call)

            key = tool_name + json.dumps(args or {}, sort_keys=True)
            repeated_seen[key] = repeated_seen.get(key, 0) + 1

            trace = {
                "iter": i,
                "tool": tool_name,
                "params": args or {},
                "status": result.status,
                "http": result.http_status,
            }
            if result.status != "ok":
                trace["detail"] = result.message
            steps.append(trace)
            tool_calls.append({"tool": tool_name, "arguments": args or {}})

            messages.append(_tool_message(result.as_prompt_block(), tool_call_id, synthetic_id=f"call_{i}"))

            if repeated_seen[key] >= 2:
                return LoopResult(
                    answer=_GENERIC_FALLBACK,
                    status="repeated_call",
                    iterations=i + 1,
                    steps=steps,
                    tool_calls=tool_calls,
                )

        # iteration cap reached without a final answer
        return LoopResult(
            answer=_GENERIC_FALLBACK,
            status="max_iterations",
            iterations=self.max_iterations,
            steps=steps,
            tool_calls=tool_calls,
        )

    def close(self) -> None:
        self.executor.close()

    # -- helpers -------------------------------------------------------------
    def _offered_tools(self) -> list[dict[str, Any]]:
        """Only role-allowed tools are handed to the model (anytool else is rejected anyway)."""
        allowed = set(allowed_tools_for(self.role))
        return [TOOL_REGISTRY[n] for n in TOOL_REGISTRY if n in allowed]


def load_system_prompt(path: Path | None = None) -> str:
    p = path or PROMPTS_DIR / "system.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["prompt"]


def _llm_error_phrase(exc: LLMError) -> str:
    """Human message per failure class - never the blanket 'unavailable'."""
    if exc.status == 429:
        return "The AI service is rate-limited right now (free-tier quota). Wait a few seconds and try again."
    if exc.status in (401, 403):
        return "The AI service rejected the API key. Check LLM_API_KEY in .env."
    if exc.status == 404:
        return "The AI model (or service URL) was not found. Check LLM_MODEL and LLM_BASE_URL."
    if exc.status == 400:
        return "The AI service rejected the request (bad parameters or schema). Check LLM_MODEL and LLM_BASE_URL."
    return "The AI service is unavailable right now. Please try again in a moment."


def _history_message(msg: dict[str, Any], tool_id_suffix: str) -> dict[str, Any]:
    """Provider-safe assistant turn for history.

    - Keeps ONLY the executed tool call: OpenAI-compatible APIs reject a
      following turn whose tool_call_ids were never answered (multi-call turns
      from the model are common on qwen3/llama).
    - Guarantees an id on the kept call (synthesized when absent, e.g. older
      Ollama) so the paired role:"tool" message is always id-linked.
    - Strips provider-specific keys (think/reasoning/...): unknown message
      fields are 400s on strict providers.
    """
    content = (msg.get("content") or "").strip()
    calls = msg.get("tool_calls") or []
    if not calls:
        return {"role": "assistant", "content": content}
    first = calls[0].get("function", {})
    call = {
        "id": calls[0].get("id") or tool_id_suffix,
        "type": "function",
        "function": {
            "name": first.get("name"),
            "arguments": first.get("arguments"),
        },
    }
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [call],
    }


def _tool_message(content: str, tool_call_id: str | None, synthetic_id: str) -> dict[str, Any]:
    """role:"tool" result, id-keyed so every provider can follow it."""
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id or synthetic_id,
    }