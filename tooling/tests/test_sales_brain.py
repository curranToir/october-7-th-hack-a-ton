"""Brain protocol, ACL filtering and crash-safe outbox tests without network access."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.orchestrator.models.research import Citation, Source
from apps.orchestrator.sales import brain as module
from apps.orchestrator.sales.brain import (
    BrainClient,
    deliver_memory,
    enqueue_memory,
    enqueue_research,
)
from apps.orchestrator.sales.models import Company, ContactReport, Job, Proposal
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.sqlite import SQLiteRunRepository


def proposal():
    source = Source(
        id="src_1234567890abcdef",
        url="https://example.com",
        title="Example",
        text="Example builds software in the US.",
        retrieved_at=datetime.now(UTC),
    )
    return Proposal(
        session_id="session",
        job_id="job",
        requested_by="curran@toirinc.com",
        company=Company(
            name="Example",
            domain="example.com",
            citations=[Citation(source_id=source.id, quote=source.text)],
        ),
        sources=[source],
    )


def client(monkeypatch, handler):
    monkeypatch.setenv("BRAIN_API_URL", "http://brain.test")
    monkeypatch.setenv("BRAIN_API_TOKEN", "private-token")
    return BrainClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ready_reply(request):
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path.startswith("/access/"):
        return httpx.Response(
            200, json={"readable": ["toir-pipeline", "toir-firm", "private-client"]}
        )
    if request.url.path == "/capabilities":
        return httpx.Response(
            200, json={"research_idempotency": True, "research_writers": list(module.SALES_USERS)}
        )
    raise AssertionError(f"Unexpected request: {request.url.path}")


def test_recall_preserves_verified_actor_and_drops_private_and_malformed_content(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer private-token"
        if request.url.path == "/recall":
            body = json.loads(request.content)
            assert body == {
                "as_user": "curran@toirinc.com",
                "question": "Who can buy?",
                "mode": "context",
                "session_id": "session",
                "top_k": 5,
            }
            return httpx.Response(
                200,
                json={
                    "answer": "private answer",
                    "sources": ["private top-level source"],
                    "context": [
                        {
                            "dataset": "toir-pipeline",
                            "text": "shared prospect",
                            "sources": ["link"],
                            "secret": "omit",
                        },
                        {"dataset": "toir-firm", "text": "shared firm", "sources": []},
                        {"dataset": "private-client", "text": "private customer text"},
                        {"dataset": ["toir-pipeline"], "text": "bad"},
                        None,
                    ],
                    "withheld": [
                        {
                            "client": "acme",
                            "layer": "commercial",
                            "owner": "jared",
                            "text": "private withheld",
                        }
                    ],
                },
            )
        return ready_reply(request)

    async def scenario():
        api = client(monkeypatch, handler)
        try:
            result = await api.recall("curran@toirinc.com", "Who can buy?", "session")
            assert len(result["context"]) == 2
            assert "private" not in json.dumps(result)
            assert result["withheld"] == [
                {"client": "acme", "layer": "commercial", "owner": "jared"}
            ]
            before = len(calls)
            with pytest.raises(RuntimeError, match="not authorized"):
                await api.recall("unknown@example.com", "Who can buy?", "session")
            assert len(calls) == before
        finally:
            await api.close()

    asyncio.run(scenario())


def test_recall_checks_grants_and_never_returns_denied_or_unlabelled_hits(monkeypatch):
    def handler(request):
        if request.url.path.startswith("/access/"):
            return httpx.Response(200, json={"readable": ["toir-firm"]})
        return httpx.Response(
            200,
            json={
                "context": [
                    {"dataset": "toir-pipeline", "text": "unauthorized pipeline"},
                    {"text": "unlabelled"},
                    {"dataset": "toir-firm", "text": "allowed"},
                ],
                "withheld": None,
            },
        )

    async def scenario():
        api = client(monkeypatch, handler)
        try:
            result = await api.recall("jared@neptuneops.com", "Question", "session")
            assert result == {
                "context": [{"dataset": "toir-firm", "text": "allowed", "sources": []}],
                "withheld": [],
            }
        finally:
            await api.close()
        denied = client(
            monkeypatch, lambda _: httpx.Response(403, json={"error": "private-response"})
        )
        try:
            with pytest.raises(RuntimeError, match="HTTP 403") as error:
                await denied.recall("curran@toirinc.com", "Question", "session")
            assert "private" not in str(error.value)
        finally:
            await denied.close()

    asyncio.run(scenario())


def test_legacy_service_without_handshake_cannot_ingest_or_claim_readiness(monkeypatch):
    writes = []

    def handler(request):
        if request.method == "POST":
            writes.append(request)
        if request.url.path == "/capabilities":
            return httpx.Response(404, json={"detail": "Not Found"})
        return ready_reply(request)

    async def scenario():
        api = client(monkeypatch, handler)
        try:
            assert not (await api.capabilities())["ready"]
            item = {
                "id": "a" * 64,
                "workspace_id": "toir",
                "as_user": "curran@toirinc.com",
                "run_id": "job",
                "report": {"ingestion_id": "a" * 64},
            }
            with pytest.raises(RuntimeError, match="not ready"):
                await api.remember(item)
            assert not writes
        finally:
            await api.close()

    asyncio.run(scenario())


def test_research_body_matches_existing_boundary_and_requires_exact_durable_ack(monkeypatch):
    key = "a" * 64
    item = {
        "id": key,
        "workspace_id": "toir",
        "as_user": "jared@neptuneops.com",
        "run_id": "job",
        "report": {"ingestion_id": key, "leads": []},
    }
    acknowledged = False

    def handler(request):
        if request.url.path == "/remember/research":
            assert request.headers["Idempotency-Key"] == key
            assert json.loads(request.content) == {
                "as_user": item["as_user"],
                "run_id": "job",
                "report": item["report"],
            }
            result = {"dataset": "toir-pipeline", "documents": 1}
            if acknowledged:
                result["ingestion_id"] = key
            return httpx.Response(200, json=result)
        return ready_reply(request)

    async def scenario():
        nonlocal acknowledged
        api = client(monkeypatch, handler)
        try:
            with pytest.raises(RuntimeError, match="acknowledge"):
                await api.remember(item)
            acknowledged = True
            assert (await api.remember(item))["ingestion_id"] == key
        finally:
            await api.close()

    asyncio.run(scenario())


def test_bad_envelopes_and_stalled_requests_fail_closed(monkeypatch):
    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"status": "ok"})

    async def scenario():
        api = client(monkeypatch, lambda _: httpx.Response(200, json=["unexpected"]))
        try:
            assert not (await api.capabilities())["ready"]
        finally:
            await api.close()
        monkeypatch.setattr(module, "REQUEST_TIMEOUT_SECONDS", 0.005)
        api = client(monkeypatch, slow)
        try:
            with pytest.raises(RuntimeError, match="temporarily unavailable"):
                await api.recall("curran@toirinc.com", "Question", "session")
        finally:
            await api.close()

    asyncio.run(scenario())


def test_outbox_is_atomic_durable_replayable_and_preserves_research_only_findings(tmp_path):
    async def scenario():
        path = tmp_path / "runs.sqlite"
        repo = await SQLiteRunRepository.open(path)
        store, record = SalesStore(repo), proposal()
        await store.setup()
        async with store.transaction() as tx:
            await tx.put("proposal", record.id, record.model_dump(mode="json"))
            await enqueue_memory(tx, record)
            await enqueue_memory(tx, record)
            assert len(await tx.list("outbox")) == 1
            job = Job(
                session_id="research-session",
                requested_by="curran@toirinc.com",
                kind="enrich",
                query="Example",
            )
            report = ContactReport(company=record.company, sources=record.sources)
            await enqueue_research(tx, job, report)
            await enqueue_research(tx, job, report)
            assert len(await tx.list("outbox")) == 2
        await repo.close()
        repo = await SQLiteRunRepository.open(path)
        store = SalesStore(repo)

        class Brain:
            fail = True
            calls = []

            async def remember(self, item):
                self.calls.append(item["id"])
                if self.fail:
                    raise RuntimeError("private-provider-response")

        api = Brain()
        try:
            await deliver_memory(store, api, ready=False)
            assert not api.calls
            async with store.transaction() as tx:
                assert (await tx.get("proposal", record.id))["memory_status"] == "blocked"
            await deliver_memory(store, api, ready=True)
            first_id = api.calls[0]
            async with store.transaction() as tx:
                item = await tx.get("outbox", first_id)
                assert item["status"] == "pending" and item["attempts"] == 1
                assert "private" not in item["error"]
                item["next_attempt_at"] = ""
                await tx.put("outbox", first_id, item)
            api.fail = False
            await deliver_memory(store, api, ready=True)
            assert api.calls[:2] == [first_id, first_id]
            await deliver_memory(store, api, ready=True)
            async with store.transaction() as tx:
                items = await tx.list("outbox")
                assert all(i["status"] == "synced" for i in items)
                assert (await tx.get("proposal", record.id))["memory_status"] == "synced"
                research = next(i for i in items if not i.get("proposal_id"))
                workflow = research["report"]["leads"][0]["workflow"]
                assert workflow["status"] == "research_only"
                assert workflow["session_id"] == "research-session"
                assert research["report"]["sources"][0]["text"] == record.sources[0].text
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_outbox_lease_prevents_concurrent_delivery_and_recovers_uncertain_cancellation(tmp_path):
    async def scenario():
        repo = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        store, record = SalesStore(repo), proposal()
        await store.setup()
        async with store.transaction() as tx:
            await tx.put("proposal", record.id, record.model_dump(mode="json"))
            await enqueue_memory(tx, record)
        entered, release, calls = asyncio.Event(), asyncio.Event(), []

        class Brain:
            async def remember(self, item):
                calls.append(item["id"])
                entered.set()
                await release.wait()

        api = Brain()
        try:
            sending = asyncio.create_task(deliver_memory(store, api, ready=True))
            await entered.wait()
            await deliver_memory(store, api, ready=True)
            assert len(calls) == 1
            sending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sending
            async with store.transaction() as tx:
                item = (await tx.list("outbox"))[0]
                assert item["status"] == "sending"
                item["lease_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                await tx.put("outbox", item["id"], item)
            release.set()
            await deliver_memory(store, api, ready=True)
            assert calls[0] == calls[1]
            async with store.transaction() as tx:
                assert (await tx.list("outbox"))[0]["status"] == "synced"
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_shared_session_uses_intersection_of_both_sales_members_grants(monkeypatch):
    def handler(request):
        if request.url.path.startswith("/access/"):
            readable = ["toir-firm"]
            if "jared" in request.url.path:
                readable.append("toir-pipeline")
            return httpx.Response(200, json={"readable": readable})
        assert json.loads(request.content)["as_user"] == "jared@neptuneops.com"
        return httpx.Response(
            200,
            json={
                "context": [
                    {"dataset": "toir-pipeline", "text": "Jared-only pipeline"},
                    {"dataset": "toir-firm", "text": "Shared firm"},
                ],
                "withheld": [],
            },
        )

    async def scenario():
        api = client(monkeypatch, handler)
        try:
            result = await api.recall("jared@neptuneops.com", "Question", "shared-session")
            assert result["context"] == [
                {"dataset": "toir-firm", "text": "Shared firm", "sources": []}
            ]
        finally:
            await api.close()

    asyncio.run(scenario())
