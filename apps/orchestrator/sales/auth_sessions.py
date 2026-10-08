"""Coordinator-owned identities and revocable browser sessions, never provider tokens."""

import hashlib
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel


class AuthSessionView(BaseModel):
    id: str
    created_at: float
    last_seen_at: float
    expires_at: float
    user_agent: str
    current: bool


class UserView(BaseModel):
    id: str
    email: str
    name: str
    workspace_id: str
    role: str
    email_verified: bool


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def prune_expired(tx, kind: str, now: float) -> None:
    for key, record in await tx.entries(kind):
        if record.get("expires_at", 0) <= now:
            await tx.delete(kind, key)


class AuthSessions:
    def __init__(self, store, config):
        self.store, self.config = store, config

    @property
    def issuer(self):
        return self.config.environment_url.rstrip("/")

    async def establish(self, claims, token, *, previous_token=None, user_agent=""):
        if claims.get("email_verified") is not True:
            raise HTTPException(403, "A verified email is required")
        email = str(claims.get("email", "")).strip().lower()
        department = self.config.department(email)
        if department is None:
            raise HTTPException(403, "This account is not a member of the Toir workspace")
        subject, now = claims["sub"], time.time()
        identity_key = "oidc:" + digest(self.issuer + "\n" + subject)
        async with self.store.transaction() as tx:
            identity = await tx.get("identity", identity_key)
            member = await tx.get("member", email)
            if identity and identity["email"] != email:
                raise HTTPException(403, "Identity changed; contact the workspace administrator")
            if member and (
                not member.get("active")
                or member.get("role") != department
                or member.get("subject") not in {None, subject}
                or member.get("issuer") not in {None, self.issuer}
            ):
                raise HTTPException(403, "Workspace membership is inactive or changed")
            user_id = (member or {}).get("user_id") or str(uuid4())
            member = {
                **(member or {}),
                "id": email,  # Preserve ownership/audit keys used by sales and Cognee.
                "user_id": user_id,
                "workspace_id": "toir",
                "email": email,
                "email_verified": True,
                "name": str(claims.get("name") or email)[:200],
                "role": department,
                "department": department,
                "active": True,
                "subject": subject,
                "issuer": self.issuer,
                "created_at": (member or {}).get("created_at", now),
                "last_login_at": now,
                "memory_user_id": email,
            }
            await tx.put("member", email, member)
            await tx.put(
                "identity",
                identity_key,
                {
                    "workspace_id": "toir",
                    "user_id": user_id,
                    "email": email,
                    "issuer": self.issuer,
                    "subject": subject,
                },
            )
            await prune_expired(tx, "auth_session", now)
            if previous_token:
                await tx.delete("auth_session", digest(previous_token))
            await tx.put(
                "auth_session",
                digest(token),
                {
                    "id": str(uuid4()),
                    "workspace_id": "toir",
                    "user_id": user_id,
                    "email": email,
                    "subject": subject,
                    "issuer": self.issuer,
                    "created_at": now,
                    "last_seen_at": now,
                    "expires_at": now + self.config.session_ttl_seconds,
                    # Informational only; never trusted for authentication or rendered as HTML.
                    "user_agent": "".join(c for c in user_agent[:300] if c.isprintable()),
                },
            )

    async def _resolve(self, tx, token):
        if not self.config.configured or not token or len(token) > 256:
            raise HTTPException(401, "Sign in to the Toir workspace")
        key, now = digest(token), time.time()
        session = await tx.get("auth_session", key)
        # Old sessions without an issuer/user ID must sign in once after upgrade.
        if (
            not session
            or session["expires_at"] <= now
            or session.get("issuer") != self.issuer
            or not session.get("user_id")
        ):
            raise HTTPException(401, "Your session has expired; sign in again")
        member = await tx.get("member", session["email"])
        if (
            not member
            or not member.get("active")
            or member.get("role") != self.config.department(session["email"])
            or member.get("subject") != session["subject"]
            or member.get("issuer") != self.issuer
            or member.get("user_id") != session["user_id"]
            or self.config.department(member.get("email")) is None
        ):
            raise HTTPException(403, "This account no longer has workspace access")
        if now - session.get("last_seen_at", 0) >= 60:
            session["last_seen_at"] = now
            await tx.put("auth_session", key, session)
        return session, member

    async def current_user(self, token):
        async with self.store.transaction() as tx:
            _, member = await self._resolve(tx, token)
        return UserView(
            id=member["user_id"],
            email=member["email"],
            name=member["name"],
            workspace_id="toir",
            role=member["role"],
            email_verified=True,
        ).model_dump()

    async def list(self, token) -> list[AuthSessionView]:
        async with self.store.transaction() as tx:
            current, _ = await self._resolve(tx, token)
            await prune_expired(tx, "auth_session", time.time())
            records = await tx.list("auth_session")
        return sorted(
            [
                AuthSessionView(
                    id=s["id"],
                    created_at=s["created_at"],
                    last_seen_at=s["last_seen_at"],
                    expires_at=s["expires_at"],
                    user_agent=s.get("user_agent", ""),
                    current=s["id"] == current["id"],
                )
                for s in records
                if s.get("user_id") == current["user_id"] and s.get("issuer") == self.issuer
            ],
            key=lambda s: s.created_at,
            reverse=True,
        )

    async def revoke(self, token, session_id=None):
        """Revoke one owned session or all owned sessions atomically with authorization."""
        async with self.store.transaction() as tx:
            current, _ = await self._resolve(tx, token)
            found = False
            for key, session in await tx.entries("auth_session"):
                if (
                    session.get("user_id") == current["user_id"]
                    and session.get("issuer") == self.issuer
                    and (session_id is None or session.get("id") == session_id)
                ):
                    await tx.delete("auth_session", key)
                    found = True
            if not found:
                raise HTTPException(404, "Session not found")
            return session_id is None or session_id == current["id"]

    async def logout(self, token):
        if token and len(token) <= 256:
            async with self.store.transaction() as tx:
                await tx.delete("auth_session", digest(token))
