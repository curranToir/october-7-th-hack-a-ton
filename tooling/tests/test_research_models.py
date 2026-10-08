"""Exercise the pinned model SDK's HTTP serialization without provider calls."""

import asyncio
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from apps.orchestrator.integrations import models
from apps.orchestrator.models.research import ResearchPlan


@pytest.mark.parametrize("configured_model", [None, "gpt-5-mini-2025-08-07"])
def test_respan_structured_request_uses_mini_chat_tools_and_completion_budget(
    monkeypatch, configured_model,
):
    monkeypatch.setenv("RESPAN_API_KEY", "test-only-respan-key")
    if configured_model:
        monkeypatch.setenv("RESPAN_MODEL", configured_model)
    else:
        monkeypatch.delenv("RESPAN_MODEL", raising=False)
    requests = []
    expected = ResearchPlan(queries=["US warehouse AI integrations"], focus="Warehouse operations")

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": configured_model or "gpt-5-mini",
            "choices": [{
                "index": 0, "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_plan", "type": "function",
                        "function": {
                            "name": "ResearchPlan", "arguments": expected.model_dump_json(),
                        },
                    }],
                },
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    async def scenario():
        transport = httpx.MockTransport(respond)
        with httpx.Client(transport=transport) as sync_client:
            async with httpx.AsyncClient(transport=transport) as async_client:
                def local_model(**kwargs):
                    return ChatOpenAI(
                        **kwargs, http_client=sync_client, http_async_client=async_client,
                    )

                monkeypatch.setattr(models, "ChatOpenAI", local_model)
                adapter = models.RespanModels()
                actual = await adapter.structured(
                    ResearchPlan, "Plan source-grounded company searches.", {"company": "Toir"},
                )
                assert actual == expected
                assert adapter.client.use_responses_api is False
                assert len(requests) == 1
                request = requests[0]
                assert str(request.url) == "https://api.respan.ai/api/chat/completions"
                payload = json.loads(request.content)
                assert payload["model"] == (configured_model or "gpt-5-mini")
                assert payload["reasoning_effort"] == "low"
                assert payload["max_completion_tokens"] == 16384
                assert "max_tokens" not in payload
                assert "temperature" not in payload
                assert "input" not in payload, "This integration must use Chat Completions"
                assert payload["tools"][0]["function"]["name"] == "ResearchPlan"
                assert payload["tool_choice"] == {
                    "type": "function", "function": {"name": "ResearchPlan"},
                }
                assert payload["messages"][0]["role"] == "system"
                assert "untrusted data" in payload["messages"][0]["content"]
                assert json.loads(payload["messages"][1]["content"]) == {"company": "Toir"}

    asyncio.run(scenario())
