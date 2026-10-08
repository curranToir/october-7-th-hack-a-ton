"""Real RSA verification and durable session/identity boundaries; no live provider calls."""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from apps.orchestrator.sales.auth import AuthConfig, AuthService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.sqlite import SQLiteRunRepository

CONFIG = AuthConfig(
    environment_url="https://toir.scalekit.example",
    client_id="test-client",
    client_secret="fixture-secret",
    public_url="https://toir.example",
)


@asynccontextmanager
async def auth_stack(tmp_path, *, overrides=None, algorithm="RS256"):
    repository = await SQLiteRunRepository.open(tmp_path / "auth.sqlite")
    store = SalesStore(repository)
    await store.setup()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", use="sig")
    seen = []
    claims = {}

    def provider(request):
        seen.append(request)
        if request.url.path == "/oauth/token":
            payload = {
                "iss": CONFIG.environment_url,
                "aud": CONFIG.client_id,
                "sub": "user-curran",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "email": "curran@toirinc.com",
                "email_verified": True,
                "name": "Curran",
                **claims,
                **(overrides or {}),
            }
            signed = jwt.encode(
                payload,
                key if algorithm == "RS256" else "wrong-secret",
                algorithm=algorithm,
                headers={"kid": "test-key"},
            )
            return httpx.Response(200, json={"id_token": signed})
        assert request.url.path == "/keys"
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        auth = AuthService(store, CONFIG, client)
        url, cookie = await auth.login()
        query = parse_qs(urlsplit(url).query)
        claims["nonce"] = query["nonce"][0]
        try:
            yield auth, query, cookie, seen, store
        finally:
            await auth.close()
            await repository.close()


@pytest.mark.parametrize("email", ["curran@toirinc.com", "jared@neptuneops.com"])
def test_verified_members_sign_in_and_tokens_are_not_stored(tmp_path, email):
    async def scenario():
        async with auth_stack(tmp_path, overrides={"email": email}) as (
            auth,
            query,
            cookie,
            seen,
            store,
        ):
            assert query["redirect_uri"] == ["https://toir.example/api/auth/callback"]
            assert query["code_challenge_method"] == ["S256"]
            assert query["provider"] == ["google"]
            token = await auth.callback("valid-code", query["state"][0], cookie)
            user = await auth.current_user(token)
            assert user == {
                "id": user["id"],
                "email_verified": True,
                "email": email,
                "name": "Curran",
                "workspace_id": "toir",
                "role": "sales",
            }
            request_data = parse_qs(seen[0].content.decode())
            assert request_data["client_secret"] == [CONFIG.client_secret]
            assert len(request_data["code_verifier"][0]) >= 43
            async with store.transaction() as tx:
                saved = await tx.get("auth_session", hashlib.sha256(token.encode()).hexdigest())
                assert saved["email"] == email
                assert token not in json.dumps(saved)
                assert "id_token" not in saved and "access_token" not in saved
                assert await tx.list("auth_flow") == []
            await auth.logout(token)
            with pytest.raises(HTTPException) as error:
                await auth.current_user(token)
            assert error.value.status_code == 401

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "claims, status",
    [
        ({"email_verified": False}, 403),
        ({"email_verified": "true"}, 403),
        ({"email": "outside@example.com"}, 403),
        ({"aud": "someone-elses-client"}, 401),
        ({"iss": "https://attacker.example"}, 401),
        ({"exp": 1}, 401),
        ({"nonce": "other-browser"}, 401),
        ({"aud": [CONFIG.client_id, "another"], "azp": "another"}, 401),
        ({"at_hash": "wrong"}, 401),
    ],
)
def test_unverified_identity_and_invalid_tokens_are_rejected(tmp_path, claims, status):
    async def scenario():
        async with auth_stack(tmp_path, overrides=claims) as (auth, query, cookie, _, store):
            with pytest.raises(HTTPException) as error:
                await auth.callback("valid-code", query["state"][0], cookie)
            assert error.value.status_code == status
            async with store.transaction() as tx:
                assert await tx.list("member") == []
                assert await tx.list("auth_session") == []

    asyncio.run(scenario())


def test_unsigned_or_symmetric_tokens_cannot_supply_identity(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path, algorithm="HS256") as (auth, query, cookie, _, _store):
            with pytest.raises(HTTPException) as error:
                await auth.callback("valid-code", query["state"][0], cookie)
            assert error.value.status_code == 401

    asyncio.run(scenario())


def test_state_is_browser_bound_and_atomically_single_use(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, query, cookie, seen, _store):
            with pytest.raises(HTTPException) as error:
                await auth.callback("valid-code", query["state"][0], "another-browser")
            assert error.value.status_code == 400
            assert not seen
            results = await asyncio.gather(
                auth.callback("valid-code", query["state"][0], cookie),
                auth.callback("valid-code", query["state"][0], cookie),
                return_exceptions=True,
            )
            assert sum(isinstance(result, str) for result in results) == 1
            assert sum(isinstance(result, HTTPException) for result in results) == 1
            assert sum(request.url.path == "/oauth/token" for request in seen) == 1

    asyncio.run(scenario())


def test_membership_revocation_takes_effect_on_existing_session(tmp_path):
    async def scenario():
        async with auth_stack(tmp_path) as (auth, query, cookie, _, store):
            token = await auth.callback("valid-code", query["state"][0], cookie)
            async with store.transaction() as tx:
                member = await tx.get("member", "curran@toirinc.com")
                member["active"] = False
                await tx.put("member", member["id"], member)
            with pytest.raises(HTTPException) as error:
                await auth.current_user(token)
            assert error.value.status_code == 403

    asyncio.run(scenario())


def test_configuration_and_csrf_require_fixed_trusted_origin():
    assert CONFIG.configured and CONFIG.secure_cookies
    local = AuthConfig(**{**CONFIG.__dict__, "public_url": "http://localhost:8080"})
    assert local.configured and not local.secure_cookies
    for url in [
        "http://remote.example",
        "https://user:secret@toir.example",
        "https://toir.example/path",
    ]:
        assert not AuthConfig(**{**CONFIG.__dict__, "public_url": url}).configured
    auth = AuthService(None, CONFIG, client=object())
    auth.assert_same_origin(CONFIG.public_url)
    for origin in [None, "null", "https://other.example", "https://toir.example.attacker.com"]:
        with pytest.raises(HTTPException) as error:
            auth.assert_same_origin(origin)
        assert error.value.status_code == 403
