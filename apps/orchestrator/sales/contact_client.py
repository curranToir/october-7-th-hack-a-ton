"""Versioned contact-worker boundary; the coordinator owns durable task state."""

from __future__ import annotations

import os
from typing import Literal

import httpx
from opentelemetry.propagate import inject
from pydantic import Field, ValidationError

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.models.research import Contract, Source
from apps.orchestrator.sales.models import ContactReport, ContactTask


class ContactStatus(Contract):
    version: Literal[1] = 1
    task_id: str
    run_id: str
    status: Literal["running", "completed", "failed", "cancelled"]
    progress: str = Field(default="Researching contacts", max_length=500)
    usage: dict[str, int] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list, max_length=100)
    report: ContactReport | None = None
    error: str | None = None


class ContactAgentClient:
    def __init__(self, *, base_url: str | None = None, transport=None):
        self.client = httpx.AsyncClient(
            base_url=base_url or os.environ.get("CONTACT_AGENT_URL", "http://contacts:8000"),
            timeout=15,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method: str, path: str, payload: dict | None = None):
        headers: dict[str, str] = {}
        inject(headers)
        try:
            response = await self.client.request(method, path, json=payload, headers=headers)
        except httpx.HTTPError:
            raise AgentFailure("Contact worker is unreachable") from None
        if response.status_code == 404:
            return None
        if response.is_error:
            raise AgentFailure(f"Contact worker rejected the request (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError:
            raise AgentFailure("Contact worker returned an invalid response") from None

    async def capabilities(self):
        return await self.request("GET", "/v1/capabilities")

    def _status(self, value) -> ContactStatus | None:
        if value is None:
            return None
        try:
            return ContactStatus.model_validate(value)
        except ValidationError:
            raise AgentFailure("Contact worker returned an invalid status contract") from None

    async def submit(self, task: ContactTask | dict):
        task = ContactTask.model_validate(task)
        value = await self.request("POST", "/v1/tasks", task.model_dump(mode="json"))
        return self._status(value)

    async def get(self, task_id: str):
        return self._status(await self.request("GET", f"/v1/tasks/{task_id}"))

    async def cancel(self, task_id: str):
        return self._status(await self.request("POST", f"/v1/tasks/{task_id}/cancellation"))
