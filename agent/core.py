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

from agent.executor import ToolCall, ToolExecutor
from agent.llm_client import LLMClient
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
        self.llm = llm or LLMClient()
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
            except Exception as exc:  # Ollama unreachable / timeout
                return LoopResult(
                    answer="The AI service is unavailable right now. Please try again in a moment.",
                    status="error",
                    iterations=i,
                    steps=steps,
                    tool_calls=tool_calls,
                )

            msg = data.get("message", {})
            messages.append(msg)  # keep assistant turn (incl. tool_calls) in history

            tool_name, args, _raw = LLMClient.extract_tool_call(msg)

            if tool_name is None:
                return LoopResult(
                    answer=msg.get("content", "").strip() or _GENERIC_FALLBACK,
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

            messages.append({"role": "tool", "content": result.as_prompt_block()})

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