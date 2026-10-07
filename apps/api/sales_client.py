"""Allowlisted browser facade transport; the coordinator owns authorization."""

import os
import re

import httpx
from fastapi import HTTPException, Request, Response

from apps.api.browser_auth import browser_headers

RESOURCE = r"[0-9a-fA-F-]{36}"
ALLOWED = {
    "GET": re.compile(r"^/v1/(?:me|auth/(?:login|callback)|sales/(?:workspace|capabilities))$"),
    "POST": re.compile(
        rf"^/v1/(?:auth/logout|sales/(?:sessions|sessions/{RESOURCE}/messages|"
        rf"tasks/{RESOURCE}/(?:decisions|retries)|jobs/{RESOURCE}/(?:cancellation|retries)))$"
    ),
    "PATCH": re.compile(rf"^/v1/sales/(?:automation|sessions/{RESOURCE}|tasks/{RESOURCE})$"),
    "DELETE": re.compile(rf"^/v1/sales/sessions/{RESOURCE}$"),
}


class SalesClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            base_url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
            timeout=25,
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def forward(self, request: Request, path: str, *, params=None, key=None) -> Response:
        if not ALLOWED.get(request.method, re.compile(r"a^")).fullmatch(path):
            raise HTTPException(404, "Unknown sales resource")
        headers = browser_headers(request, include_flow=path == "/v1/auth/callback")
        if key:
            headers["Idempotency-Key"] = str(key)
        content = None
        if request.method in {"POST", "PATCH"}:
            content = await request.body()
            if len(content) > 65536:
                raise HTTPException(413, "Sales request is too large")
            if content:
                headers["Content-Type"] = "application/json"
        try:
            result = await self.client.request(
                request.method,
                path,
                params=params,
                headers=headers,
                content=content,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Sales coordinator is temporarily unavailable") from None
        response = Response(content=result.content, status_code=result.status_code)
        for name in ("content-type", "location", "retry-after"):
            if name in result.headers:
                response.headers[name] = result.headers[name]
        for cookie in result.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", cookie)
        response.headers["Cache-Control"] = "no-store"
        return response


def get_sales_client(request: Request) -> SalesClient:
    return request.app.state.sales_client
