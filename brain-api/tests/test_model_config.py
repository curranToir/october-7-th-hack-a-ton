"""Offline checks of the actual GPT-5 mini request payloads; no provider calls."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from brain.model_config import (
    GATEWAY,
    completion_parameters,
    configure_llm_environment,
)
from openai import AsyncOpenAI


def test_cognee_uses_one_respan_model_with_bounded_low_reasoning():
    env = {
        "LLM_MODEL": "openai/previous-model", "LLM_ENDPOINT": "https://other.example",
        "LLM_ARGS": '{"temperature":0,"max_tokens":600}', "LLM_TEMPERATURE": "0",
        "LLM_QUERY_MODEL": "different-model", "LLM_QUERY_API_KEY": "different-key",
        "FALLBACK_MODEL": "another-model", "FALLBACK_API_KEY": "another-key",
        "JUDGE_MODEL": "old-judge", "EMBEDDING_MODEL": "nemotron-embed",
        "RESPAN_API_KEY": "test-only", "DB_PASSWORD": "test-database-only",
    }
    configure_llm_environment(env)
    assert env["LLM_MODEL"] == "openai/gpt-5-mini"
    assert env["JUDGE_MODEL"] == "gpt-5-mini"
    assert env["LLM_ENDPOINT"] == GATEWAY
    assert env["STRUCTURED_OUTPUT_FRAMEWORK"] == "litellm_native"
    assert json.loads(env["LLM_ARGS"]) == {
        "reasoning_effort": "low", "max_completion_tokens": 32768,
    }
    assert env["LLM_MAX_COMPLETION_TOKENS"] == "32768"
    assert "LLM_TEMPERATURE" not in env
    assert env["LLM_QUERY_MODEL"] == env["LLM_QUERY_API_KEY"] == ""
    assert env["FALLBACK_MODEL"] == env["FALLBACK_API_KEY"] == ""
    assert env["EMBEDDING_MODEL"] == "nemotron-embed"
    assert env["RESPAN_API_KEY"] == "test-only" and env["DB_PASSWORD"] == "test-database-only"


@pytest.mark.parametrize("value", ["0", "-1", "128001", "not-an-integer", "1.5"])
def test_invalid_budget_fails_before_any_provider_call_without_echoing_value(value):
    with pytest.raises(ValueError) as error:
        completion_parameters("TEST_BUDGET", 8192, {"TEST_BUDGET": value})
    assert str(error.value) == "TEST_BUDGET must be an integer from 1 to 128000"


def test_explicit_completion_budget_is_applied_to_cognee_request_and_chunk_setting():
    env = {"LLM_MAX_COMPLETION_TOKENS": "65536"}
    configure_llm_environment(env)
    assert json.loads(env["LLM_ARGS"])["max_completion_tokens"] == 65536
    assert env["LLM_MAX_COMPLETION_TOKENS"] == "65536"


@pytest.mark.parametrize("finish,content,expected_error", [
    ("stop", "Grounded answer (source:hubspot).", None),
    ("length", "Incomplete answer", "brain_answer_completion_limit"),
    ("stop", None, "brain_answer_empty"),
])
def test_synthesis_sends_compatible_gateway_request_and_surfaces_limits(
    monkeypatch, finish, content, expected_error,
):
    from brain import synthesis

    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0, "model": "gpt-5-mini",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}],
        })

    async def run():
        async with AsyncOpenAI(
            base_url=GATEWAY, api_key="offline-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ) as client:
            monkeypatch.setattr(synthesis, "_llm", client)
            monkeypatch.delenv("BRAIN_ANSWER_MAX_COMPLETION_TOKENS", raising=False)
            args = ("What changed?", [{"dataset": "toir-pipeline", "text": "Verified evidence."}])
            if expected_error:
                with pytest.raises(RuntimeError, match=expected_error):
                    await synthesis.synthesize(*args)
            else:
                assert await synthesis.synthesize(*args) == content

    asyncio.run(run())
    assert len(seen) == 1
    assert str(seen[0].url) == f"{GATEWAY}/chat/completions"
    payload = json.loads(seen[0].content)
    assert payload["model"] == "gpt-5-mini"
    assert payload["max_completion_tokens"] == 8192 and payload["reasoning_effort"] == "low"
    assert "temperature" not in payload and "max_tokens" not in payload
    assert "[dataset=toir-pipeline]\nVerified evidence." in payload["messages"][1]["content"]


def test_evaluator_uses_same_model_without_loading_real_credentials(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", SimpleNamespace(load_dotenv=lambda *args: None))
    path = Path(__file__).resolve().parents[1] / "eval" / "run.py"
    spec = importlib.util.spec_from_file_location("offline_brain_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []

    def respond(url, payload, token):
        seen.append((url, payload, token))
        return {"choices": [{"message": {"content": '{"score":1,"reason":"Grounded."}'}}]}

    monkeypatch.setattr(module, "post_json", respond)
    monkeypatch.setenv("RESPAN_API_KEY", "offline-test")
    monkeypatch.setenv("JUDGE_MODEL", "obsolete-model")
    monkeypatch.delenv("JUDGE_MAX_COMPLETION_TOKENS", raising=False)
    result = module.judge({"question": "What changed?", "must_mention": ["evidence"]}, "evidence")
    assert result == {"score": 1, "reason": "Grounded."}
    url, payload, token = seen[0]
    assert url == f"{GATEWAY}/chat/completions" and token == "offline-test"
    assert payload["model"] == "gpt-5-mini"
    assert payload["max_completion_tokens"] == 8192 and payload["reasoning_effort"] == "low"
    assert "temperature" not in payload and "max_tokens" not in payload
