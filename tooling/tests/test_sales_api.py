"""Facade preserves coordinator outcomes without trusting browser identity headers."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI

from apps.api.auth_routes import router as auth_router
from apps.api.research_client import ResearchClient
from apps.api.research_routes import get_client as get_research_client
from apps.api.research_routes import router as research_router
from apps.api.sales_client import SalesClient, get_sales_client
from apps.api.sales_routes import router as sales_router


@asynccontextmanager
async def facade(provider):
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="http://coordinator"
    )
    bridge = SalesClient(upstream)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sales_router)
    app.dependency_overrides[get_sales_client] = lambda: bridge
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://toir.example"
        ) as client:
            yield client
    finally:
        await bridge.close()


def test_redirects_and_multiple_cookies_preserve_status_without_following():
    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(
                303,
                headers=[
                    ("Location", "https://toir.example"),
                    ("Set-Cookie", "toir_session=opaque; HttpOnly; Path=/; SameSite=lax; Secure"),
                    (
                        "Set-Cookie",
                        "toir_auth_flow=; Max-Age=0; Path=/api/auth; SameSite=lax; Secure",
                    ),
                ],
            )

        async with facade(provider) as client:
            response = await client.get("/api/auth/callback?code=abc&state=xyz&as_user=attacker")
            assert response.status_code == 303
            assert response.headers["location"] == "https://toir.example"
            assert len(response.headers.get_list("set-cookie")) == 2
            assert response.headers["cache-control"] == "no-store"
            assert len(requests) == 1
            assert requests[0].url.path == "/v1/auth/callback"
            assert dict(requests[0].url.params) == {"code": "abc", "state": "xyz"}

    asyncio.run(scenario())


def test_proxy_forwards_only_session_origin_and_validated_idempotency():
    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(202, json={"id": "message-id"})

        async with facade(provider) as client:
            session_id, key = str(uuid4()), str(uuid4())
            response = await client.post(
                f"/api/sales/sessions/{session_id}/messages",
                json={"content": "Research Acme"},
                headers={
                    "Idempotency-Key": key,
                    "Origin": "https://toir.example",
                    "Cookie": (
                        "toir_session=opaque_session; unrelated=private; toir_auth_flow=secret"
                    ),
                    "Authorization": "Bearer forged",
                    "X-User-Email": "attacker@example.com",
                },
            )
            assert response.status_code == 202
            sent = requests[0]
            assert sent.headers["cookie"] == "toir_session=opaque_session"
            assert sent.headers["origin"] == "https://toir.example"
            assert sent.headers["idempotency-key"] == key
            assert "authorization" not in sent.headers and "x-user-email" not in sent.headers
            assert (
                await client.post(f"/api/sales/sessions/{session_id}/messages")
            ).status_code == 422
            assert (await client.get("/api/sales/arbitrary-connector")).status_code == 404
            assert (await client.patch("/api/sales/tasks/invalid")).status_code == 422
            assert len(requests) == 1

    asyncio.run(scenario())


def test_shared_http_client_never_reuses_another_users_cookie():
    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            if request.url.path == "/v1/auth/login":
                return httpx.Response(
                    302,
                    headers={
                        "Set-Cookie": "toir_session=other_user; Domain=coordinator; Path=/",
                        "Location": "https://scalekit.example/login",
                    },
                )
            return httpx.Response(401, json={"detail": "Sign in"})

        async with facade(provider) as client:
            await client.get("/api/auth/login")
            response = await client.get("/api/me")
            assert response.status_code == 401
            assert requests[-1].headers.get("cookie", "") == ""

    asyncio.run(scenario())


def test_facade_preserves_conflict_and_empty_delete():
    async def scenario():
        def provider(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(409, json={"detail": "Proposal changed; review again"})

        async with facade(provider) as client:
            response = await client.patch(f"/api/sales/tasks/{uuid4()}", json={"version": 1})
            assert response.status_code == 409
            assert response.json()["detail"] == "Proposal changed; review again"
            response = await client.delete(f"/api/sales/sessions/{uuid4()}")
            assert response.status_code == 204 and response.content == b""

    asyncio.run(scenario())


def test_existing_research_facade_forwards_identity_without_cookie_leak():
    async def scenario():
        requests = []

        def provider(request):
            requests.append(request)
            if request.url.path == "/v1/capabilities":
                return httpx.Response(
                    200,
                    json={"configured": True},
                    headers={
                        "Set-Cookie": "toir_session=another_person; Domain=coordinator; Path=/",
                    },
                )
            return httpx.Response(200, json=[])

        bridge = ResearchClient()
        await bridge.client.aclose()
        bridge.client = httpx.AsyncClient(
            transport=httpx.MockTransport(provider), base_url="http://coordinator"
        )
        app = FastAPI()
        app.include_router(research_router)
        app.dependency_overrides[get_research_client] = lambda: bridge
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://toir.example"
            ) as client:
                response = await client.get(
                    "/api/research-capabilities",
                    headers={
                        "Cookie": "toir_session=valid_session; unrelated=private",
                        "Origin": "https://toir.example",
                        "Authorization": "Bearer forged",
                        "X-User-Email": "attacker@example.com",
                    },
                )
                assert response.status_code == 200
                assert requests[-1].headers["cookie"] == "toir_session=valid_session"
                assert requests[-1].headers["origin"] == "https://toir.example"
                assert "authorization" not in requests[-1].headers
                assert "x-user-email" not in requests[-1].headers
                response = await client.get("/api/research-runs")
                assert response.status_code == 200
                assert requests[-1].headers["cookie"] == ""
        finally:
            await bridge.close()

    asyncio.run(scenario())
