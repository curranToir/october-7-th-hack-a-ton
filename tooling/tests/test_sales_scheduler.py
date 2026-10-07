"""Persisted workflow lifecycle tests with provider boundaries replaced by fakes."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.models.research import Brief, Citation, ResearchReport, Source
from apps.orchestrator.sales.contact_client import ContactAgentClient, ContactStatus
from apps.orchestrator.sales.models import Automation, Company, ContactReport, Job, Proposal
from apps.orchestrator.sales.scheduler import SalesScheduler
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_research_evidence import lead

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
SOURCE = Source(
    id="src_1111111111111111",
    url="https://acme.example/about",
    title="About Acme",
    retrieved_at=NOW,
    text="Acme is a US company with 100 employees and Janet Doe is CEO.",
)
COMPANY = Company(
    name="Acme",
    domain="acme.example",
    country="US",
    employee_count=100,
    fit_score=80,
    citations=[Citation(source_id=SOURCE.id, quote="Acme is a US company with 100 employees")],
)


class Worker:
    def __init__(self):
        self.submitted = []
        self.cancelled = []
        self.statuses = {}
        self.unreachable = False

    async def submit(self, task):
        self.submitted.append(task)
        self.statuses[task.task_id] = ContactStatus(
            task_id=task.task_id,
            run_id=task.run_id,
            status="running",
        )
        return self.statuses[task.task_id]

    async def get(self, task_id):
        if self.unreachable:
            raise AgentFailure("Temporary failure")
        return self.statuses.get(task_id)

    async def cancel(self, task_id):
        self.cancelled.append(task_id)

    def complete(self, task, report):
        self.statuses[task.task_id] = ContactStatus(
            task_id=task.task_id,
            run_id=task.run_id,
            status="completed",
            report=report,
            sources=report.sources,
            usage={"searches": 2, "model_turns": 1},
        )


class Research:
    maintenance = False

    def __init__(self, repository):
        self.repository = repository
        self.created = []

    async def create(self, brief, key):
        self.created.append(key)
        return await self.repository.create(brief, key)

    async def cancel(self, run):
        run.status = "cancelled"
        await self.repository.save(run)


class Harness:
    @classmethod
    async def open(cls, path):
        self = cls()
        self.repository = await SQLiteRunRepository.open(path / "runs.sqlite")
        self.store = SalesStore(self.repository)
        await self.store.setup()
        self.worker = Worker()
        self.research = Research(self.repository)
        self.finished = []
        self.reviewed = []
        self.now = NOW
        self.caps = dict.fromkeys(
            ("ready", "postgres", "research", "contacts", "crm", "brain", "auth"), True
        )
        self.scheduler = self.new_scheduler()
        return self

    def new_scheduler(self):
        async def capabilities():
            return self.caps

        async def finish(job, report):
            self.finished.append((job, report))

        async def review(job, report):
            self.reviewed.append(job.kind)
            return report

        return SalesScheduler(
            self.store,
            self.research,
            self.worker,
            capabilities=capabilities,
            finish_contact=finish,
            review_report=review,
            clock=lambda: self.now,
        )

    async def put(self, kind, value):
        async with self.store.transaction() as tx:
            await tx.put(kind, value.id, value.model_dump(mode="json"))

    async def get(self, job):
        async with self.store.transaction() as tx:
            return Job.model_validate(await tx.get("job", job.id))

    async def jobs(self):
        async with self.store.transaction() as tx:
            return [Job.model_validate(v) for v in await tx.list("job")]

    async def close(self):
        await self.repository.close()


def job(**kwargs):
    return Job(
        session_id="session",
        requested_by="curran@toirinc.com",
        query="Research Acme and find its decision-makers",
        **kwargs,
    )


def test_parallel_slots_manual_priority_and_no_brain_dependency(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            h.caps["brain"] = h.caps["ready"] = False
            background = job(kind="enrich", origin="background", company=COMPANY)
            manual = job(kind="resolve")
            discovery = job(kind="discovery")
            for item in (background, manual, discovery):
                await h.put("job", item)
            await h.scheduler.tick()
            assert (await h.get(manual)).status == "running"
            assert (await h.get(discovery)).status == "running"
            assert (await h.get(background)).status == "queued"
            assert len(h.worker.submitted) == len(h.research.created) == 1
            assert (await h.get(manual)).budget_day is None
            assert (await h.get(manual)).deadline_at == (NOW + timedelta(minutes=10)).isoformat()
        finally:
            await h.close()

    asyncio.run(scenario())


def test_resolve_enrich_shared_budget_deadline_and_recovery(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            item = job(kind="resolve", propose_crm=True)
            await h.put("job", item)
            await h.scheduler.tick()
            original = h.worker.submitted[0]
            h.worker.complete(original, ContactReport(company=COMPANY, sources=[SOURCE]))
            h.now += timedelta(minutes=1)
            await h.scheduler.shutdown()
            assert h.worker.cancelled == []
            h.scheduler = h.new_scheduler()
            await h.scheduler.tick()
            second = h.worker.submitted[1]
            assert second.mode == "enrich" and second.task_id != original.task_id
            assert second.run_id == original.run_id and second.deadline_at == original.deadline_at
            assert second.usage["searches"] == 2 and second.prior_sources == [SOURCE]
            h.worker.complete(
                second, ContactReport(company=COMPANY, sources=[SOURCE], summary="Verified")
            )
            await h.scheduler.tick()
            assert (await h.get(item)).status == "completed"
            assert h.reviewed == ["resolve", "enrich"]
            assert len(h.finished) == 1
            await h.scheduler.tick()
            assert len(h.finished) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_lost_contact_session_never_resubmits_and_retry_is_explicit(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            item = job(kind="enrich", company=COMPANY, sources=[SOURCE])
            await h.put("job", item)
            await h.scheduler.tick()
            h.worker.statuses.clear()
            h.scheduler = h.new_scheduler()
            await h.scheduler.tick()
            assert (await h.get(item)).status == "interrupted"
            await h.scheduler.tick()
            assert len(h.worker.submitted) == 1
            replacement = await h.scheduler.retry(item.id)
            assert replacement.id != item.id and replacement.sources == [SOURCE]
            await h.scheduler.tick()
            assert len(h.worker.submitted) == 2
        finally:
            await h.close()

    asyncio.run(scenario())


def test_transient_unreachable_then_deadline_and_explicit_cancel(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            first = job(kind="resolve")
            await h.put("job", first)
            await h.scheduler.tick()
            h.worker.unreachable = True
            await h.scheduler.tick()
            assert (await h.get(first)).status == "running"
            h.now += timedelta(minutes=11)
            await h.scheduler.tick()
            assert (await h.get(first)).status == "failed"
            assert len(h.worker.cancelled) == 1
            second = await h.scheduler.retry(first.id)
            h.worker.unreachable = False
            await h.scheduler.tick()
            await h.scheduler.cancel(second.id)
            assert (await h.get(second)).status == "cancelled"
            assert len(h.worker.cancelled) == 2
        finally:
            await h.close()

    asyncio.run(scenario())


def test_ambiguity_and_pending_proposal_reuse(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            item = job(kind="resolve", propose_crm=True)
            await h.put("job", item)
            await h.scheduler.tick()
            h.worker.complete(
                h.worker.submitted[0], ContactReport(candidates=[COMPANY], sources=[SOURCE])
            )
            await h.scheduler.tick()
            assert (await h.get(item)).status == "needs_input"
            assert len(h.worker.submitted) == 1
            pending = Proposal(
                session_id="elsewhere",
                job_id="prior",
                requested_by=item.requested_by,
                company=COMPANY,
            )
            await h.put("proposal", pending)
            second = job(kind="resolve", propose_crm=True)
            await h.put("job", second)
            await h.scheduler.tick()
            h.worker.complete(
                h.worker.submitted[1], ContactReport(company=COMPANY, sources=[SOURCE])
            )
            await h.scheduler.tick()
            assert (await h.get(second)).status == "completed"
            assert len(h.worker.submitted) == 2
            async with h.store.transaction() as tx:
                messages = await tx.list("message")
                assert any(
                    m["task_ids"] == [pending.id] and m["session_id"] == "session" for m in messages
                )
        finally:
            await h.close()

    asyncio.run(scenario())


def test_la_daily_limits_reset_and_maintenance_drains_without_new_dispatch(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            h.now = datetime(2026, 10, 8, 6, 59, tzinfo=UTC)  # October 7, 23:59 PDT
            await h.put(
                "automation", Automation(enabled=True, daily_enrichments=1, daily_discoveries=1)
            )
            used = job(kind="enrich", origin="background", status="failed", budget_day="2026-10-07")
            queued = job(kind="enrich", origin="background", company=COMPANY, sources=[SOURCE])
            await h.put("job", used)
            await h.put("job", queued)
            await h.scheduler.tick()
            assert (await h.get(queued)).status == "queued"
            assert len(h.research.created) == 1
            h.now += timedelta(minutes=2)
            h.research.maintenance = True
            await h.scheduler.tick()
            assert (await h.get(queued)).status == "queued"
            h.research.maintenance = False
            await h.scheduler.tick()
            assert (await h.get(queued)).status == "running"
            assert (await h.get(queued)).budget_day == "2026-10-08"
        finally:
            await h.close()

    asyncio.run(scenario())


def test_background_requires_every_dependency_and_disabled_by_default(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            await h.scheduler.tick()
            assert await h.jobs() == []
            await h.put("automation", Automation(enabled=True))
            for dependency in h.caps:
                h.caps[dependency] = False
                await h.scheduler.tick()
                assert await h.jobs() == []
                h.caps[dependency] = True
            await h.scheduler.tick()
            assert len(await h.jobs()) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_research_create_link_crash_recovered_without_create_and_qualifies_leads(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            brief = Brief(request="Find qualified US companies needing practical AI integrations")
            item = job(
                kind="discovery", origin="background", status="running", research_brief=brief
            )
            await h.put("job", item)
            run = await h.repository.create(brief, f"sales-discovery:{item.id}")
            accepted = lead()
            accepted.fit_score = 85
            rejected = accepted.model_copy(update={"domain": "low.example", "fit_score": 69})
            run.status = "completed"
            run.report = ResearchReport(leads=[accepted, rejected])
            await h.repository.save(run)
            await h.scheduler.tick()
            current = await h.get(item)
            assert current.status == "completed" and current.research_run_id == run.id
            children = [j for j in await h.jobs() if j.parent_id == item.id]
            assert len(children) == 1 and children[0].company.domain == accepted.domain
            assert h.research.created == []
            await h.scheduler.tick()
            assert len([j for j in await h.jobs() if j.parent_id == item.id]) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_background_suppresses_recent_and_denied_companies(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            await h.put("automation", Automation(enabled=True))
            previous = job(
                kind="enrich",
                status="completed",
                company=COMPANY,
                updated_at=NOW.isoformat(),
                report=ContactReport(company=COMPANY),
            )
            recent = job(kind="enrich", origin="background", company=COMPANY)
            denied_company = COMPANY.model_copy(update={"domain": "denied.example"})
            denied = job(kind="enrich", origin="background", company=denied_company)
            for item in (previous, recent, denied):
                await h.put("job", item)
            async with h.store.transaction() as tx:
                await tx.put(
                    "suppression",
                    denied_company.domain,
                    {
                        "domain": denied_company.domain,
                        "reason": "denied",
                        "until": (NOW + timedelta(days=30)).isoformat(),
                    },
                )
            await h.scheduler.tick()
            assert (await h.get(recent)).status == (await h.get(denied)).status == "completed"
            assert h.worker.submitted == []
        finally:
            await h.close()

    asyncio.run(scenario())


def test_contact_client_redacts_upstream_errors_and_validates_contract():
    async def scenario():
        responses = [
            httpx.Response(500, text="secret-token-provider-body"),
            httpx.Response(200, json={"status": "completed", "unsafe_field": "value"}),
            httpx.Response(404),
        ]

        async def handler(request):
            return responses.pop(0)

        client = ContactAgentClient(
            base_url="https://contacts.example", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(AgentFailure, match="HTTP 500") as failure:
                await client.get("task")
            assert "secret" not in str(failure.value)
            with pytest.raises(AgentFailure, match="invalid status contract"):
                await client.get("task")
            assert await client.get("task") is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_cancellation_during_submit_cancels_the_late_accepted_worker(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            entered, release = asyncio.Event(), asyncio.Event()
            original_submit = h.worker.submit

            async def delayed_submit(task):
                entered.set()
                await release.wait()
                return await original_submit(task)

            h.worker.submit = delayed_submit
            item = job(kind="resolve")
            await h.put("job", item)
            tick = asyncio.create_task(h.scheduler.tick())
            await asyncio.wait_for(entered.wait(), 1)
            await h.scheduler.cancel(item.id)
            release.set()
            await asyncio.wait_for(tick, 1)
            assert (await h.get(item)).status == "cancelled"
            assert len(h.worker.submitted) == 1
            # First cancellation raced before acceptance; second reconciles that race.
            assert h.worker.cancelled == [h.worker.submitted[0].task_id] * 2
        finally:
            await h.close()

    asyncio.run(scenario())


def test_uncertain_submit_polls_same_id_without_repeating_paid_work(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            original_submit = h.worker.submit

            async def uncertain_submit(task):
                await original_submit(task)
                raise AgentFailure("Response lost after worker accepted the request")

            h.worker.submit = uncertain_submit
            item = job(kind="enrich", company=COMPANY, sources=[SOURCE])
            await h.put("job", item)
            await asyncio.gather(h.scheduler.tick(), h.scheduler.tick())
            assert (await h.get(item)).status == "running"
            assert len(h.worker.submitted) == 1
            h.worker.complete(
                h.worker.submitted[0], ContactReport(company=COMPANY, sources=[SOURCE])
            )
            await h.scheduler.tick()
            assert (await h.get(item)).status == "completed"
            assert len(h.worker.submitted) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_double_retry_returns_one_new_attempt(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            item = job(kind="enrich", status="interrupted", company=COMPANY)
            await h.put("job", item)
            first, second = await asyncio.gather(
                h.scheduler.retry(item.id), h.scheduler.retry(item.id)
            )
            assert first.id == second.id
            assert len(await h.jobs()) == 2
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["denied", "researched"])
def test_broad_chat_discovery_respects_suppression_but_named_company_bypasses(tmp_path, reason):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            brief = Brief(request="Find prospects with practical AI integration needs")
            discovery = job(kind="discovery", origin="chat", status="running", research_brief=brief)
            run = await h.repository.create(brief, f"sales-discovery:{discovery.id}")
            run.status = "completed"
            run.report = ResearchReport(
                leads=[lead().model_copy(update={"domain": COMPANY.domain, "fit_score": 85})]
            )
            await h.repository.save(run)
            discovery.research_run_id = run.id
            await h.put("job", discovery)
            async with h.store.transaction() as tx:
                await tx.put(
                    "suppression",
                    COMPANY.domain,
                    {
                        "domain": COMPANY.domain,
                        "reason": reason,
                        "until": (
                            NOW + timedelta(days=30 if reason == "denied" else 7)
                        ).isoformat(),
                    },
                )
            await h.scheduler.tick()
            assert (await h.get(discovery)).status == "completed"
            assert not [j for j in await h.jobs() if j.parent_id == discovery.id]
            assert h.worker.submitted == []

            named = job(kind="enrich", origin="chat", company=COMPANY, sources=[SOURCE])
            await h.put("job", named)
            await h.scheduler.tick()
            assert (await h.get(named)).status == "running"
            assert len(h.worker.submitted) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_queued_discovery_child_rechecks_denial_but_explicit_retry_bypasses(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            discovery = job(kind="discovery", origin="chat", status="completed")
            child = job(
                kind="enrich",
                origin="chat",
                company=COMPANY,
                sources=[SOURCE],
                parent_id=discovery.id,
            )
            await h.put("job", discovery)
            await h.put("job", child)
            # The denial arrives after discovery has already queued enrichment.
            async with h.store.transaction() as tx:
                await tx.put(
                    "suppression",
                    COMPANY.domain,
                    {
                        "domain": COMPANY.domain,
                        "reason": "denied",
                        "until": (NOW + timedelta(days=30)).isoformat(),
                    },
                )
            await h.scheduler.tick()
            assert (await h.get(child)).status == "completed"
            assert h.worker.submitted == []

            failed = job(
                kind="enrich",
                origin="chat",
                status="failed",
                company=COMPANY,
                sources=[SOURCE],
                parent_id=discovery.id,
            )
            await h.put("job", failed)
            explicit_retry = await h.scheduler.retry(failed.id)
            await h.scheduler.tick()
            assert (await h.get(explicit_retry)).status == "running"
            assert len(h.worker.submitted) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("flag", ["maintenance", "stopping"])
def test_drain_flag_changed_while_waiting_for_store_prevents_claim(tmp_path, flag):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            item = job(kind="resolve")
            await h.put("job", item)
            await h.store.lock.acquire()
            try:
                claim = asyncio.create_task(h.scheduler._claim("contacts", Automation(), False))
                await asyncio.sleep(0)
                assert not claim.done()
                if flag == "maintenance":
                    h.research.maintenance = True
                else:
                    await h.scheduler.shutdown()
            finally:
                h.store.lock.release()
            assert await claim is None
            assert (await h.get(item)).status == "queued"
            assert h.worker.submitted == []
        finally:
            await h.close()

    asyncio.run(scenario())
