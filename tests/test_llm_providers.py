"""Provider abstraction tests - network-free (httpx.MockTransport).

Covers: request/body/auth correctness, Groq response normalization,
content:null safety, tool_call_id threading, oneOf stripping, 429 retry,
missing-key fail-fast. The 6 live integration tests remain in
test_agent_loop.py (skipped when no provider is reachable - see conftest).
"""
from __future__ import annotations

import json

import httpx
import pytest
from httpx import Response

from agent.llm_client import LLMError, OpenAICompatClient, OllamaClient, filter_tools_for_provider


def _client(cls, **kwargs):
    captured: dict = {}

    def handler(request: httpx.Request) -> Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content or b"{}")
        captured["headers"] = dict(request.headers)
        calls = request.headers.get("x-bomb")
        if calls is not None and int(calls) > 0:
            return Response(429, headers={"retry-after": "0", "x-bomb": str(int(calls) - 1)})
        body = {
            "message": {"role": "assistant", "content": ""},
        }
        return Response(200, json=body)

    client = cls(transport=httpx.MockTransport(handler), **kwargs)
    return client, captured


def test_openai_body_is_openai_shaped_and_auth():
    client, captured = _client(OpenAICompatClient, api_key="gsk_abc123")
    client.chat([{"role": "user", "content": "hi"}], tools=None)
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer gsk_abc123"
    body = captured["body"]
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 1024
    for banned in ("options", "think", "seed", "num_ctx", "stream"):
        assert banned not in body, f"OpenAI-compat body must not contain {banned}"
    client.close()


def test_openai_no_key_sends_no_auth_header():
    client, captured = _client(OpenAICompatClient, api_key=None)
    client.chat([{"role": "user", "content": "hi"}])
    assert "authorization" not in captured["headers"]
    client.close()


def test_oneof_stripped_for_openai_compat():
    from agent.tools import TOOLS

    stripped = filter_tools_for_provider(TOOLS)
    for tool in stripped:
        params = tool["function"]["parameters"]
        assert "oneOf" not in params and "anyOf" not in params and "$ref" not in params
    names = [t["function"]["name"] for t in stripped]
    assert names == ["get_students", "get_student", "get_teachers", "get_attendance"]
    assert stripped[1]["function"]["parameters"]["properties"]["id"]["type"] == "integer"


def test_openai_normalizes_groq_response():
    def handler(request):
        return Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_9",
                        "function": {"name": "get_students", "arguments": '{"grade": 5}'},
                    }],
                }
            }]
        })

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    data, _ = client.chat([{"role": "user", "content": "count"}])
    msg = data["message"]
    assert msg["content"] == ""
    assert msg["tool_calls"][0]["id"] == "call_9"
    name, args, call_id, _raw = OllamaClient.extract_tool_call(msg)
    assert (name, args, call_id) == ("get_students", {"grade": 5}, "call_9")
    client.close()


def test_openai_normalization_strips_unknown_message_fields():
    def handler(request):
        return Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "function_call": None,
                    "provider_specific_fields": {"x": 1},
                }
            }]
        })

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    data, _ = client.chat([{"role": "user", "content": "hi"}])
    msg = data["message"]
    assert set(msg.keys()) == {"role", "content"}
    client.close()


def test_content_none_no_crash_no_tools():
    def handler(request):
        return Response(200, json={"choices": [{"message": {"role": "assistant", "content": None}}]})

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    data, _ = client.chat([{"role": "user", "content": "hi"}])
    name, args, _id, raw = client.extract_tool_call(data["message"])
    assert (name, args, _id, raw) == (None, None, None, "")
    client.close()


def test_ollama_payload_regression_and_id_synthesis():
    def handler(request):
        body = json.loads(request.content)
        assert body["think"] is False
        assert body["options"]["num_ctx"] == 8192
        assert body["options"]["seed"] == 42
        return Response(200, json={
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "get_teachers", "arguments": {"grade": 5}}}],
            }
        })

    client = OllamaClient(transport=httpx.MockTransport(handler))
    data, _ = client.chat([{"role": "user", "content": "teachers"}], tools=[{"type": "function"}])
    name, args, call_id, _raw = client.extract_tool_call(data["message"])
    assert (name, args) == ("get_teachers", {"grade": 5})
    assert call_id is None  # older Ollama omits ids - the loop synthesizes them
    client.close()


def test_429_retries_then_succeeds_honoring_retry_after():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        if count["n"] < 3:
            return Response(429, headers={"retry-after": "0"})
        return Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    data, _ = client.chat([{"role": "user", "content": "hi"}])
    assert data["message"]["content"] == "ok"
    assert count["n"] == 3
    client.close()


def test_429_exhausted_raises_typed_error():
    def handler(request):
        return Response(429, headers={"retry-after": "1"})

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.status == 429
    assert exc_info.value.retry_after == 1.0
    client.close()


def test_400_never_retried():
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        return Response(400, text="Invalid schema.")

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.status == 400
    assert count["n"] == 1
    client.close()


def test_missing_key_fails_fast_when_openai():
    import agent.config as config

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from agent.config import resolve_config

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        resolve_config()
    monkeypatch.undo()


def test_env_file_crlf_and_precedence(tmp_path, monkeypatch):
    import os

    env = tmp_path / ".env"
    env.write_bytes(b"LLM_PROVIDER=openai\r\nLLM_MODEL=llama-3.3-70b-versatile\r\nLLM_API_KEY=gsk_test\r\n")
    import agent.config as config

    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)

    config.load_env(env)
    cfg = config.resolve_config()
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "llama-3.3-70b-versatile"  # no trailing \r
    assert cfg["api_key"] == "gsk_test"