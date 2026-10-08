"""Fixed GitHub issue tools through the selected user's Scalekit connection."""

import asyncio
import json
import os
import time
from urllib.parse import urlsplit

import httpx

from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure

ISSUE_TOOLS = {"github_issue_create", "github_issues_list", "github_repo_get"}


class ScalekitGitHub:
    def __init__(self, repository, client):
        self.repository, self.client = repository, client
        self.connection = os.getenv("MEETING_GITHUB_CONNECTION_NAME", "")
        self.identifier = os.getenv("MEETING_GITHUB_ACCOUNT_ID", "")
        self.base = os.getenv("SCALEKIT_ENVIRONMENT_URL", "").rstrip("/")
        self.client_id = os.getenv("SCALEKIT_CLIENT_ID", "")
        self._secret = os.getenv("SCALEKIT_CLIENT_SECRET", "")
        parsed = urlsplit(self.base)
        self.configured = bool(
            self.connection
            and self.identifier
            and self.client_id
            and self._secret
            and parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
        self._token = None
        self._token_until = 0.0
        self._auth_lock = asyncio.Lock()
        self._ready_lock = asyncio.Lock()
        self._ready_until = 0.0
        self.ready_value = False
        self.error = "Connect the selected GitHub account in Scalekit"

    async def token(self):
        if not self.configured:
            raise ProviderFailure("Scalekit GitHub settings or server credentials are missing")
        async with self._auth_lock:
            if self._token and time.monotonic() < self._token_until:
                return self._token
            try:
                response = await self.client.post(
                    self.base + "/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self._secret,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                token = payload.get("access_token")
                if not isinstance(token, str) or not token:
                    raise ValueError()
                self._token = token
                self._token_until = time.monotonic() + max(
                    0, min(float(payload.get("expires_in", 300)), 3600) - 30
                )
                return token
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                raise ProviderFailure("Scalekit server authentication failed") from None

    async def request(self, method, path, *, mutation=False, **kwargs):
        token = await self.token()  # Authentication failure precedes issue dispatch.
        try:
            response = await self.client.request(
                method,
                self.base + path,
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except httpx.HTTPError:
            error = OutcomeUnknown if mutation else ProviderFailure
            raise error("Scalekit GitHub did not return a confirmed result") from None
        if response.status_code in {401, 403}:
            self._token = None
            self.ready_value = False
            self._ready_until = 0
        if not response.is_success:
            error = OutcomeUnknown if mutation and response.status_code >= 500 else ProviderFailure
            raise error(f"Scalekit GitHub request failed (HTTP {response.status_code})")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error") or payload.get("isError"):
                raise ValueError()
            return payload
        except ValueError:
            error = OutcomeUnknown if mutation else ProviderFailure
            raise error("Scalekit GitHub returned an invalid result") from None

    async def execute(self, tool_name, params):
        if tool_name not in ISSUE_TOOLS:
            raise ProviderFailure("This GitHub operation is not permitted")
        result = await self.request(
            "POST",
            "/api/v1/execute_tool",
            mutation=tool_name == "github_issue_create",
            json={
                "tool_name": tool_name,
                "connector": self.connection,
                "identifier": self.identifier,
                "params": params,
            },
        )
        data = result.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = None
        if not isinstance(data, dict | list) or (
            isinstance(data, dict)
            and (data.get("error") or data.get("isError") or data.get("ok") is False)
        ):
            error = OutcomeUnknown if tool_name == "github_issue_create" else ProviderFailure
            raise error("Scalekit GitHub returned an unsuccessful tool result")
        return data

    async def ready(self, *, force=False):
        async with self._ready_lock:
            if not force and time.monotonic() < self._ready_until:
                return self.ready_value
            try:
                async with asyncio.timeout(10):
                    await self.check_ready()
                self.ready_value, self.error = True, None
            except ProviderFailure as error:
                self.ready_value, self.error = False, str(error)
            except (TimeoutError, ValueError, TypeError, AttributeError):
                self.ready_value = False
                self.error = "GitHub connection verification could not complete; retry shortly"
            self._ready_until = time.monotonic() + (60 if self.ready_value else 5)
            return self.ready_value

    async def check_ready(self):
        accounts = await self.request(
            "GET",
            "/api/v1/connected_accounts",
            params={
                "connector": self.connection,
                "identifier": self.identifier,
                "page_size": 50,
            },
        )
        matches = [
            row
            for row in accounts.get("connected_accounts", [])
            if isinstance(row, dict)
            and row.get("identifier") == self.identifier
            and row.get("connector") == self.connection
            and row.get("status") == "ACTIVE"
        ]
        if len(matches) != 1 or accounts.get("next_page_token"):
            raise ProviderFailure("The selected Scalekit GitHub account needs authorization")
        names, page, seen = set(), "", set()
        for _ in range(10):
            query = {
                "identifier": self.identifier,
                "filter.connection_names": self.connection,
                "page_size": 100,
            }
            if page:
                query["page_token"] = page
            payload = await self.request("GET", "/api/v1/tools/scoped", params=query)
            for item in payload.get("tools", []):
                if not isinstance(item, dict):
                    continue
                meta = item.get("tool", item)
                if not isinstance(meta, dict):
                    continue
                definition = meta.get("definition", meta)
                if not isinstance(definition, dict):
                    continue
                name = definition.get("name")
                if name in ISSUE_TOOLS:
                    schema = definition.get("input_schema", {})
                    required = {"owner", "repo"} | (
                        {"title"} if name == "github_issue_create" else set()
                    )
                    if isinstance(schema, dict) and required <= set(schema.get("required", [])):
                        names.add(name)
            page = payload.get("next_page_token", "")
            if not page:
                break
            if page in seen:
                raise ProviderFailure("Scalekit GitHub tool pagination did not complete")
            seen.add(page)
        else:
            raise ProviderFailure("Scalekit GitHub tool pagination exceeded its limit")
        if ISSUE_TOOLS - names:
            raise ProviderFailure("The selected connection lacks GitHub issue create/read tools")
        owner, repo = self.repository.split("/")
        metadata = await self.execute("github_repo_get", {"owner": owner, "repo": repo})
        if not isinstance(metadata, dict) or (
            str(metadata.get("full_name", "")).casefold() != self.repository.casefold()
            or metadata.get("has_issues") is not True
            or metadata.get("archived") is True
            or metadata.get("disabled") is True
        ):
            raise ProviderFailure("The selected GitHub repository is unavailable for issues")
