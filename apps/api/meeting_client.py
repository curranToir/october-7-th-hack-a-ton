"""Allowlisted meeting transport, with raw webhook bytes and explicit identity forwarding."""

import os
import re

import httpx
from fastapi import HTTPException, Request, Response

from apps.api.browser_auth import browser_headers

ID = r"[0-9a-fA-F-]{36}"
ALLOWED = {
    "GET": re.compile(r"^/v1/meetings/workspace$"),
    "POST": re.compile(
        rf"^/v1/meetings(?:/(?:imports|demos|{ID}/retries|"
        rf"tasks/{ID}/(?:decisions|retries|reconciliation)|"
        r"webhooks/recall/(?:realtime|dashboard)))?$"
    ),
    "PATCH": re.compile(rf"^/v1/meetings/tasks/{ID}$"),
}


class MeetingClient:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(
            base_url=os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000"),
            timeout=20,
            follow_redirects=False,
        )

    async def forward(self, request: Request, path: str):
        if not ALLOWED.get(request.method, re.compile(r"a^")).fullmatch(path):
            raise HTTPException(404, "Unknown meeting resource")
        headers = browser_headers(request)
        if "/webhooks/" in path:
            headers = {"Cookie": "", "Accept": "application/json"}
            for prefix in ("webhook", "svix"):
                for field in ("id", "timestamp", "signature"):
                    name = f"{prefix}-{field}"
                    if name in request.headers:
                        headers[name] = request.headers[name]
        if "idempotency-key" in request.headers:
            headers["Idempotency-Key"] = request.headers["idempotency-key"]
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_000_000:
                raise HTTPException(413, "Meeting request is too large")
        if body:
            headers["Content-Type"] = "application/json"
        try:
            result = await self.client.request(
                request.method, path, content=bytes(body), headers=headers
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Meeting coordinator is temporarily unavailable") from None
        response = Response(
            content=result.content,
            status_code=result.status_code,
            headers={
                "Content-Type": result.headers.get("content-type", "application/json"),
                "Cache-Control": "no-store",
            },
        )

        if "retry-after" in result.headers:
            response.headers["Retry-After"] = result.headers["retry-after"]
        return response

    async def close(self):
        await self.client.aclose()


def get_meeting_client(request: Request):
    return request.app.state.meeting_client
