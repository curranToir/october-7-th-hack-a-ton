"""Exercise persisted meeting commands through the real browser/auth facade."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from apps.api.meeting_client import MeetingClient, get_meeting_client
from apps.api.meeting_routes import router as public_router
from apps.orchestrator.meetings.routes import router
from apps.orchestrator.sales.auth import AuthService
from apps.orchestrator.sales.store import SalesStore
from tooling.tests.test_meetings_webhooks import event_body, signed
from tooling.tests.test_meetings_workflow import ACTOR, meeting_stack
from tooling.tests.test_sales_auth import CONFIG


@asynccontextmanager
async def route_stack(tmp_path, public=True, authenticated=True):
    async with meeting_stack(tmp_path) as stack:
        sales_store = SalesStore(stack.repository)
        await sales_store.setup()

        def no_network(request):
            raise AssertionError("The meeting route test must not contact external providers")

        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as provider:
            auth = AuthService(sales_store, CONFIG, provider)
            await auth.sessions.establish(
                {"email": ACTOR["email"], "email_verified": True, "sub": "verified-user"},
                "meeting_session",
            )
            internal = FastAPI()
            internal.include_router(router)
            internal.state.meeting_service = stack.service
            internal.state.sales_service = SimpleNamespace(auth=auth)
            bridge = MeetingClient(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=internal),
                    base_url="http://coordinator",
                )
            )
            browser = FastAPI()
            browser.include_router(public_router)
            browser.dependency_overrides[get_meeting_client] = lambda: bridge
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=browser if public else internal),
                    base_url=CONFIG.public_url,
                    headers={"Origin": CONFIG.public_url},
                ) as client:
                    if authenticated:
                        client.cookies.set("toir_session", "meeting_session")
                    stack.client, stack.auth, stack.sales_store = client, auth, sales_store
                    stack.prefix = "/api/meetings" if public else "/v1/meetings"
                    stack.bridge = bridge
                    yield stack
            finally:
                await bridge.close()


@pytest.mark.parametrize("public", [True, False])
def test_authenticated_demo_edit_approval_and_replay_use_persisted_service(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as stack:
            client, prefix = stack.client, stack.prefix
            key = str(uuid4())
            response = await client.post(prefix + "/demos", headers={"Idempotency-Key": key})
            assert response.status_code == 201, response.text
            meeting = response.json()
            assert meeting["id"] == key and meeting["owner_email"] == ACTOR["email"]
            assert (
                not {"fingerprint", "consent_confirmed", "created_by", "join_attempts"}
                & meeting.keys()
            )
            assert (
                await client.post(prefix + "/demos", headers={"Idempotency-Key": key})
            ).json() == meeting
            await stack.service.processing.analysis_tick()
            response = await client.get(prefix + "/workspace")
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            task = response.json()["tasks"][0]
            assert task["status"] == "pending" and not stack.github.calls
            edited = await client.patch(
                prefix + f"/tasks/{task['id']}",
                json={
                    "version": 1,
                    "title": "Reviewed CSV export issue",
                    "body": "Reviewed customer report. " * 3,
                },
            )
            assert edited.status_code == 200 and edited.json()["version"] == 2
            path = prefix + f"/tasks/{task['id']}/decisions"
            stale = await client.post(path, json={"version": 1, "decision": "approve"})
            assert stale.status_code == 409
            approved = await client.post(path, json={"version": 2, "decision": "approve"})
            assert approved.status_code == 200 and approved.json()["status"] == "approved"
            assert not stack.github.calls
            await stack.service.tasks.publish_one()
            replay = await client.post(path, json={"version": 2, "decision": "approve"})
            assert replay.status_code == 200 and replay.json()["status"] == "published"
            await stack.service.tasks.publish_one()
            assert len(stack.github.calls) == 1
            assert stack.github.calls[0]["body"] == edited.json()["body"]

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [True, False])
def test_anonymous_spoofed_identity_cross_origin_and_revoked_members_are_rejected(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public, authenticated=False) as stack:
            client, prefix = stack.client, stack.prefix
            headers = {"Idempotency-Key": str(uuid4()), "X-User-Email": ACTOR["email"]}
            assert (await client.get(prefix + "/workspace", headers=headers)).status_code == 401
            assert (await client.post(prefix + "/demos", headers=headers)).status_code == 401
            # An upstream pooled client's cookie jar cannot turn an anonymous request into a member.
            stack.bridge.client.cookies.set("toir_session", "meeting_session")
            assert (await client.get(prefix + "/workspace")).status_code == 401
            client.cookies.set("toir_session", "meeting_session")
            for origin in ["https://attacker.example", "null", ""]:
                denied = await client.post(prefix + "/demos", headers={**headers, "Origin": origin})
                assert denied.status_code == 403
            async with stack.sales_store.transaction() as tx:
                member = await tx.get("member", ACTOR["email"])
                await tx.put("member", ACTOR["email"], {**member, "active": False})
            assert (await client.get(prefix + "/workspace")).status_code == 403
            assert (await client.post(prefix + "/demos", headers=headers)).status_code == 403
            assert not await stack.store.list("meeting")
            assert not stack.github.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", ["webhook", "svix"])
def test_public_webhook_preserves_raw_bytes_and_requires_signature_without_browser_login(
    tmp_path, prefix
):
    async def scenario():
        async with route_stack(tmp_path, authenticated=False) as stack:
            path = stack.prefix + "/webhooks/recall/dashboard"
            body = event_body("bot.done") + b"\n  "
            headers = signed(body, prefix=prefix)
            response = await stack.client.post(path, content=body, headers=headers)
            assert response.status_code == 202, response.text
            replay = await stack.client.post(path, content=body, headers=headers)
            assert replay.status_code == 202 and len(await stack.store.list("event")) == 1
            tampered = await stack.client.post(path, content=body + b" ", headers=headers)
            assert tampered.status_code == 403
            unsigned = await stack.client.post(path, content=body)
            assert unsigned.status_code == 403
            unknown = await stack.client.post(
                stack.prefix + "/webhooks/recall/unknown", content=body
            )
            assert unknown.status_code == 422
            too_big = await stack.client.post(path, content=b" " * 1_000_001, headers=headers)
            assert too_big.status_code == 413
            assert len(await stack.store.list("event")) == 1

    asyncio.run(scenario())


def test_maintenance_pauses_commands_and_requests_webhook_redelivery(tmp_path):
    async def scenario():
        async with route_stack(tmp_path) as stack:
            stack.coordinator.maintenance = True
            response = await stack.client.post(
                stack.prefix + "/demos",
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 409
            body = event_body("bot.done")
            accepted = await stack.client.post(
                stack.prefix + "/webhooks/recall/dashboard",
                content=body,
                headers=signed(body),
            )
            assert accepted.status_code == 503
            assert accepted.headers["retry-after"] == "30"
            assert not await stack.store.list("event")
            stack.coordinator.maintenance = False
            retried = await stack.client.post(
                stack.prefix + "/webhooks/recall/dashboard",
                content=body,
                headers=signed(body),
            )
            assert retried.status_code == 202
            assert not await stack.store.list("meeting")
            assert len(await stack.store.list("event")) == 1

    asyncio.run(scenario())
