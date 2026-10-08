"""Real durable state with fake provider boundaries exercises run ownership and recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.coordinator import Coordinator, NotConfigured
from apps.orchestrator.graph.evidence import finalize_report
from apps.orchestrator.graph.workflow import BUDGET_GAP, build_graph, review_sources
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
            assert await service.set_maintenance(True) == {"enabled": True, "active_run_id": None}
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


class FollowUpModels(ReviewOnlyModels):
    async def structured(self, schema, instruction, data):
        self.calls.append(schema)
        assert schema is Review
        return Review(
            accepted_domains=["acme.example"], accepted_competitor_indices=[],
            follow_up_queries=["Find more US companies with warehouse integration needs"],
        )


class PassAgent(FakeAgent):
    def __init__(self, statuses):
        super().__init__()
        self.statuses = statuses

    async def submit(self, payload):
        self.status = self.statuses[len(self.submissions)]
        return await super().submit(payload)


def accepted_candidate():
    # A business need has no date-window dependency on the day the test runs.
    return report(leads=[lead(signals=[signal(kind="business_need", event_date=None, quote=NEED)])])


def completed_pass(candidate, **usage):
    return {
        "status": "completed", "progress": "Report ready for review",
        "report": candidate.model_dump(mode="json"), "usage": usage,
        "sources": [source.model_dump(mode="json") for source in candidate.sources],
    }


async def execute_passes(repository, directory, agent, *, seed=None):
    run = await repository.create(brief(), "budget-case")
    run.plan = ResearchPlan(queries=["US warehouse integration needs"], focus="Warehouses")
    if seed:
        seed(run)
    await repository.save(run)
    models = FollowUpModels()
    graph = build_graph(repository, models, agent, checkpointer=None)
    service = Coordinator(repository, graph, models, agent, directory)
    try:
        await service.execute(run)
        return await repository.get(run.id), models
    finally:
        await service.shutdown()


@pytest.mark.parametrize("error", [
    "Research exhausted its searches budget.",
    "Research exhausted its pages budget.",
    "Research exhausted its model turns budget.",
    "Research exhausted its input context budget. Saved evidence is available.",
])
def test_follow_up_budget_failure_preserves_only_reviewed_report_and_usage(tmp_path, error):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        candidate = accepted_candidate()
        unreviewed = candidate.model_copy(deep=True)
        unreviewed.leads[0].company = "Unreviewed fabricated company"
        unreviewed.leads[0].domain = "fabricated.example"
        unreviewed.sources[0].text = "Replaced source text that cannot support earlier citations."
        unreviewed.summary = "The failed worker found 100 qualified companies."
        unreviewed.gaps = ["Invented claims from a failed follow-up"]
        # Preserve the original 50 -> 60 page production failure: a worker's
        # explicit budget error is authoritative even below newer local limits.
        usage = {"pages": 60, "searches": 12, "model_turns": 20, "input_tokens": 12345}
        agent = PassAgent([
            completed_pass(candidate, pages=50, searches=10, model_turns=18),
            {
                "status": "failed", "progress": "Research stopped", "usage": usage,
                "error": error,
                "report": unreviewed.model_dump(mode="json"),
                "sources": [source.model_dump(mode="json") for source in unreviewed.sources],
            },
        ])
        try:
            result, models = await execute_passes(repository, tmp_path, agent)
            assert result.status == result.stage == "completed"
            assert result.error is None
            assert result.task_id is None
            assert result.pass_number == 1
            assert result.usage == usage
            expected = finalize_report(candidate, result.brief)
            expected.leads[0].decision_maker = None  # No accepted appointment signal.
            expected.gaps.append(BUDGET_GAP)
            assert result.report == expected
            assert models.calls == [Review], "Failed follow-up must not trigger paid review"
            assert len(agent.submissions) == 2
            assert agent.submissions[1]["usage"]["pages"] == 50
            assert agent.submissions[1]["prior_report"]["sources"] == (
                candidate.model_dump(mode="json")["sources"]
            )
            assert agent.cancellations == []
            events = await repository.events(result.id)
            assert [event.stage for event in events][-2:] == ["budget_exhausted", "completed"]
            assert events[-2].message == BUDGET_GAP
            assert await repository.active() is None
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("prior_candidates", [False, True])
@pytest.mark.parametrize("error", [
    "Research exhausted its pages budget.",
    "Research exhausted its input context budget. Saved evidence is available.",
])
def test_first_pass_budget_failure_cannot_promote_unreviewed_candidates(
    tmp_path, prior_candidates, error,
):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        candidate = accepted_candidate()
        agent = PassAgent([{
            **completed_pass(candidate, pages=60), "status": "failed",
            "error": error,
        }])

        def seed(run):
            if prior_candidates:
                run.report = candidate  # An explicit retry may carry older candidate evidence.

        try:
            result, models = await execute_passes(repository, tmp_path, agent, seed=seed)
            assert result.status == "failed"
            assert "before any findings passed evidence review" in result.error
            assert result.pass_number == 0
            assert result.usage == {"pages": 60}
            assert result.report.sources == candidate.sources
            assert BUDGET_GAP not in result.report.gaps
            assert models.calls == []
            assert len(agent.submissions) == 1
            assert agent.cancellations == [agent.submissions[0]["task_id"]]
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("budget,limit", [("searches", 60), ("pages", 200), ("model_turns", 60)])
@pytest.mark.parametrize("extra", [0, 1])
def test_spent_budget_prevents_follow_up_despite_reviewer_queries(tmp_path, budget, limit, extra):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        agent = PassAgent([completed_pass(accepted_candidate(), **{budget: limit + extra})])
        try:
            result, models = await execute_passes(repository, tmp_path, agent)
            assert result.status == "completed"
            assert len(result.report.leads) == 1
            assert BUDGET_GAP in result.report.gaps
            assert len(agent.submissions) == 1
            assert models.calls == [Review]
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("budget,limit", [("searches", 60), ("pages", 200), ("model_turns", 60)])
def test_new_dispatch_is_gated_by_all_worker_usage_limits(tmp_path, reviewed, budget, limit):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        agent = PassAgent([])

        def seed(run):
            run.usage = {budget: limit}
            if reviewed:
                run.pass_number = 1
                run.report = accepted_candidate()

        try:
            result, models = await execute_passes(repository, tmp_path, agent, seed=seed)
            assert result.status == ("completed" if reviewed else "failed")
            assert agent.submissions == agent.lookups == []
            assert agent.cancellations == []
            assert models.calls == []
            assert (BUDGET_GAP in result.report.gaps) is reviewed
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("budget,old_limit,new_limit", [
    ("searches", 30, 60), ("pages", 60, 200), ("model_turns", 30, 60),
])
def test_follow_up_can_use_increased_budget_past_the_old_limit(
    tmp_path, budget, old_limit, new_limit,
):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        candidate = accepted_candidate()
        agent = PassAgent([
            completed_pass(candidate, **{budget: old_limit}),
            completed_pass(candidate, **{budget: new_limit}),
        ])
        try:
            result, models = await execute_passes(repository, tmp_path, agent)
            assert result.status == "completed"
            assert len(agent.submissions) == 2
            assert agent.submissions[1]["usage"] == {budget: old_limit}
            assert result.usage == {budget: new_limit}
            assert result.pass_number == 2
            assert models.calls == [Review, Review]
            assert BUDGET_GAP in result.report.gaps
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status,error,expected", [
    ("failed", "Respan authentication failed; check the runtime secret", "failed"),
    ("failed", "Research reached its deadline. Saved evidence is available for retry.", "failed"),
    ("failed", "Provider budget or credentials failed; retry later.", "failed"),
    ("failed", "Research exhausted its input context budget. Invalid credentials.", "failed"),
    ("cancelled", "Research exhausted its pages budget.", "cancelled"),
])
def test_follow_up_authentication_deadline_and_cancellation_are_not_budget_fallbacks(
    tmp_path, status, error, expected,
):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        agent = PassAgent([
            completed_pass(accepted_candidate(), pages=50),
            {"status": status, "error": error, "progress": "Stopped", "usage": {"pages": 200}},
        ])
        try:
            result, models = await execute_passes(repository, tmp_path, agent)
            assert result.status == expected
            assert len(result.report.leads) == 1
            assert result.usage == {"pages": 200}
            expected_error = error if expected == "failed" else "Research cancelled by the user"
            assert result.error == expected_error
            assert BUDGET_GAP not in result.report.gaps
            assert models.calls == [Review]
            assert len(agent.submissions) == 2
            assert agent.cancellations == [agent.submissions[-1]["task_id"]]
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("error,expected", [
    ("Research exhausted its pages budget.", "completed"),
    ("Scalekit authentication failed; check runtime credentials", "failed"),
])
def test_recovered_follow_up_inspects_existing_terminal_task_even_with_spent_budget(
    tmp_path, error, expected,
):
    async def scenario():
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        run = await repository.create(brief(), "recover-budget")
        run.status = "running"
        run.plan = ResearchPlan(queries=["US integration needs"], focus="Warehouses")
        run.report = accepted_candidate()
        run.pass_number = 1
        run.task_id = f"{run.id}-1"
        run.usage = {"pages": 200}
        await repository.save(run)
        await repository.close()
        repository = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        agent = FakeAgent({
            "status": "failed", "error": error, "progress": "Stopped",
            "usage": {"pages": 200, "model_turns": 25},
            "sources": [run.report.sources[0].model_copy(update={
                "text": "The failed worker supplied replacement text without any prior quotation.",
            }).model_dump(mode="json")],
        })
        models = FollowUpModels()
        graph = build_graph(repository, models, agent, checkpointer=None)
        service = Coordinator(repository, graph, models, agent, tmp_path)
        try:
            await service.recover()
            await asyncio.wait_for(asyncio.gather(*list(service.tasks.values())), 2)
            result = await repository.get(run.id)
            assert result.status == expected
            assert result.report.sources == run.report.sources
            assert result.report.leads[0].signals == run.report.leads[0].signals
            assert result.usage == {"pages": 200, "model_turns": 25}
            assert set(agent.lookups) == {run.task_id}
            assert agent.submissions == []
            assert models.calls == []
            assert (BUDGET_GAP in result.report.gaps) is (expected == "completed")
        finally:
            await service.shutdown()
            await repository.close()

    asyncio.run(scenario())
