"""Permission-aware Brain API boundary and leased, durable memory outbox.

The Spark owner must advertise idempotent ingestion before this client can write.
The existing recall/remember body shapes remain unchanged; ingestion identity and
workflow metadata live inside report, as supported by the existing API contract.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import httpx

from apps.orchestrator.sales.models import ContactReport, Job, Proposal, timestamp

SALES_USERS = ("curran@toirinc.com", "jared@neptuneops.com")
SHARED_DATASETS = frozenset({"toir-pipeline", "toir-firm"})
REQUEST_TIMEOUT_SECONDS = 30
CAPABILITY_TIMEOUT_SECONDS = 12
OUTBOX_LEASE_SECONDS = 120
MAX_RECALL_CHARS = 20_000


def verified_sales_user(user: str) -> str:
    # Caller is the server's authenticated session or its persisted requester.
    # Never accept arbitrary identifiers or fall back to the connector owner.
    if not isinstance(user, str) or user not in SALES_USERS:
        raise RuntimeError("This identity is not authorized for sales memory")
    return user


class BrainClient:
    def __init__(self, client=None):
        self.url = os.environ.get("BRAIN_API_URL", "").rstrip("/")
        self.token = os.environ.get("BRAIN_API_TOKEN", "")
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))
        self.ingestion_ready_until = 0.0

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, *, payload=None, key=None, success=(200,)):
        if not self.url or not self.token:
            raise RuntimeError("Brain API connection is not configured")
        headers = {"Authorization": f"Bearer {self.token}"}
        if key:
            headers["Idempotency-Key"] = key
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                response = await self.client.request(
                    method,
                    self.url + path,
                    json=payload,
                    headers=headers,
                )
            if response.status_code not in success:
                raise RuntimeError(f"Brain API returned HTTP {response.status_code}")
            if len(response.content) > 2_000_000:
                raise RuntimeError("Brain API returned an oversized response")
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Brain API returned an invalid response")
            return data
        except (httpx.HTTPError, ValueError, TimeoutError):
            raise RuntimeError("Brain API is temporarily unavailable") from None

    async def access(self, user):
        verified_sales_user(user)
        access = await self.request("GET", "/access/" + quote(user, safe=""))
        readable = access.get("readable")
        if not isinstance(readable, list) or not all(isinstance(item, str) for item in readable):
            raise RuntimeError("Brain API returned invalid access permissions")
        return set(readable)

    async def capabilities(self):
        self.ingestion_ready_until = 0.0
        reasons = []
        try:
            async with asyncio.timeout(CAPABILITY_TIMEOUT_SECONDS):
                health = await self.request("GET", "/health")
                if health.get("status") != "ok":
                    reasons.append("Brain API health is not ready")
                for user in SALES_USERS:
                    if "toir-pipeline" not in await self.access(user):
                        reasons.append(f"Pipeline read access is missing for {user}")
                capabilities = await self.request("GET", "/capabilities")
                if capabilities.get("research_idempotency") is not True:
                    reasons.append("Brain API needs idempotent research ingestion")
                writers = capabilities.get("research_writers")
                if not isinstance(writers, list):
                    writers = []
                for user in SALES_USERS:
                    if user not in writers:
                        reasons.append(f"Pipeline ingestion access is missing for {user}")
        except RuntimeError as error:
            reasons.append(str(error))
        except TimeoutError:
            reasons.append("Brain API readiness check timed out")
        if not reasons:
            self.ingestion_ready_until = time.monotonic() + 20
        return {"ready": not reasons, "reasons": reasons}

    async def recall(self, user, question, session_id):
        verified_sales_user(user)
        if not isinstance(question, str) or not question.strip() or len(question) > 12_000:
            raise RuntimeError("A bounded sales memory question is required")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
            raise RuntimeError("A sales session ID is required")
        readable = await self.access(user)
        allowed = readable & SHARED_DATASETS
        # A session is visible to both sales members. The requester's private grant
        # is insufficient to publish recalled text into that shared conversation.
        for member in SALES_USERS:
            if member != user:
                allowed &= await self.access(member)
        if not allowed:
            return {"context": [], "withheld": []}
        response = await self.request(
            "POST",
            "/recall",
            payload={
                "as_user": user,
                "question": question,
                "mode": "context",
                "session_id": session_id,
                "top_k": 5,
            },
        )
        context = response.get("context")
        if not isinstance(context, list):
            raise RuntimeError("Brain API returned an invalid recall response")
        # Sales sessions are shared: ignore answer/top-level sources, private client
        # datasets, malformed hits and arbitrary extra keys even for a permitted user.
        result, remaining = [], MAX_RECALL_CHARS
        for item in context:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("dataset"), str)
                or item["dataset"] not in allowed
                or not isinstance(item.get("text"), str)
                or not item["text"].strip()
            ):
                continue
            text = item["text"][:remaining]
            if not text:
                break
            source_ids = item.get("sources", [])
            result.append(
                {
                    "text": text,
                    "dataset": item["dataset"],
                    "sources": [value[:500] for value in source_ids[:10] if isinstance(value, str)]
                    if isinstance(source_ids, list)
                    else [],
                }
            )
            remaining -= len(text)
            if len(result) >= 10 or remaining <= 0:
                break
        # The service may reveal only these ACL-routing labels, never withheld content.
        withheld = response.get("withheld", [])
        if not isinstance(withheld, list):
            withheld = []
        safe_withheld = [
            {key: item[key][:200] for key in ("client", "layer", "owner")}
            for item in withheld[:20]
            if isinstance(item, dict)
            and all(isinstance(item.get(key), str) for key in ("client", "layer", "owner"))
        ]
        return {"context": result, "withheld": safe_withheld}

    async def remember(self, item):
        user = verified_sales_user(item.get("as_user"))
        if item.get("workspace_id") != "toir":
            raise RuntimeError("Sales memory item belongs to an invalid workspace")
        key, report, run_id = item.get("id"), item.get("report"), item.get("run_id")
        if (
            not isinstance(key, str)
            or len(key) != 64
            or not isinstance(run_id, str)
            or not run_id
            or not isinstance(report, dict)
            or report.get("ingestion_id") != key
        ):
            raise RuntimeError("Sales memory item has invalid ingestion identity")
        if time.monotonic() >= self.ingestion_ready_until:
            status = await self.capabilities()
            if not status["ready"]:
                raise RuntimeError("Brain API ingestion is not ready; saved memory remains pending")
        result = await self.request(
            "POST",
            "/remember/research",
            key=key,
            payload={
                "as_user": user,
                "run_id": run_id,
                "report": report,
            },
            success=(200, 201),
        )
        if (
            result.get("dataset") != "toir-pipeline"
            or result.get("ingestion_id") != key
            or type(result.get("documents")) is not int
            or result["documents"] < 1
        ):
            raise RuntimeError("Brain API did not acknowledge durable research ingestion")
        return result


async def enqueue_memory(tx, proposal: Proposal):
    """Persist with proposal changes; immutable payload identity makes replay safe."""
    verified_sales_user(proposal.requested_by)
    lead = proposal.company.model_dump(mode="json")
    lead["company"] = lead.pop("name")
    lead["contacts"] = [
        c.model_dump(mode="json")
        for c in proposal.contacts
        if c.id not in proposal.excluded_contact_ids
    ]
    lead["workflow"] = {
        "proposal_id": proposal.id,
        "version": proposal.version,
        "status": proposal.status,
        "execution": proposal.execution,
        "session_id": proposal.session_id,
        "requested_by": proposal.requested_by,
        "approved_by": proposal.decided_by,
        "approved_at": proposal.decided_at,
        "excluded_operation_ids": proposal.excluded_operation_ids,
        "crm_operations": [operation.model_dump(mode="json") for operation in proposal.operations],
    }
    report = {"leads": [lead], "sources": [s.model_dump(mode="json") for s in proposal.sources]}
    digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if await tx.get("outbox", digest):
        return
    report["ingestion_id"] = digest
    await tx.put(
        "outbox",
        digest,
        {
            "id": digest,
            "workspace_id": "toir",
            "proposal_id": proposal.id,
            "as_user": proposal.requested_by,
            "run_id": proposal.job_id,
            "report": report,
            "status": "pending",
            "attempts": 0,
            "created_at": timestamp(),
            "next_attempt_at": timestamp(),
        },
    )
    current = await tx.get("proposal", proposal.id)
    if current:
        current["memory_status"] = "pending"
        await tx.put("proposal", proposal.id, current)


async def enqueue_research(tx, job: Job, report: ContactReport):
    """Research-only findings use the same durable ingestion path, without a CRM task."""
    verified_sales_user(job.requested_by)
    if not report.company:
        return
    lead = report.company.model_dump(mode="json")
    lead["company"] = lead.pop("name")
    lead["contacts"] = [contact.model_dump(mode="json") for contact in report.contacts]
    lead["workflow"] = {
        "session_id": job.session_id,
        "run_id": job.id,
        "requested_by": job.requested_by,
        "status": "research_only",
        "execution": "not_requested",
    }
    payload = {"leads": [lead], "sources": [s.model_dump(mode="json") for s in report.sources]}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if await tx.get("outbox", digest):
        return
    payload["ingestion_id"] = digest
    await tx.put(
        "outbox",
        digest,
        {
            "id": digest,
            "workspace_id": "toir",
            "as_user": job.requested_by,
            "run_id": job.id,
            "report": payload,
            "status": "pending",
            "attempts": 0,
            "created_at": timestamp(),
            "next_attempt_at": timestamp(),
        },
    )


async def deliver_memory(store, brain, *, ready: bool):
    now, lease = timestamp(), str(uuid4())
    async with store.transaction() as tx:
        items = sorted(await tx.list("outbox"), key=lambda x: x["created_at"])
        if not ready:
            for proposal_id in {
                i["proposal_id"] for i in items if i["status"] != "synced" and i.get("proposal_id")
            }:
                proposal = await tx.get("proposal", proposal_id)
                if proposal and proposal["memory_status"] != "blocked":
                    proposal["memory_status"] = "blocked"
                    await tx.put("proposal", proposal_id, proposal)
            return
        item = next(
            (
                item
                for item in items
                if item["status"] != "synced"
                and item.get("next_attempt_at", "") <= now
                and (item.get("status") != "sending" or (item.get("lease_expires_at") or "") <= now)
            ),
            None,
        )
        if not item:
            return
        item.update(
            status="sending",
            lease_id=lease,
            lease_expires_at=(
                datetime.now(UTC) + timedelta(seconds=OUTBOX_LEASE_SECONDS)
            ).isoformat(),
            attempts=item.get("attempts", 0) + 1,
        )
        await tx.put("outbox", item["id"], item)
    error = None
    try:
        await brain.remember(item)
    except Exception:
        # Provider bodies and SDK exception strings can contain secrets.
        error = "Research memory sync failed or was not acknowledged; saved findings will retry."
    # A cancelled task leaves the lease for recovery; the stable ingestion ID protects
    # against an uncertain remote success followed by a local crash or cancellation.
    async with store.transaction() as tx:
        current = await tx.get("outbox", item["id"])
        if not current or current.get("lease_id") != lease:
            return
        current.update(
            status="pending" if error else "synced",
            error=error,
            lease_id=None,
            lease_expires_at=None,
        )
        current["next_attempt_at"] = (
            datetime.now(UTC)
            + timedelta(
                seconds=min(3600, 15 * 2 ** min(current["attempts"], 8)),
            )
        ).isoformat()
        await tx.put("outbox", current["id"], current)
        proposal = (
            await tx.get("proposal", current["proposal_id"]) if current.get("proposal_id") else None
        )
        if proposal:
            pending = any(
                i.get("proposal_id") == current["proposal_id"] and i["status"] != "synced"
                for i in await tx.list("outbox")
            )
            proposal["memory_status"] = "pending" if pending else "synced"
            await tx.put("proposal", proposal["id"], proposal)
