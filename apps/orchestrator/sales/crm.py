"""Restricted server-only HubSpot access through Scalekit's documented REST API.

Provider contract: https://docs.scalekit.com/agentkit/connectors/hubspot/
No tool names, credentials, or account identifiers are accepted from research output.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

READ_TOOLS = frozenset(
    {
        "hubspot_companies_search",
        "hubspot_contacts_search",
        "hubspot_company_get",
        "hubspot_contact_get",
        "hubspot_associations_batch_read",
    }
)
WRITE_TOOLS = frozenset(
    {
        "hubspot_company_create",
        "hubspot_company_update",
        "hubspot_contacts_batch_create",
        "hubspot_contact_update",
        "hubspot_association_create",
        "hubspot_note_create",
    }
)
COMPANY_FIELDS = ("name", "domain", "description", "country", "numberofemployees")
CONTACT_FIELDS = ("firstname", "lastname", "email", "phone", "jobtitle", "company")


class CRMError(Exception):
    """Sanitized provider failure; never retains an HTTP request or secret."""

    def __init__(self, message: str, *, uncertain: bool = False, code: str = "provider"):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


class AmbiguousMatch(CRMError):
    pass


def canonical_domain(value: str) -> str:
    host = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
    return host.lower().removeprefix("www.").rstrip(".")


def same_value(left: Any, right: Any) -> bool:
    # HubSpot represents numeric property values as strings and missing values as null/empty.
    return str(left if left is not None else "") == str(right if right is not None else "")


class ScalekitCRM:
    def __init__(
        self,
        environment_url: str = "",
        client_id: str = "",
        client_secret: str = "",
        *,
        connection: str = "hubspot",
        identifier: str = "curran@toirinc.com",
        client: httpx.AsyncClient | None = None,
        write_scopes_verified: bool = False,
    ):
        parsed = urlsplit(environment_url)
        self.configured = bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and client_id
            and client_secret
        )
        self.base_url = environment_url.rstrip("/")
        self.client_id, self._secret = client_id, client_secret
        self.connection, self.identifier = connection, identifier
        self.write_scopes_verified = write_scopes_verified
        self._client = client or httpx.AsyncClient(timeout=25, follow_redirects=False)
        self._owns_client = client is None
        self._token: str | None = None
        self._token_until = 0.0
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ScalekitCRM:
        env = os.environ if env is None else env
        return cls(
            env.get("SCALEKIT_ENVIRONMENT_URL", ""),
            env.get("SCALEKIT_CLIENT_ID", ""),
            env.get("SCALEKIT_CLIENT_SECRET", ""),
            connection=env.get("SCALEKIT_HUBSPOT_CONNECTION", "hubspot"),
            identifier=env.get("SCALEKIT_HUBSPOT_IDENTIFIER", "curran@toirinc.com"),
            write_scopes_verified=env.get("SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED", "false").lower()
            == "true",
        )

    async def close(self):
        if self._owns_client:
            await self._client.aclose()

    async def _access_token(self) -> str:
        if not self.configured:
            raise CRMError("Scalekit CRM credentials are missing or invalid.", code="configuration")
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_until:
                return self._token
            try:
                response = await self._client.post(
                    f"{self.base_url}/oauth/token",
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
                    raise ValueError("Missing access token")
                self._token = token
                self._token_until = time.monotonic() + max(
                    0, min(float(payload.get("expires_in", 300)), 3600) - 30
                )
                return token
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                raise CRMError(
                    "Scalekit server authentication failed.", code="connection"
                ) from None

    async def _request(self, method: str, path: str, *, mutation=False, **kwargs) -> dict:
        token = await self._access_token()
        try:
            response = await self._client.request(
                method,
                self.base_url + path,
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except httpx.HTTPError:
            raise CRMError(
                "CRM request did not return a confirmed result.",
                uncertain=mutation,
            ) from None
        if response.status_code in {401, 403}:
            self._token = None
            raise CRMError("The HubSpot connection needs authorization.", code="connection")
        if response.status_code == 429:
            raise CRMError("The CRM rate limit was reached. Retry later.", code="rate_limit")
        if response.is_error:
            raise CRMError(
                "Scalekit could not complete the CRM request.",
                uncertain=mutation and response.status_code >= 500,
            )
        try:
            payload = response.json()
        except ValueError:
            raise CRMError("CRM returned an invalid response.", uncertain=mutation) from None
        if not isinstance(payload, dict):
            raise CRMError("CRM returned an invalid response.", uncertain=mutation)
        if payload.get("error") or payload.get("isError"):
            raise CRMError("CRM returned an unsuccessful result.", uncertain=mutation)
        return payload

    async def _execute(self, tool_name: str, params: dict, *, write=False) -> dict:
        if tool_name not in (WRITE_TOOLS if write else READ_TOOLS):
            raise CRMError("This CRM operation is not permitted.", code="allowlist")
        result = await self._request(
            "POST",
            "/api/v1/execute_tool",
            mutation=write,
            json={
                "tool_name": tool_name,
                "connector": self.connection,
                "identifier": self.identifier,
                "params": params,
            },
        )
        data = result.get("data")
        if not isinstance(data, dict) or data.get("error") or data.get("isError"):
            raise CRMError("CRM returned an unsuccessful tool result.", uncertain=write)
        return data

    async def capabilities(self) -> dict:
        """Read scoped tool permissions and perform searches; never test writes."""
        if not self.configured:
            return {"ready": False, "reasons": ["Scalekit CRM credentials are missing."]}
        try:
            names, token, seen = set(), "", set()
            for _ in range(20):
                params = {
                    "identifier": self.identifier,
                    "filter.connection_names": self.connection,
                    "page_size": 100,
                }
                if token:
                    params["page_token"] = token
                payload = await self._request("GET", "/api/v1/tools/scoped", params=params)
                for tool in payload.get("tools", []):
                    if isinstance(tool, dict):
                        metadata = tool.get("tool", tool)
                        definition = metadata.get("definition", metadata)
                        names.add(definition.get("name") or definition.get("tool_name"))
                names.update(payload.get("tool_names", []))
                token = payload.get("next_page_token", "")
                if not token:
                    break
                if token in seen:
                    raise CRMError("Scalekit tool pagination did not finish.")
                seen.add(token)
            else:
                raise CRMError("Scalekit tool pagination exceeded its limit.")
            missing = sorted((READ_TOOLS | WRITE_TOOLS) - names)
            if missing:
                return {
                    "ready": False,
                    "reasons": ["HubSpot tools unavailable: " + ", ".join(missing)],
                }
            await self.find_company("readiness-check.invalid")
            await self._search("contacts", {"query": "readiness-check.invalid"})
            return {
                "ready": self.write_scopes_verified,
                "reasons": []
                if self.write_scopes_verified
                else ["HubSpot write scopes need server-side operator verification."],
                "read_verified": True,
                "write_tested": False,
                "write_scopes_verified": self.write_scopes_verified,
            }
        except CRMError as error:
            return {"ready": False, "reasons": [str(error)]}

    async def _search(self, kind: str, params: dict) -> list[dict]:
        records, after, seen = [], None, set()
        for _ in range(5):
            query = {
                "limit": 100,
                "properties": list(COMPANY_FIELDS if kind == "companies" else CONTACT_FIELDS),
                **params,
            }
            if after:
                query["after"] = after
            payload = await self._execute(f"hubspot_{kind}_search", query)
            rows = payload.get("results")
            if not isinstance(rows, list) or any(
                not isinstance(r, dict)
                or not r.get("id")
                or not isinstance(r.get("properties"), dict)
                for r in rows
            ):
                raise CRMError("CRM search returned invalid records.")
            records.extend(rows)
            after = (payload.get("paging") or {}).get("next", {}).get("after")
            if after is None:
                total = payload.get("total")
                if total is not None and int(total) > len(records):
                    raise AmbiguousMatch("CRM search returned an incomplete result set.")
                return records
            after = str(after)
            if after in seen:
                break
            seen.add(after)
        raise AmbiguousMatch("CRM search was too broad to resolve safely.", code="ambiguous")

    async def find_company(self, domain: str) -> dict | None:
        domain = canonical_domain(domain)
        # Live-verified 2026-10-07: the connector forwards filterGroups verbatim.
        # Its string schema is incorrect; HubSpot requires a native array.
        rows = await self._search(
            "companies",
            {
                "filterGroups": [
                    {"filters": [{"propertyName": "domain", "operator": "EQ", "value": value}]}
                    for value in (domain, f"www.{domain}")
                ]
            },
        )
        # Domain fields can contain full URLs (with paths or trailing slashes). A broad
        # search catches those forms; _search requires all pages before we match exactly.
        rows.extend(await self._search("companies", {"query": domain}))
        matches = list(
            {
                str(row["id"]): row
                for row in rows
                if canonical_domain(row["properties"].get("domain") or "") == domain
            }.values()
        )
        if len(matches) > 1:
            raise AmbiguousMatch("Multiple HubSpot companies have this domain.", code="ambiguous")
        return matches[0] if matches else None

    async def get_record(self, kind: str, record_id: str) -> dict:
        if kind not in {"company", "contact"}:
            raise CRMError("Unsupported CRM record.", code="allowlist")
        result = await self._execute(
            f"hubspot_{kind}_get",
            {
                f"{kind}_id": record_id,
                "properties": ",".join(COMPANY_FIELDS if kind == "company" else CONTACT_FIELDS),
            },
        )
        if str(result.get("id")) != str(record_id) or not isinstance(
            result.get("properties"), dict
        ):
            raise CRMError("CRM returned an unexpected record.")
        return result

    async def company_associations(self, contact_id: str) -> set[str]:
        payload = await self._execute(
            "hubspot_associations_batch_read",
            {
                "from_object_type": "contacts",
                "to_object_type": "companies",
                "inputs": json.dumps([{"id": contact_id}]),
            },
        )
        rows = payload.get("results")
        if not isinstance(rows, list) or payload.get("errors"):
            raise CRMError("Could not verify contact company associations.")
        ids = set()
        for row in rows:
            if str(row.get("from", {}).get("id")) == str(contact_id):
                if row.get("paging"):
                    raise AmbiguousMatch("Contact has too many company associations to verify.")
                for target in row.get("to", []):
                    value = target.get("toObjectId", target.get("id"))
                    if value is not None:
                        ids.add(str(value))
        return ids

    async def find_contact(
        self, name: str, email: str | None, company_id: str | None
    ) -> dict | None:
        normalized_name = " ".join(name.casefold().split())
        if email:
            params = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "email",
                                "operator": "EQ",
                                "value": email,
                            }
                        ]
                    }
                ]
            }
        else:
            params = {"query": name}
        rows = await self._search("contacts", params)
        exact, ambiguous = [], False
        for row in rows:
            props = row["properties"]
            candidate = " ".join(
                f"{props.get('firstname') or ''} {props.get('lastname') or ''}".casefold().split()
            )
            if email and str(props.get("email") or "").casefold() == email.casefold():
                exact.append(row)
            elif not email and candidate == normalized_name:
                if company_id and company_id in await self.company_associations(str(row["id"])):
                    exact.append(row)
                else:
                    ambiguous = True
        if len(exact) > 1 or (not exact and ambiguous):
            raise AmbiguousMatch(
                "Contact identity or company association needs review.", code="ambiguous"
            )
        return exact[0] if exact else None

    async def write(
        self, kind: str, action: str, properties: dict, record_id: str | None = None
    ) -> str:
        """Called only by the approved-operation executor, never exposed to agents or browsers."""
        if kind == "company" and action in {"create", "update"}:
            if set(properties) - set(COMPANY_FIELDS):
                raise CRMError("Unsupported company fields.", code="allowlist")
            params = dict(properties)
            if action == "update":
                params["company_id"] = record_id
            result = await self._execute(f"hubspot_company_{action}", params, write=True)
        elif kind == "contact" and action in {"create", "update"}:
            if set(properties) - set(CONTACT_FIELDS):
                raise CRMError("Unsupported contact fields.", code="allowlist")
            if action == "create":
                # Unlike contact_create, batch creation supports verified people without email.
                result = await self._execute(
                    "hubspot_contacts_batch_create",
                    {
                        "inputs": json.dumps([{"properties": properties}]),
                    },
                    write=True,
                )
                rows = result.get("results")
                if result.get("errors") or not isinstance(rows, list) or len(rows) != 1:
                    raise CRMError("Contact creation needs reconciliation.", uncertain=True)
                result = rows[0]
            else:
                result = await self._execute(
                    "hubspot_contact_update",
                    {
                        "contact_id": record_id,
                        **properties,
                    },
                    write=True,
                )
        elif kind == "note" and action == "create":
            if set(properties) != {"hs_note_body", "hs_timestamp"}:
                raise CRMError("Unsupported note fields.", code="allowlist")
            result = await self._execute("hubspot_note_create", {"props": properties}, write=True)
        elif kind == "association" and action == "associate":
            if set(properties) != {
                "from_object_type",
                "from_object_id",
                "to_object_type",
                "to_object_id",
            } or (properties["from_object_type"], properties["to_object_type"]) not in {
                ("contacts", "companies"),
                ("notes", "companies"),
                ("notes", "contacts"),
            }:
                raise CRMError("Unsupported association.", code="allowlist")
            await self._execute("hubspot_association_create", properties, write=True)
            return f"{properties['from_object_id']}:{properties['to_object_id']}"
        else:
            raise CRMError("Unsupported CRM operation.", code="allowlist")
        value = result.get("id")
        if not isinstance(value, str | int) or not str(value):
            raise CRMError(
                "CRM write returned no record ID; reconciliation required.", uncertain=True
            )
        return str(value)
