"""User ownership, issuer binding, durable lifecycle, and browser facade contracts."""

import asyncio
import json
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.orchestrator.sales.auth import AuthService
from apps.orchestrator.sales.auth_sessions import digest
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_sales_auth import CONFIG, auth_stack
from tooling.tests.test_sales_routes import route_stack

CURRAN = {"email": "curran@toirinc.com", "sub": "curran", "email_verified": True}
JARED = {"email": "jared@neptuneops.com", "sub": "jared", "email_verified": True}


def test_durable_identity_session_rotation_and_restart(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, query, cookie, _, store):
            token = await auth.callback("code", query["state"][0], cookie, user_agent="Chrome")
            first = await auth.current_user(token)
            async with store.transaction() as tx:
                member = await tx.get("member", first["email"])
                assert member["memory_user_id"] == first["email"]
                assert member["email_verified"] is True
                created = member["created_at"]
            await auth.sessions.establish(
                {**CURRAN, "sub": "user-curran", "name": "Updated name"},
                "new-cookie",
                previous_token=token,
            )
            with pytest.raises(HTTPException) as failure:
                await auth.current_user(token)
            assert failure.value.status_code == 401
            updated = await auth.current_user("new-cookie")
            assert updated["id"] == first["id"] and updated["name"] == "Updated name"
            async with store.transaction() as tx:
                assert (await tx.get("member", first["email"]))["created_at"] == created
                assert len(await tx.list("identity")) == 1
        repository = await SQLiteRunRepository.open(tmp_path / "auth.sqlite")
        try:
            restarted = AuthService(SalesStore(repository), CONFIG, client=object())
            assert await restarted.current_user("new-cookie") == updated
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_sessions_are_private_revocable_and_have_no_bearer_material(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, _, _, _, store):
            await auth.sessions.establish(CURRAN, "first", user_agent="Chrome\n" + "x" * 500)
            await auth.sessions.establish(CURRAN, "second")
            await auth.sessions.establish(JARED, "jared")
            sessions = await auth.sessions.list("first")
            assert len(sessions) == 2
            assert sum(s.current for s in sessions) == 1
            assert all(len(s.user_agent) <= 300 and "\n" not in s.user_agent for s in sessions)
            encoded = json.dumps([s.model_dump() for s in sessions])
            assert digest("first") not in encoded and '"first"' not in encoded
            other = (await auth.sessions.list("jared"))[0]
            with pytest.raises(HTTPException) as error:
                await auth.sessions.revoke("first", other.id)
            assert error.value.status_code == 404
            target = next(s for s in sessions if not s.current)
            assert not await auth.sessions.revoke("first", target.id)
            with pytest.raises(HTTPException):
                await auth.current_user("second")
            await auth.sessions.revoke("first")
            with pytest.raises(HTTPException):
                await auth.current_user("first")
            assert (await auth.current_user("jared"))["email"] == JARED["email"]
            async with store.transaction() as tx:
                assert len(await tx.list("member")) == 2  # Revoke never deletes users/history.

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changed",
    [
        {"sub": "different-subject"},
        {"email": JARED["email"]},
    ],
)
def test_no_automatic_identity_reassignment(tmp_path, changed):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, _, _, _, _store):
            await auth.sessions.establish(CURRAN, "original")
            with pytest.raises(HTTPException) as failure:
                await auth.sessions.establish({**CURRAN, **changed}, "new")
            assert failure.value.status_code == 403
            assert (await auth.current_user("original"))["email"] == CURRAN["email"]

    asyncio.run(scenario())


def test_issuer_change_rejects_existing_session_and_subject_reuse(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, _, _, _, store):
            await auth.sessions.establish(CURRAN, "original")
            other = AuthService(
                store, replace(CONFIG, environment_url="https://other.example"), client=object()
            )
            with pytest.raises(HTTPException) as failure:
                await other.current_user("original")
            assert failure.value.status_code == 401
            with pytest.raises(HTTPException) as failure:
                await other.sessions.establish(CURRAN, "new")
            assert failure.value.status_code == 403

    asyncio.run(scenario())


def test_expiry_activity_and_legacy_records(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, _, _, _, store):
            await auth.sessions.establish(CURRAN, "active")
            async with store.transaction() as tx:
                record = await tx.get("auth_session", digest("active"))
                expires = record["expires_at"]
                record["last_seen_at"] = time.time() - 120
                await tx.put("auth_session", digest("active"), record)
                await tx.put("auth_session", digest("expired"), {**record, "expires_at": 1})
                await tx.put("auth_flow", "expired-flow", {"expires_at": 1})
                legacy = {**record}
                del legacy["issuer"]
                await tx.put("auth_session", digest("legacy"), legacy)
            with pytest.raises(HTTPException):
                await auth.current_user("legacy")
            with pytest.raises(HTTPException):
                await auth.current_user("expired")
            await auth.login()
            assert len(await auth.sessions.list("active")) == 1
            async with store.transaction() as tx:
                saved = await tx.get("auth_session", digest("active"))
                assert saved["last_seen_at"] > record["last_seen_at"]
                assert saved["expires_at"] == expires  # No indefinite sliding lifetime.
                assert await tx.get("auth_session", digest("expired")) is None
                assert await tx.get("auth_flow", "expired-flow") is None

    asyncio.run(scenario())


@pytest.mark.parametrize("public", [True, False])
def test_session_routes_csrf_and_revocation(tmp_path, public):
    async def scenario():
        async with route_stack(tmp_path, public) as stack:
            client, path = stack.client, stack.prefix + "/auth/sessions"
            result = await client.get(path)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
            current = result.json()[0]
            assert current["current"]
            assert set(current) == {
                "id",
                "created_at",
                "expires_at",
                "last_seen_at",
                "current",
                "user_agent",
            }
            for suffix in ["", "/" + current["id"]]:
                denied = await client.delete(
                    path + suffix, headers={"Origin": "https://attacker.example"}
                )
                assert denied.status_code == 403
            assert (await client.delete(path + "/" + str(uuid4()))).status_code == 404
            assert (await client.delete(path + "/not-a-uuid")).status_code == 422
            revoked = await client.delete(path + "/" + current["id"])
            assert revoked.status_code == 204 and "Max-Age=0" in revoked.headers["set-cookie"]
            assert (await client.get(stack.prefix + "/me")).status_code == 401
            # Logout succeeds for an already expired/revoked/anonymous browser.
            logout = await client.post(stack.prefix + "/auth/logout")
            assert logout.status_code == 204

    asyncio.run(scenario())


def test_cancelled_callback_consumes_state_and_clears_flow_cookie(tmp_path):
    async def scenario():
        from urllib.parse import parse_qs, urlsplit

        async with route_stack(tmp_path, email=None) as stack:
            login = await stack.client.get("/api/auth/login")
            query = parse_qs(urlsplit(login.headers["location"]).query)
            assert query["provider"] == ["google"]
            response = await stack.client.get(
                "/api/auth/callback",
                params={
                    "state": query["state"][0],
                    "error": "access_denied",
                    "error_description": "secret",
                },
            )
            assert response.status_code == 400
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "Max-Age=0" in response.headers["set-cookie"]
            assert "secret" not in response.text
            assert not stack.provider_requests
            async with stack.store.transaction() as tx:
                assert not await tx.list("auth_flow")

    asyncio.run(scenario())
