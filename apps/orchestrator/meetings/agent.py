"""HTTP contract with the independently deployed meeting agent."""

import os

import httpx

from apps.orchestrator.meetings.evidence import validate_evidence
from apps.orchestrator.meetings.models import AnalysisRequest, MeetingNotes


class MeetingAgentClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=os.getenv("MEETING_AGENT_URL", "http://meetings:8000"),
            timeout=90,
        )

    async def configured(self):
        try:
            response = await self.client.get("/v1/capabilities", timeout=3)
            return response.is_success and response.json().get("configured") is True
        except (httpx.HTTPError, ValueError):
            return False

    async def analyze(self, request: AnalysisRequest):
        try:
            response = await self.client.post("/v1/analyses", json=request.model_dump())
            response.raise_for_status()
            return validate_evidence(MeetingNotes.model_validate(response.json()), request)
        except (httpx.HTTPError, ValueError):
            raise ValueError("Meeting analysis failed; retry using the saved transcript") from None

    async def close(self):
        await self.client.aclose()
