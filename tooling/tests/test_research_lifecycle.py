"""Real durable state with fake provider boundaries exercises run ownership and recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.coordinator import Coordinator, NotConfigured
from apps.orchestrator.graph.workflow import build_graph, review_sources
from apps.orchestrator.models.research import (
    Brief,
    Citation,
    CompetitorFact,
    ResearchPlan,
    ResearchReport,
    Review,
    Source,
)
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_research_evidence import IDENTITY, NEED, lead, report, signal


class FakeAgent:
    def __init__(self, status=None):
        self.status = status
        self.lookups = []
        self.submissions = []
        self.cancellations = []
        self.lookup_observed = asyncio.Event()

    async def capabilities(self):
        return {"version": 1, "agent": "research", "configured": True, "missing_credentials": []}

    async def get(self, task_id):
        self.lookups.append(task_id)
        self.lookup_observed.set()
        return self.status

    async def submit(self, payload):
        self.submissions.append(payload)
        return self.status

    async def cancel(self, task_id):
        self.cancellations.append(task_id)
        return {"status": "cancelled"}


class BlockingGraph:
    def __init__(self):
        self.entered = asyncio.Event()
        self.calls = []

    async def ainvoke(self, state, config):
        self.calls.append((state, config))
        self.entered.set()
        await asyncio.Event().wait()


class ReviewOnlyModels:
    client = object()

    def __init__(self):
        self.calls = []

    async def structured(self, schema, instruction, data):
        self.calls.append(schema)
        assert schema is Review, "Recovery must not regenerate an existing paid research plan"
        return Review(accepted_domains=[], accepted_competitor_indices=[], follow_up_queries=[])


def brief():
    return Brief(request="Find US companies investing in warehouse AI integration")


def coordinator(repository, directory, agent=None, graph=None, configured=True):
    return Coordinator(
        repository, graph or BlockingGraph(),
        SimpleNamespace(client=object() if configured else None),
        agent or FakeAgent(), directory,
    )


def test_create_replays_without_starting_duplicate_work_and_rejects_competitor(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            first, replay = await asyncio.gather(
                service.create(brief(), "same-request"), service.create(brief(), "same-request"),
            )
            await asyncio.wait_for(service.graph.entered.wait(), 1)
            assert first.id == replay.id
            assert len(service.graph.calls) == 1
            assert len(service.tasks) == 1
            with pytest.raises(Conflict):
                await service.create(brief(), "another-request")
            assert len(await repository.list()) == 1
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_instant_cancellation_before_task_starts_releases_active_slot(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            run = await service.create(brief(), "immediately-cancelled")
            cancelled = await service.cancel(run)
            assert cancelled.status == "cancelled"
            assert await repository.active() is None
            assert service.graph.calls == []
            assert service.tasks == {}
            replacement = await service.create(brief(), "replacement")
            assert replacement.id != run.id
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_active_cancellation_stops_remote_task_and_persists_terminal_event(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            run = await repository.create(brief(), "first")
            run.task_id = f"{run.id}-0"
            await repository.save(run)
            service.start(run)
            await asyncio.wait_for(service.graph.entered.wait(), 1)
            result = await service.cancel(await repository.get(run.id))
            assert result.status == "cancelled"
            assert service.agent.cancellations == [run.task_id]
            assert (await repository.events(run.id))[-1].stage == "cancelled"
            assert await repository.active() is None
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_shutdown_leaves_existing_agent_session_recoverable(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            run = await repository.create(brief(), "first")
            run.task_id = f"{run.id}-0"
            run.plan = ResearchPlan(queries=["US logistics AI initiatives"], focus="Logistics")
            await repository.save(run)
            service.start(run)
            await asyncio.wait_for(service.graph.entered.wait(), 1)
            await service.shutdown()
            persisted = await repository.get(run.id)
            assert persisted.status == "running"
            assert persisted.task_id == run.task_id
            assert persisted.plan == run.plan
            assert service.agent.cancellations == []
            assert service.tasks == {}
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("task_id", [None, "lost-worker-task"])
def test_restart_without_live_agent_task_marks_interrupted_without_paid_work(tmp_path, task_id):
    async def scenario():
        path = tmp_path / "runs.sqlite"
        repository = await SQLiteRunRepository.open(path)
        run = await repository.create(brief(), "first")
        run.status = "running"
        run.task_id = task_id
        run.report.gaps = ["Evidence gathered before restart remains available."]
        await repository.save(run)
        await repository.close()
        repository = await SQLiteRunRepository.open(path)
        service = coordinator(repository, tmp_path)
        try:
            await service.recover()
            result = await repository.get(run.id)
            assert result.status == "interrupted"
            assert result.report.gaps == run.report.gaps
            assert "Retry using saved evidence" in result.error
            assert service.tasks == {}
            assert service.graph.calls == []
            assert service.agent.submissions == []
            assert (await repository.events(run.id))[-1].stage == "interrupted"
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_recovery_reconnects_to_completed_worker_task_without_resubmission(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        run = await repository.create(brief(), "first")
        run.status = "running"
        run.task_id = f"{run.id}-0"
        run.plan = ResearchPlan(queries=["US firms hiring CTOs"], focus="Technology leadership")
        await repository.save(run)
        agent = FakeAgent(status={
            "status": "completed", "report": ResearchReport().model_dump(mode="json"),
            "progress": "Research completed", "usage": {"tool_calls": 3},
        })
        models = ReviewOnlyModels()
        graph = build_graph(repository, models, agent, checkpointer=None)
        service = Coordinator(repository, graph, models, agent, tmp_path)
        try:
            await service.recover()
            pending = list(service.tasks.values())
            assert len(pending) == 1
            await asyncio.wait_for(asyncio.gather(*pending), 2)
            result = await repository.get(run.id)
            assert result.status == "completed"
            assert result.task_id is None
            assert result.usage == {"tool_calls": 3}
            assert agent.submissions == []
            assert agent.cancellations == []
            assert set(agent.lookups) == {run.task_id}
            assert models.calls == [Review]
            assert any(event.stage == "recovering" for event in await repository.events(run.id))
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_expired_deadline_stops_remote_task_without_starting_another_graph(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            run = await repository.create(brief(), "expired")
            run.task_id = f"{run.id}-0"
            run.deadline_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            await repository.save(run)
            service.start(run)
            await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 1)
            result = await repository.get(run.id)
            assert result.status == "failed"
            assert "time limit" in result.error
            assert service.graph.calls == []
            assert service.agent.cancellations == [run.task_id]
            assert await repository.active() is None
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_unconfigured_provider_rejects_before_creating_durable_work(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path, configured=False)
        try:
            with pytest.raises(NotConfigured):
                await service.create(brief(), "first")
            assert await repository.list() == []
            assert service.tasks == {}
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_completed_idempotent_replay_survives_provider_outage_and_maintenance(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path, configured=False)
        try:
            run = await repository.create(brief(), "completed-request")
            run.status = "completed"
            run.report.summary = "Previously completed report."
            await repository.save(run)
            await service.set_maintenance(True)
            replay = await service.create(brief(), "completed-request")
            assert replay.id == run.id
            assert replay.status == "completed"
            assert replay.report.summary == run.report.summary
            assert service.graph.calls == []
            assert service.tasks == {}
            assert len(await repository.list()) == 1
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_maintenance_blocks_new_work_and_survives_coordinator_restart(tmp_path):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path)
        try:
            assert await service.set_maintenance(True) == {
                "enabled": True, "active_run_id": None, "active_meeting_operations": [],
            }
            with pytest.raises(Conflict, match="maintenance"):
                await service.create(brief(), "first")
            restarted = coordinator(repository, tmp_path)
            assert (await restarted.maintenance_status())["enabled"] is True
            await restarted.set_maintenance(False)
            run = await restarted.create(brief(), "first")
            assert (await restarted.maintenance_status())["active_run_id"] == run.id
            await restarted.shutdown()
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_unreachable_worker_has_clear_capability_state(tmp_path):
    class UnreachableAgent(FakeAgent):
        async def capabilities(self):
            raise AgentFailure("Research agent is unreachable")

    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        service = coordinator(repository, tmp_path, agent=UnreachableAgent())
        try:
            result = await service.capabilities()
            assert result["configured"] is False
            assert result["missing"] == ["Research agent unavailable"]
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("existing_task", [False, True])
def test_worker_session_disappearing_during_execution_marks_interrupted(tmp_path, existing_task):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        run = await repository.create(brief(), "lost-during-execution")
        run.plan = ResearchPlan(queries=["US logistics AI initiatives"], focus="Logistics")
        run.report = report(leads=[])
        if existing_task:
            run.task_id = f"{run.id}-0"
        await repository.save(run)
        agent = FakeAgent(status=None)
        models = ReviewOnlyModels()
        graph = build_graph(repository, models, agent, checkpointer=None)
        service = Coordinator(repository, graph, models, agent, tmp_path)
        try:
            service.start(run)
            await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 2)
            result = await repository.get(run.id)
            assert result.status == "interrupted"
            assert result.stage == "interrupted"
            assert "session was lost" in result.error
            assert result.report.sources == run.report.sources
            assert (await repository.events(run.id))[-1].stage == "interrupted"
            assert len(agent.submissions) == (0 if existing_task else 1)
            assert models.calls == []
            assert await repository.active() is None
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_semantic_review_receives_context_that_refutes_a_literal_quote(tmp_path):
    candidate_report = report(
        leads=[lead(signals=[signal(kind="business_need", event_date=None, quote=NEED)])],
        text=(f"{IDENTITY} A trade forum repeated the rumor: {NEED} "
              "Acme says this claim is false and that no such project exists."),
    )

    class ContextAwareModels(ReviewOnlyModels):
        async def structured(self, schema, instruction, data):
            self.calls.append(schema)
            assert schema is Review
            assert len(data["leads"]) == 1, "Substring admission alone cannot detect this claim"
            source = data["sources"][0]
            assert source["id"] == candidate_report.sources[0].id
            assert source["url"] == str(candidate_report.sources[0].url)
            excerpts = " ".join(source["excerpts"])
            assert "rumor" in excerpts
            assert NEED.casefold() in excerpts
            assert "claim is false" in excerpts
            assert "negation, rumors" in instruction
            return Review(
                accepted_domains=[], accepted_competitor_indices=[],
                gaps=["The cited source refutes the alleged need."],
            )

    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        run = await repository.create(brief(), "misleading-context")
        run.plan = ResearchPlan(queries=["US logistics AI initiatives"], focus="Logistics")
        await repository.save(run)
        agent = FakeAgent(status={
            "status": "completed", "report": candidate_report.model_dump(mode="json"),
            "progress": "Research completed", "usage": {},
        })
        models = ContextAwareModels()
        graph = build_graph(repository, models, agent, checkpointer=None)
        service = Coordinator(repository, graph, models, agent, tmp_path)
        try:
            service.start(run)
            await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 2)
            result = await repository.get(run.id)
            assert result.status == "completed"
            assert result.report.leads == []
            assert "Only 0 of 10 requested companies qualified." in result.report.gaps
            assert "The cited source refutes the alleged need." not in result.report.gaps
            assert models.calls == [Review]
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())


def test_review_context_is_bounded_without_silently_truncating_a_quotation():
    sources, citations = [], []
    for index in range(100):
        source_id = f"src_{index:016x}"
        quote = f"Provider {index} offers implementation of AI integrations to its customers."
        citations.append(Citation(source_id=source_id, quote=quote))
        sources.append(Source(
            id=source_id, url=f"https://provider{index}.example/offer",
            title=f"Provider {index}", retrieved_at=datetime(2026, 10, 7, tzinfo=UTC),
            text="Context before. " * 50 + quote + " Context after." * 50,
        ))
    facts = [CompetitorFact(
        company=f"Provider {index}", kind="marketing", claim="Offers AI implementation services.",
        citations=citations[index * 5:(index + 1) * 5],
        positioning_hypothesis="Consider narrower implementation scope.",
    ) for index in range(20)]
    context = review_sources(ResearchReport(sources=sources, competitors=facts))
    assert 0 < len(context) < len(sources), "The input is large enough to exercise the cap"
    assert sum(len(excerpt) for item in context for excerpt in item["excerpts"]) <= 80000
    by_id = {citation.source_id: citation.quote.casefold() for citation in citations}
    assert all(by_id[item["id"]] in excerpt for item in context for excerpt in item["excerpts"])
