"""Exercise real coordinator routes and authentication through the browser facade."""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from apps.api.auth_routes import router as public_auth
from apps.api.sales_client import SalesClient, get_sales_client
from apps.api.sales_routes import router as public_sales
from apps.orchestrator.sales.auth import AuthConfig, AuthService
from apps.orchestrator.sales.chat import ChatService
from apps.orchestrator.sales.models import Automation
from apps.orchestrator.sales.proposals import ProposalService
from apps.orchestrator.sales.routes import auth_router, router
from apps.orchestrator.sales.service import SalesService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_sales_proposals import example_proposal

CONFIG = AuthConfig(
    environment_url="https://toir.scalekit.example",
    client_id="fixture-client",
    client_secret="fixture-secret",
    public_url="https://toir.example",
)
EMAIL = "curran@toirinc.com"


class FakeJobs:
    async def cancel(self, job_id):
        raise LookupError("No job")

    async def retry(self, job_id):
        raise Conflict("Only interrupted or failed jobs can be retried")


class RouteService:
    """Use real durable commands but no background workers or external research."""

    workspace = SalesService.workspace
    update_automation = SalesService.update_automation

    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.chat = ChatService(store, models=None, brain=None)
        self.proposals = ProposalService(store)
        self.scheduler = FakeJobs()

    async def capabilities(self):
        return {"ready": False, "reasons": ["Test workers are disabled"], "auth": True}


@asynccontextmanager
async def route_stack(tmp_path, public=True, email=EMAIL):
    repository = await SQLiteRunRepository.open(tmp_path / "sales-routes.sqlite")
    store = SalesStore(repository)
    await store.setup()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="route-key", use="sig")
    provider_claims = {}
    provider_requests = []

    def provider(request):
        provider_requests.append(request)
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        assert request.url.path == "/oauth/token"
        claims = {
            "iss": CONFIG.environment_url,
            "aud": CONFIG.client_id,
            "sub": "verified-subject",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "email": EMAIL,
            "email_verified": True,
            "name": "Curran",
            **provider_claims,
        }
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "route-key"})
        return httpx.Response(200, json={"id_token": token})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    auth = AuthService(store, CONFIG, upstream)
    service = RouteService(store, auth)
    internal = FastAPI()
    internal.include_router(auth_router)
    internal.include_router(router)
    internal.state.sales_service = service
    bridge = SalesClient(
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal), base_url="http://coordinator"
        )
    )
    browser = FastAPI()
    browser.include_router(public_auth)
    browser.include_router(public_sales)
    browser.dependency_overrides[get_sales_client] = lambda: bridge
    async with store.transaction() as tx:
        await tx.put("automation", "default", Automation().model_dump(mode="json"))
        if email:
            await tx.put(
                "member",
                email,
                {
                    "email": email,
                    "name": "Sales member",
                    "subject": "verified-subject",
                    "role": "sales",
                    "active": True,
                },
            )
            await tx.put(
                "auth_session",
                hashlib.sha256(b"route_session").hexdigest(),
                {
                    "email": email,
                    "subject": "verified-subject",
                    "expires_at": time.time() + 60,
                },
            )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=browser if public else internal),
            base_url=CONFIG.public_url,
            headers={"Origin": CONFIG.public_url},
        ) as client:
            if email:
                client.cookies.set("toir_session", "route_session")
            yield SimpleNamespace(
                client=client,
                store=store,
                service=service,
                prefix="/api" if public else "/v1",
                provider_claims=provider_claims,
                provider_requests=provider_requests,
            )
    finally:
        await bridge.close()
        await upstream.aclose()
        await repository.close()


def test_real_login_callback_cookie_session_and_logout(tmp_path):
    async def scenario():
        async with route_stack(tmp_path, email=None) as stack:
            client = stack.client
            login = await client.get("/api/auth/login?redirect=https://attacker.example")
            assert login.status_code == 303
            target = urlsplit(login.headers["location"])
            assert target.scheme == "https" and target.netloc == "toir.scalekit.example"
            query = parse_qs(target.query)
            assert query["redirect_uri"] == [CONFIG.public_url + "/api/auth/callback"]
            assert query["code_challenge_method"] == ["S256"]
            flow_cookie = login.headers["set-cookie"]
            for expected in ["HttpOnly", "Secure", "SameSite=lax", "Path=/api/auth", "Max-Age=600"]:
                assert expected in flow_cookie
            stack.provider_claims["nonce"] = query["nonce"][0]
            callback = await client.get(
                "/api/auth/callback",
                params={
                    "code": "valid-code",
                    "state": query["state"][0],
                },
            )
            assert callback.status_code == 303
            assert callback.headers["location"] == CONFIG.public_url
            cookies = callback.headers.get_list("set-cookie")
            assert len(cookies) == 2
            assert "toir_session=" in cookies[0] and "HttpOnly" in cookies[0]
            assert "Secure" in cookies[0] and "Max-Age=28800" in cookies[0]
            assert "toir_auth_flow=" in cookies[1] and "Max-Age=0" in cookies[1]
            assert callback.headers["cache-control"] == "no-store"
            user = await client.get("/api/me")
            assert user.status_code == 200 and user.json()["email"] == EMAIL
            assert set(user.json()) == {"email", "name", "workspace_id", "role"}
            old_cookie = client.cookies.get("toir_session")
            logout = await client.post("/api/auth/logout")
            assert logout.status_code == 204 and not logout.content
            assert "Max-Age=0" in logout.headers["set-cookie"]
            assert (await client.get("/api/me")).status_code == 401
            assert (
                await client.get(
                    "/api/me",
                    headers={
                        "Cookie": f"toir_session={old_cookie}",
                    },
                )
            ).status_code == 401

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("email,status", [(None, 401), ("outside@example.com", 403)])
def test_all_sales_routes_deny_missing_or_unauthorized_identity(tmp_path, public, email, status):
    async def scenario():
        async with route_stack(tmp_path, public, email) as stack:
            prefix = stack.prefix
            session, task, job = str(uuid4()), str(uuid4()), str(uuid4())
            requests = [
                ("GET", "/me", None),
                ("GET", "/sales/workspace", None),
                ("GET", "/sales/capabilities", None),
                ("POST", "/sales/sessions", {}),
                ("PATCH", f"/sales/sessions/{session}", {"title": "Changed"}),
                ("DELETE", f"/sales/sessions/{session}", None),
                ("POST", f"/sales/sessions/{session}/messages", {"content": "Research Acme"}),
                ("PATCH", f"/sales/tasks/{task}", {"version": 1}),
                ("POST", f"/sales/tasks/{task}/decisions", {"version": 1, "decision": "approved"}),
                ("POST", f"/sales/tasks/{task}/retries", None),
                ("PATCH", "/sales/automation", {"enabled": True}),
                ("POST", f"/sales/jobs/{job}/cancellation", None),
                ("POST", f"/sales/jobs/{job}/retries", None),
                ("POST", "/auth/logout", None),
            ]
            for method, path, body in requests:
                response = await stack.client.request(
                    method,
                    prefix + path,
                    json=body,
                    headers={
                        "Idempotency-Key": str(uuid4()),
                        "X-User-Email": EMAIL,
                        "Authorization": "Bearer forged-identity",
                    },
                )
                assert response.status_code == status, (method, path, response.text)
            async with stack.store.transaction() as tx:
                assert not await tx.list("session")
                assert not await tx.list("job")
                assert (await tx.get("automation", "default"))["enabled"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("email", [EMAIL, "jared@neptuneops.com"])
def test_real_session_message_persistence_idempotency_and_shared_workspace(tmp_path, public, email):
    async def scenario():
        async with route_stack(tmp_path, public, email) as stack:
            prefix, client = stack.prefix + "/sales", stack.client
            created = await client.post(prefix + "/sessions", json={})
            assert created.status_code == 201
            session = created.json()
            assert session["owner"] == email and session["workspace_id"] == "toir"
            path = f"{prefix}/sessions/{session['id']}"
            renamed = await client.patch(path, json={"title": "Targets"})
            assert renamed.status_code == 200 and renamed.json()["title"] == "Targets"
            headers = {"Idempotency-Key": str(uuid4())}
            body = {"content": "Research Acme and prepare CRM additions"}
            first = await client.post(path + "/messages", json=body, headers=headers)
            second = await client.post(path + "/messages", json=body, headers=headers)
            assert first.status_code == second.status_code == 202
            assert first.json() == second.json()
            conflict = await client.post(
                path + "/messages", json={"content": "Changed"}, headers=headers
            )
            assert conflict.status_code == 409
            assert (await client.post(path + "/messages", json=body)).status_code == 422
            workspace = await client.get(prefix + "/workspace")
            assert workspace.status_code == 200
            assert workspace.json()["messages"][0]["id"] == first.json()["id"]
            assert workspace.json()["jobs"][0]["requested_by"] == email
            assert workspace.json()["tasks"] == []
            deleted = await client.delete(path)
            assert deleted.status_code == 204 and not deleted.content
            assert (await client.patch(path, json={"title": "Deleted"})).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
def test_proposal_edit_stale_atomic_approval_replay_and_retry_errors(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as stack:
            proposal = example_proposal().model_copy(
                update={
                    "id": str(uuid4()),
                    "session_id": str(uuid4()),
                    "job_id": str(uuid4()),
                }
            )
            async with stack.store.transaction() as tx:
                await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            path = f"{stack.prefix}/sales/tasks/{proposal.id}"
            edited = await stack.client.patch(
                path,
                json={
                    "version": 1,
                    "excluded_contact_ids": ["alex"],
                },
            )
            assert edited.status_code == 200 and edited.json()["version"] == 2
            assert edited.json()["excluded_operation_ids"] == ["association", "contact", "note"]
            headers = {"Idempotency-Key": str(uuid4())}
            stale = await stack.client.post(
                path + "/decisions",
                json={
                    "version": 1,
                    "decision": "approved",
                },
                headers=headers,
            )
            assert stale.status_code == 409
            decision = {"version": 2, "decision": "approved"}
            responses = await asyncio.gather(
                *[
                    stack.client.post(path + "/decisions", json=decision, headers=headers)
                    for _ in range(2)
                ]
            )
            assert all(response.status_code == 200 for response in responses)
            assert responses[0].json() == responses[1].json()
            approved = responses[0].json()
            assert approved["status"] == "approved" and approved["execution"] == "queued"
            assert approved["decided_by"] == EMAIL
            async with stack.store.transaction() as tx:
                assert len(await tx.list("decision")) == 1
                assert await tx.list("crm_operation") == []  # Approval performs zero CRM writes.
            conflict = await stack.client.post(
                path + "/decisions",
                json={
                    "version": 2,
                    "decision": "denied",
                },
                headers=headers,
            )
            assert conflict.status_code == 409
            assert (await stack.client.post(path + "/retries")).status_code == 409
            assert (await stack.client.patch(path, json={"version": 2})).status_code == 409

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [False, True])
def test_mutation_csrf_uuid_validation_and_actor_injection_rejection(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as stack:
            client, prefix = stack.client, stack.prefix + "/sales"
            for origin in ["", "null", "https://attacker.example"]:
                response = await client.post(
                    prefix + "/sessions", json={}, headers={"Origin": origin}
                )
                assert response.status_code == 403
                logout = await client.post(
                    stack.prefix + "/auth/logout", headers={"Origin": origin}
                )
                assert logout.status_code == 403
            for body in [
                {"owner": "outside@example.com"},
                {"as_user": EMAIL},
                {"workspace_id": "other"},
            ]:
                assert (await client.post(prefix + "/sessions", json=body)).status_code == 422
            assert (
                await client.patch(prefix + "/sessions/not-uuid", json={"title": "X"})
            ).status_code == 422
            assert (
                await client.patch(prefix + "/tasks/not-uuid", json={"version": 1})
            ).status_code == 422
            assert (await client.post(prefix + "/jobs/not-uuid/retries")).status_code == 422
            unknown = str(uuid4())
            assert (await client.post(prefix + f"/jobs/{unknown}/cancellation")).status_code == 404
            assert (await client.post(prefix + f"/jobs/{unknown}/retries")).status_code == 409
            bad_update = await client.patch(
                prefix + "/automation", json={"owner": "outside@example.com"}
            )
            assert bad_update.status_code == 422
            updated = await client.patch(prefix + "/automation", json={"daily_enrichments": 12})
            assert updated.status_code == 200 and updated.json()["daily_enrichments"] == 12
            assert (
                await client.patch(prefix + "/automation", json={"employee_max": 1})
            ).status_code == 422
            async with stack.store.transaction() as tx:
                assert await tx.list("session") == []

    asyncio.run(scenario())
