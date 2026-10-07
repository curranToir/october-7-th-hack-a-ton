"""Versioned HTTP boundary. No Kubernetes client or direct agent imports."""

import os

import httpx
from opentelemetry.propagate import inject


class AgentFailure(Exception):
    pass


class SessionLost(AgentFailure):
    pass


class ResearchAgentClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=os.environ.get("RESEARCH_AGENT_URL", "http://research:8000"),
            timeout=15,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method: str, path: str, payload: dict | None = None):
        headers: dict[str, str] = {}
        inject(headers)
        try:
            response = await self.client.request(method, path, json=payload, headers=headers)
        except httpx.HTTPError:
            raise AgentFailure("Research agent is unreachable") from None
        if response.status_code == 404:
            return None
        if response.is_error:
            # Only display a fixed error code, never arbitrary upstream text.
            raise AgentFailure(f"Research agent rejected the request (HTTP {response.status_code})")
        return response.json()

    async def capabilities(self):
        return await self.request("GET", "/v1/capabilities")

    async def submit(self, payload: dict):
        return await self.request("POST", "/v1/tasks", payload)

    async def get(self, task_id: str):
        return await self.request("GET", f"/v1/tasks/{task_id}")

    async def cancel(self, task_id: str):
        return await self.request("POST", f"/v1/tasks/{task_id}/cancellation")
