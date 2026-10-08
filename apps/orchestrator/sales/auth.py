"""Scalekit OIDC login and opaque, durable sales-workspace sessions.

Protocol endpoints follow Scalekit's SDK and complete-login documentation. Browser
identity claims are never accepted; only a signature-verified ID token can enroll
one of the configured sales members. Provider tokens are not retained.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException

from apps.orchestrator.sales.auth_sessions import AuthSessions, prune_expired

SESSION_COOKIE = "toir_session"
FLOW_COOKIE = "toir_auth_flow"
FLOW_TTL_SECONDS = 600
DEFAULT_MEMBERS = frozenset({"curran@toirinc.com", "jared@neptuneops.com"})
DEFAULT_ENGINEERING_MEMBERS = frozenset({"curran@neptuneops.com"})


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _valid_origin(value: str, *, localhost: bool = False) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port  # Reject malformed ports.
        valid_scheme = parsed.scheme == "https" or (
            localhost and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        )
        return bool(
            valid_scheme
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class AuthConfig:
    environment_url: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    public_url: str = ""
    member_emails: frozenset[str] = DEFAULT_MEMBERS
    engineering_member_emails: frozenset[str] = DEFAULT_ENGINEERING_MEMBERS
    session_ttl_seconds: int = 8 * 60 * 60

    @classmethod
    def from_env(cls) -> AuthConfig:
        members = os.environ.get("SALES_MEMBER_EMAILS")
        engineering = os.environ.get("ENGINEERING_MEMBER_EMAILS")
        return cls(
            environment_url=os.environ.get("SCALEKIT_ENVIRONMENT_URL", "").rstrip("/"),
            client_id=os.environ.get("SCALEKIT_CLIENT_ID", ""),
            client_secret=os.environ.get("SCALEKIT_CLIENT_SECRET", ""),
            public_url=os.environ.get("SALES_PUBLIC_URL", "").rstrip("/"),
            member_emails=(
                frozenset(email.strip().lower() for email in members.split(",") if email.strip())
                if members is not None
                else DEFAULT_MEMBERS
            ),
            engineering_member_emails=(
                frozenset(
                    email.strip().lower() for email in engineering.split(",") if email.strip()
                )
                if engineering is not None
                else DEFAULT_ENGINEERING_MEMBERS
            ),
        )

    def department(self, email: str) -> str | None:
        # A restricted engineering identity never inherits sales access from an
        # overlapping allowlist. Identity and department come from server policy.
        if email in self.engineering_member_emails:
            return "engineering"
        return "sales" if email in self.member_emails else None

    @property
    def configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and (self.member_emails or self.engineering_member_emails)
            and _valid_origin(self.environment_url)
            and _valid_origin(self.public_url, localhost=True)
        )

    @property
    def callback_url(self) -> str:
        return self.public_url.rstrip("/") + "/api/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        return not self.public_url.startswith("http://")


class AuthService:
    def __init__(self, store, config: AuthConfig | None = None, client=None):
        self.store = store
        self.config = config or AuthConfig.from_env()
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self._owns_client = client is None
        self.sessions = AuthSessions(store, self.config)

    @property
    def configured(self) -> bool:
        return self.config.configured

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    def assert_same_origin(self, origin: str | None) -> None:
        if not self.config.configured or origin != self.config.public_url.rstrip("/"):
            raise HTTPException(403, "A same-origin browser request is required")

    async def login(self) -> tuple[str, str]:
        if not self.configured:
            raise HTTPException(503, "Scalekit sign-in is not configured")
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        async with self.store.transaction() as tx:
            await prune_expired(tx, "auth_flow", time.time())
            await tx.put(
                "auth_flow",
                _digest(state),
                {
                    "workspace_id": "toir",
                    "cookie_hash": _digest(cookie),
                    "verifier": verifier,
                    "nonce": nonce,
                    "expires_at": time.time() + FLOW_TTL_SECONDS,
                },
            )
        query = urlencode(
            {
                "response_type": "code",
                "provider": "google",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.callback_url,
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": _base64(hashlib.sha256(verifier.encode()).digest()),
                "code_challenge_method": "S256",
            }
        )
        return self.config.environment_url.rstrip("/") + "/oauth/authorize?" + query, cookie

    async def callback(
        self,
        code: str,
        state: str,
        flow_cookie: str | None,
        *,
        previous_token: str | None = None,
        user_agent: str = "",
        error: str = "",
    ) -> str:
        if not self.configured:
            raise HTTPException(503, "Scalekit sign-in is not configured")
        if (
            (not code and not error)
            or len(code) > 8192
            or not state
            or len(state) > 256
            or not flow_cookie
            or len(flow_cookie) > 256
        ):
            raise HTTPException(400, "Invalid sign-in callback")
        async with self.store.transaction() as tx:
            flow = await tx.get("auth_flow", _digest(state))
            if not flow or not hmac.compare_digest(flow["cookie_hash"], _digest(flow_cookie)):
                raise HTTPException(400, "Sign-in state does not match this browser")
            await tx.delete("auth_flow", _digest(state))
        # Consumed even if expired or the provider fails: a code is never retried silently.
        if flow["expires_at"] <= time.time():
            raise HTTPException(400, "Sign-in expired; start again")
        if error:
            raise HTTPException(400, "Google sign-in was cancelled; start again")
        claims = await self._exchange(code, flow)
        token = secrets.token_urlsafe(48)
        await self.sessions.establish(
            claims,
            token,
            previous_token=previous_token,
            user_agent=user_agent,
        )
        return token

    async def _exchange(self, code: str, flow: dict) -> dict:
        try:
            base = self.config.environment_url.rstrip("/")
            response = await self.client.post(
                base + "/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": code,
                    "redirect_uri": self.config.callback_url,
                    "code_verifier": flow["verifier"],
                },
            )
            response.raise_for_status()
            tokens = response.json()
            encoded = tokens["id_token"]
            header = jwt.get_unverified_header(encoded)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise ValueError("Unsupported ID token")
            response = await self.client.get(base + "/keys")
            response.raise_for_status()
            keys = [key for key in response.json()["keys"] if key.get("kid") == header["kid"]]
            if len(keys) != 1 or keys[0].get("use", "sig") != "sig":
                raise ValueError("Unknown signing key")
            key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key
            claims = jwt.decode(
                encoded,
                key,
                algorithms=["RS256"],
                audience=self.config.client_id,
                issuer=base,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Missing subject")
            if not hmac.compare_digest(str(claims["nonce"]), flow["nonce"]):
                raise ValueError("Nonce mismatch")
            audience = claims["aud"]
            if (isinstance(audience, list) and len(audience) > 1) or "azp" in claims:
                if claims.get("azp") != self.config.client_id:
                    raise ValueError("Authorized party mismatch")
            for claim, value in (("c_hash", code), ("at_hash", tokens.get("access_token"))):
                if claim in claims:
                    if not isinstance(value, str) or not hmac.compare_digest(
                        str(claims[claim]), _base64(hashlib.sha256(value.encode()).digest()[:16])
                    ):
                        raise ValueError("Token binding mismatch")
            return claims
        except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError):
            # Never include provider bodies, authorization codes or tokens in errors.
            raise HTTPException(
                401, "Scalekit sign-in could not be verified; start again"
            ) from None

    async def current_user(self, session_token: str | None) -> dict:
        return await self.sessions.current_user(session_token)

    async def logout(self, session_token: str | None) -> None:
        await self.sessions.logout(session_token)
