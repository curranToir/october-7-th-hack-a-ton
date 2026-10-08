"""Engineering can authenticate without inheriting any shared sales data access."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from apps.orchestrator.meetings.routes import router as meetings
from apps.orchestrator.routes import router as research
from apps.orchestrator.sales.auth import AuthService
from apps.orchestrator.sales.brain import BrainClient
from tooling.tests.test_sales_auth import CONFIG, auth_stack
from tooling.tests.test_sales_routes import route_stack

ENGINEER = "curran@neptuneops.com"
CLAIMS = {"email": ENGINEER, "email_verified": True, "sub": "engineering-subject"}


@pytest.mark.parametrize("public", [True, False])
def test_google_login_succeeds_but_sales_data_and_actions_are_denied(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public=public, email=None) as stack:
            client, prefix = stack.client, stack.prefix
            login = await client.get(prefix + "/auth/login")
            query = parse_qs(urlsplit(login.headers["location"]).query)
            stack.provider_claims.update(CLAIMS, nonce=query["nonce"][0], role="sales")
            if not public:
                # The internal route uses the public callback cookie path.
                client.cookies.set("toir_auth_flow", login.cookies["toir_auth_flow"])
            callback = await client.get(
                prefix + "/auth/callback",
                params={
                    "code": "verified-google-code",
                    "state": query["state"][0],
                },
            )
            assert callback.status_code == 303
            me = await client.get(prefix + "/me")
            assert me.status_code == 200
            assert me.json()["email"] == ENGINEER
            assert me.json()["role"] == "engineering"  # Ignore provider-supplied sales role.
            assert (await client.get(prefix + "/auth/sessions")).status_code == 200
            resource = str(uuid4())
            requests = [
                ("GET", "/workspace"),
                ("GET", "/capabilities"),
                ("POST", "/sessions"),
                ("PATCH", f"/sessions/{resource}"),
                ("DELETE", f"/sessions/{resource}"),
                ("POST", f"/sessions/{resource}/messages"),
                ("PATCH", f"/tasks/{resource}"),
                ("POST", f"/tasks/{resource}/decisions"),
                ("POST", f"/tasks/{resource}/retries"),
                ("PATCH", "/automation"),
                ("POST", f"/jobs/{resource}/cancellation"),
                ("POST", f"/jobs/{resource}/retries"),
            ]
            for method, path in requests:
                response = await client.request(
                    method,
                    prefix + "/sales" + path,
                    json={},
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == 403, (method, path, response.text)
                assert response.json() == {
                    "detail": "Sales data is not available to your department"
                }
            async with stack.store.transaction() as tx:
                assert not await tx.list("session")
                assert not await tx.list("job")
                assert not await tx.list("outbox")
                member = await tx.get("member", ENGINEER)
                assert member["department"] == "engineering"
            assert (await client.post(prefix + "/auth/logout")).status_code == 204
            assert (await client.get(prefix + "/me")).status_code == 401

    asyncio.run(scenario())


def test_engineering_cannot_use_research_or_meeting_routes(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, _, _, _, _store):
            await auth.sessions.establish(CLAIMS, "engineering-session")
            app = FastAPI()
            app.state.sales_service = SimpleNamespace(auth=auth)
            # These sentinels have no data methods: the access check must run first.
            app.state.coordinator = object()
            app.state.meeting_service = object()
            app.include_router(research)
            app.include_router(meetings)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=CONFIG.public_url,
                cookies={"toir_session": "engineering-session"},
            ) as client:
                for path in [
                    "/v1/capabilities",
                    "/v1/research-runs",
                    f"/v1/research-runs/{uuid4()}",
                    "/v1/meetings/workspace",
                ]:
                    response = await client.get(path)
                    assert response.status_code == 403, (path, response.text)

    asyncio.run(scenario())


def test_engineering_identity_never_reaches_brain_or_becomes_sales(tmp_path):
    async def scenario():
        # Even accidentally listing the engineer as a salesperson cannot elevate access.
        config = replace(CONFIG, member_emails=CONFIG.member_emails | {ENGINEER})
        async with auth_stack(tmp_path) as (_, _, _, _, store):
            auth = AuthService(store, config, client=object())
            await auth.sessions.establish(CLAIMS, "engineering-session")
            assert (await auth.current_user("engineering-session"))["role"] == "engineering"
            client = BrainClient(client=object())  # Any HTTP attempt would fail this test.
            with pytest.raises(RuntimeError, match="not authorized for sales memory"):
                await client.recall(ENGINEER, "What do the salespeople know?", str(uuid4()))
            removed = AuthService(
                store,
                replace(CONFIG, engineering_member_emails=frozenset()),
                client=object(),
            )
            with pytest.raises(HTTPException) as failure:
                await removed.current_user("engineering-session")
            assert failure.value.status_code == 403
            async with store.transaction() as tx:
                member = await tx.get("member", ENGINEER)
                member["role"] = "sales"
                await tx.put("member", ENGINEER, member)
            with pytest.raises(HTTPException) as failure:
                await auth.current_user("engineering-session")
            assert failure.value.status_code == 403

    asyncio.run(scenario())
