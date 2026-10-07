"""Browser API transport. Never forwards arbitrary paths or auth headers."""

import os

import httpx
from fastapi import HTTPException, Request

from apps.api.browser_auth import browser_headers


class ResearchClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
            timeout=25,
        )

    async def close(self):
        await self.client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        key: str | None = None,
        browser_request: Request | None = None,
    ):
        headers = browser_headers(browser_request)
        if key:
            headers["Idempotency-Key"] = key
        try:
            result = await self.client.request(
                method,
                path,
                json=body,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Research coordinator is temporarily unavailable") from None
        if result.is_error:
            message = "The research request could not be completed"
            try:
                detail = result.json().get("detail")
                if isinstance(detail, str) and len(detail) <= 500:
                    message = detail
            except ValueError:
                pass
            raise HTTPException(result.status_code, message)
        return result.json()
