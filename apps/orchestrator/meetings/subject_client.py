"""Meeting subject research shares the existing restricted research worker."""

import os
from uuid import UUID

import httpx
from opentelemetry.propagate import inject

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.coordinator import NotConfigured
from apps.orchestrator.storage.ports import Conflict


class SubjectResearchClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(
            base_url=os.getenv("RESEARCH_AGENT_URL", "http://research:8000"),
            timeout=15,
            follow_redirects=False,
        )

    async def request(self, method, path, payload=None):
        headers = {}
        inject(headers)
        try:
            response = await self.client.request(method, path, json=payload, headers=headers)
        except httpx.HTTPError:
            raise AgentFailure("Subject research is temporarily unreachable") from None
        if response.status_code == 404:
            return None
        if response.status_code == 409:
            raise Conflict("The research worker is busy or this task ID has different input")
        if response.status_code == 503:
            raise NotConfigured("Configure the research worker, Respan and Scalekit Exa")
        if not response.is_success:
            raise AgentFailure(f"Subject research failed (HTTP {response.status_code})")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise AgentFailure("Subject research returned an invalid response") from None

    async def configured(self):
        try:
            result = await self.request("GET", "/v1/capabilities")
            return bool(
                result
                and result.get("configured") is True
                and "subject_research" in result.get("capabilities", [])
            )
        except (AgentFailure, NotConfigured):
            return False

    @staticmethod
    def task_result(result, identifier):
        if (
            not isinstance(result, dict)
            or result.get("task_id") != identifier
            or result.get("status") not in {"running", "completed", "failed", "cancelled"}
        ):
            raise AgentFailure("Subject research returned an invalid task receipt")
        return result

    async def submit(self, mention):
        identifier = str(UUID(mention.get("task_id") or mention["id"]))
        result = await self.request(
            "POST",
            "/v1/subjects",
            {
                "version": 1,
                "task_id": identifier,
                "subject": {key: mention[key] for key in ("kind", "name", "context", "evidence")},
            },
        )
        if result is None:
            raise NotConfigured("Deploy the subject research capability before retrying")
        return self.task_result(result, identifier)

    async def get(self, task_id):
        identifier = str(UUID(task_id))
        result = await self.request("GET", f"/v1/subjects/{identifier}")
        return self.task_result(result, identifier) if result is not None else None

    async def cancel(self, task_id):
        return await self.request("POST", f"/v1/subjects/{UUID(task_id)}/cancellation")

    async def close(self):
        await self.client.aclose()
