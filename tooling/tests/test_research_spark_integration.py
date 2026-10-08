"""Authenticated chat → LangGraph → durable run/outbox → ACL-aware Brain boundary."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from apps.orchestrator.coordinator import Coordinator
from apps.orchestrator.graph.workflow import build_graph
from apps.orchestrator.models.research import Brief, ResearchPlan, Review
from apps.orchestrator.sales.brain import BrainClient
from apps.orchestrator.sales.chat import ChatIntent
from apps.orchestrator.sales.models import Job
from apps.orchestrator.sales.service import SalesService
from apps.orchestrator.sales.store import SalesStore
from tooling.tests import test_sales_storage as storage_contracts
from tooling.tests.test_research_evidence import NEED, lead, report, signal
from tooling.tests.test_sales_scheduler import Harness as SchedulerHarness
from tooling.tests.test_sales_scheduler import job
from tooling.tests.test_sales_workflow import CURRAN, JARED, Executor, Planner

open_sales_repository = storage_contracts.open_sales_repository


class ResearchModels:
    client = object()

    def __init__(self):
        self.calls = []

    async def structured(self, schema, prompt, data):
        self.calls.append((schema, data))
        assert "private-acme-content" not in json.dumps(data, default=str)
        if schema is ChatIntent:
            assert "requested company count" in prompt
            assert "no-contact research" in prompt
            return ChatIntent(
                intent="discovery", query=data["request"], propose_crm=False,
                target_count=2, enrich_contacts=False,
            )
        if schema is ResearchPlan:
            assert "not current public evidence" in prompt
            return ResearchPlan(queries=["Acme US warehouse integration 100 employees"], focus="AI")
        assert schema is Review
        return Review(accepted_domains=["acme.example"], accepted_competitor_indices=[])


class ResearchWorker:
    def __init__(self):
        self.submitted = []
        self.result = report(leads=[lead(signals=[
            signal(kind="business_need", event_date=None, quote=NEED),
        ])])

    async def capabilities(self):
        return {"configured": True, "missing_credentials": []}

    async def submit(self, task):
        self.submitted.append(task)

    async def get(self, task_id):
        return {
            "status": "completed", "progress": "Public evidence retrieved", "usage": {},
            "report": self.result.model_dump(mode="json"),
        }

    async def cancel(self, task_id):
        raise AssertionError("A successful research run should not be cancelled")


class DisabledCRM:
    async def capabilities(self):
        return {"ready": False, "reasons": ["CRM write attestation is disabled"]}


class UnavailableContacts:
    async def capabilities(self):
        return {"ready": False, "reasons": ["Contact worker unavailable"]}

    async def submit(self, task):
        raise AssertionError("Disabled contact worker must not be dispatched")


@pytest.mark.parametrize("brain_available", [True, False])
def test_real_graph_persists_and_remembers_discovery_without_crm_writes(
    tmp_path, monkeypatch, open_sales_repository, brain_available,
):
    monkeypatch.setenv("BRAIN_API_URL", "http://brain.test")
    monkeypatch.setenv("BRAIN_API_TOKEN", "test-bearer")
    remembered, recalled = [], []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-bearer"
        path = request.url.path
        if not brain_available:
            return httpx.Response(503, json={"detail": "Unavailable"})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/capabilities":
            return httpx.Response(200, json={
                "research_idempotency": True,
                "research_writers": [CURRAN["email"], JARED["email"]],
            })
        if path.startswith("/access/"):
            datasets = ["toir-firm", "toir-pipeline"]
            if CURRAN["email"] in path:
                datasets.append("acme-commercial")
            return httpx.Response(200, json={"readable": datasets})
        payload = json.loads(request.content)
        if path == "/recall":
            recalled.append(payload)
            return httpx.Response(200, json={"context": [
                {"dataset": "toir-firm", "text": "Prior warehouse AI playbook", "sources": []},
                {"dataset": "acme-commercial", "text": "private-acme-content", "sources": []},
            ], "withheld": []})
        assert path == "/remember/research"
        assert payload["as_user"] == CURRAN["email"]
        assert request.headers["Idempotency-Key"] == payload["report"]["ingestion_id"]
        remembered.append(payload)
        return httpx.Response(201, json={
            "dataset": "toir-pipeline", "ingestion_id": payload["report"]["ingestion_id"],
            "documents": len(payload["report"]["leads"]),
        })

    async def scenario():
        path = tmp_path / "runs.sqlite"
        repository = await open_sales_repository(path)
        models, worker = ResearchModels(), ResearchWorker()
        store = SalesStore(repository)
        graph = build_graph(repository, models, worker, checkpointer=None)
        research = Coordinator(repository, graph, models, worker, tmp_path)
        brain = BrainClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        executor, planner = Executor(store), Planner()
        service = SalesService(
            store, research, models, SimpleNamespace(configured=True), UnavailableContacts(),
            DisabledCRM(), planner, executor, brain,
        )
        run_id = None
        try:
            await service.setup()
            session = await service.chat.create_session(CURRAN)
            message = await service.chat.send(
                CURRAN, session.id,
                "Find 2 US warehouse companies that need AI integration; company-only, no contacts",
                "one",
            )
            await service.chat.process_next()
            capabilities = await service.capabilities()
            assert capabilities["crm"] is False
            assert capabilities["ready"] is False
            assert capabilities["research_ready"] == store.is_postgres
            assert (await service.workspace(CURRAN))["automation"]["enabled"] is False

            await service.scheduler.tick()
            assert len(research.tasks) == 1
            await asyncio.wait_for(asyncio.gather(*list(research.tasks.values())), 2)
            await service.scheduler.tick()
            workspace = await service.workspace(JARED)
            discovery = next(item for item in workspace["jobs"] if item["id"] == message.job_id)
            assert discovery["status"] == "completed"
            assert discovery["memory_status"] == "pending"
            assert discovery["target_count"] == 2
            assert discovery["enrich_contacts"] is False
            assert len(workspace["jobs"]) == 1, "Company-only discovery must not fan out"
            run_id = discovery["research_run_id"]
            run = await repository.get(run_id)
            assert run.status == "completed" and len(run.report.leads) == 1
            assert run.brief.target_count == 2
            assert len(worker.submitted) == 1
            assert worker.submitted[0]["brief"]["target_count"] == 2
            plan_data = next(data for schema, data in models.calls if schema is ResearchPlan)
            if brain_available:
                assert plan_data["shared_memory"]["context"][0]["text"] == (
                    "Prior warehouse AI playbook"
                )
                assert len(recalled) == 2  # Intent routing and the actual research planner.
                assert {call["as_user"] for call in recalled} == {CURRAN["email"]}
            else:
                assert plan_data["shared_memory"] == {"context": [], "unavailable": True}
            assert all(source.url.host == "acme.example" for source in run.report.sources)
            findings = next(m for m in workspace["messages"] if m["id"] == (
                f"discovery-report-{message.job_id}"
            ))
            assert "Acme (acme.example)" in findings["content"]
            assert "https://acme.example/news" in findings["content"]
            assert "hypothesis" in findings["content"]
            assert workspace["tasks"] == []
            assert planner.calls == executor.executed == []

            await service._memory_tick()
            async with store.transaction() as tx:
                entries = await tx.list("outbox")
                assert len(entries) == 1
                assert entries[0]["run_id"] == run_id
                expected_status = "synced" if brain_available else "blocked"
                assert (await tx.get("job", message.job_id))["memory_status"] == expected_status
                assert entries[0]["status"] == ("synced" if brain_available else "pending")
                assert await tx.list("crm_operation") == []
                assert await tx.list("decision") == []
            if brain_available:
                assert len(remembered) == 1
                saved_report = remembered[0]["report"]
                assert saved_report["leads"][0]["signals"] == (
                    run.report.leads[0].model_dump(mode="json")["signals"]
                )
                assert saved_report["sources"] == [s.model_dump(mode="json")
                                                   for s in run.report.sources]
                assert remembered[0]["run_id"] == run_id
            await service._memory_tick()
            await service.scheduler.tick()
            assert len(remembered) == (1 if brain_available else 0)
            assert len(worker.submitted) == 1
        finally:
            await service.shutdown()
            await research.shutdown()
            await brain.close()
            await repository.close()

        reopened = await open_sales_repository(path)
        try:
            assert (await reopened.get(run_id)).status == "completed"
            async with SalesStore(reopened).transaction() as tx:
                assert len(await tx.list("outbox")) == 1
                assert any(m["id"] == f"discovery-report-{message.job_id}"
                           for m in await tx.list("message"))
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_cancellation_while_recalling_memory_prevents_research_dispatch(tmp_path):
    async def scenario():
        harness = await SchedulerHarness.open(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def context(job):
            entered.set()
            await release.wait()
            return {"context": []}

        try:
            harness.scheduler.planning_context = context
            item = job(kind="discovery")
            await harness.put("job", item)
            tick = asyncio.create_task(harness.scheduler.tick())
            await asyncio.wait_for(entered.wait(), 1)
            await harness.scheduler.cancel(item.id)
            release.set()
            await asyncio.wait_for(tick, 1)
            assert (await harness.get(item)).status == "cancelled"
            assert harness.research.created == []
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [0, 11])
def test_requested_count_is_bounded_in_routing_and_durable_jobs(count):
    with pytest.raises(ValidationError):
        ChatIntent(intent="discovery", target_count=count)
    with pytest.raises(ValidationError):
        job(kind="discovery", target_count=count)


def test_retry_preserves_explicit_company_count_and_no_contact_boundary(tmp_path):
    async def scenario():
        harness = await SchedulerHarness.open(tmp_path)
        try:
            original = job(
                kind="discovery", status="interrupted", target_count=2, enrich_contacts=False,
            )
            await harness.put("job", original)
            retry = await harness.scheduler.retry(original.id)
            assert retry.id != original.id
            assert retry.target_count == 2
            assert retry.enrich_contacts is False
            await harness.scheduler.tick()
            persisted = await harness.get(retry)
            assert persisted.research_brief.target_count == 2
            assert persisted.enrich_contacts is False
            run = await harness.repository.get(persisted.research_run_id)
            assert run.brief.target_count == 2
            run.status, run.stage = "completed", "completed"
            await harness.repository.save(run)
            await harness.scheduler.tick()
            assert (await harness.get(retry)).status == "completed"
            assert len(await harness.jobs()) == 2  # Original + retry, no contact jobs.
            assert harness.worker.submitted == []
            assert Job.model_validate(persisted.model_dump()).enrich_contacts is False
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_discovery_completion_does_not_overwrite_concurrent_memory_acknowledgment(tmp_path):
    async def scenario():
        harness = await SchedulerHarness.open(tmp_path)
        try:
            item = job(
                kind="discovery", status="running", enrich_contacts=False, memory_status="pending",
            )
            await harness.put("job", item)
            run = await harness.repository.create(Brief(request=item.query), "discovery")
            run.status = "completed"
            await harness.repository.save(run)
            # Brain's acknowledgment arrives after finish_discovery enqueues its outbox,
            # before the scheduler changes the still-running job to completed.
            async with harness.store.transaction() as tx:
                current = await tx.get("job", item.id)
                current["memory_status"] = "synced"
                await tx.put("job", item.id, current)
            await harness.scheduler._enqueue_leads(item, run)
            saved = await harness.get(item)
            assert saved.status == "completed"
            assert saved.memory_status == "synced"
            stale = saved.model_copy(update={"memory_status": "pending"})
            await harness.scheduler._save(stale)
            assert (await harness.get(item)).memory_status == "synced"
        finally:
            await harness.close()

    asyncio.run(scenario())
