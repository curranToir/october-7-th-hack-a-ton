"""Durable chat-to-approval integration with provider boundaries replaced by fakes."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from apps.orchestrator.models.research import Citation, Lead, ResearchReport, Signal, Source
from apps.orchestrator.sales.chat import ChatIntent
from apps.orchestrator.sales.contact_client import ContactStatus
from apps.orchestrator.sales.crm_executor import ApprovalRequired, CRMExecutor
from apps.orchestrator.sales.crm_planner import CRMPlanner
from apps.orchestrator.sales.evidence import EvidenceReview
from apps.orchestrator.sales.models import (
    Company,
    Contact,
    ContactReport,
    CRMOperation,
    DecisionRequest,
    Job,
    Proposal,
)
from apps.orchestrator.sales.service import SalesService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.tests.test_sales_crm import FakeCRM

CURRAN = {"email": "curran@toirinc.com", "name": "Curran", "workspace_id": "toir", "role": "sales"}
JARED = {"email": "jared@neptuneops.com", "name": "Jared", "workspace_id": "toir", "role": "sales"}
SOURCE = Source(
    id="src_0123456789abcdef",
    url="https://example.com/team",
    title="Example leadership",
    retrieved_at=datetime(2026, 10, 7, tzinfo=UTC),
    text="Example is a US company at example.com. Alex Rivera is CTO of Example.",
)
COMPANY = Company(
    name="Example",
    domain="https://www.example.com/",
    country="US",
    employee_count=100,
    citations=[Citation(source_id=SOURCE.id, quote="Example is a US company at example.com.")],
)
CONTACT = Contact(
    id="alex",
    name="Alex Rivera",
    title="CTO",
    company_domain="example.com",
    buying_relevance="Leads engineering purchasing decisions",
    citations=[Citation(source_id=SOURCE.id, quote="Alex Rivera is CTO of Example.")],
)
REPORT = ContactReport(
    company=COMPANY,
    contacts=[CONTACT],
    sources=[SOURCE],
    summary="Verified Example and its CTO",
    gaps=["No public business email found."],
)


class Ready:
    async def capabilities(self):
        return {"ready": True}


class Models:
    def __init__(self):
        self.calls = []
        self.intent = ChatIntent(intent="company", query="Research Example", propose_crm=True)
        self.entered = None
        self.release = None

    async def structured(self, schema, prompt, data):
        self.calls.append((schema, data))
        if schema is ChatIntent:
            if self.entered:
                self.entered.set()
                await self.release.wait()
            return self.intent.model_copy(deep=True)
        assert schema is EvidenceReview
        report = data["report"]
        companies = report["candidates"] + ([report["company"]] if report["company"] else [])
        return EvidenceReview(
            accepted_company_domains=[company["domain"] for company in companies],
            accepted_contact_ids=[contact["id"] for contact in report["contacts"]],
        )


class Brain(Ready):
    def __init__(self):
        self.recalls = []
        self.remembered = []

    async def recall(self, user, query, session_id):
        self.recalls.append((user, query, session_id))
        return {"context": [], "withheld": []}

    async def remember(self, item):
        self.remembered.append(item)
        return {"ok": True}


class Worker(Ready):
    def __init__(self):
        self.submitted = []
        self.cancelled = []
        self.statuses = {}

    async def submit(self, task):
        self.submitted.append(task)
        self.statuses[task.task_id] = ContactStatus(
            task_id=task.task_id,
            run_id=task.run_id,
            status="running",
        )

    async def get(self, task_id):
        return self.statuses.get(task_id)

    async def cancel(self, task_id):
        self.cancelled.append(task_id)

    def complete(self, report):
        task = self.submitted[-1]
        self.statuses[task.task_id] = ContactStatus(
            task_id=task.task_id,
            run_id=task.run_id,
            status="completed",
            report=report,
            sources=report.sources,
        )


class Planner:
    def __init__(self):
        self.calls = []
        self.entered = None
        self.release = None

    async def plan(self, report, **links):
        self.calls.append((report, links))
        if self.entered:
            self.entered.set()
            await self.release.wait()
        return Proposal(
            **links,
            company=report.company,
            contacts=report.contacts,
            sources=report.sources,
            gaps=report.gaps,
            operations=[
                CRMOperation(
                    kind="company",
                    action="create",
                    properties={"name": report.company.name, "domain": report.company.domain},
                )
            ],
        )


class Executor:
    def __init__(self, store):
        self.store = store
        self.executed = []
        self.recoveries = 0

    async def recover(self):
        self.recoveries += 1

    async def execute(self, proposal_id):
        async with self.store.transaction() as tx:
            proposal = await tx.get("proposal", proposal_id)
            assert proposal["status"] == "approved"
            assert any(
                decision["proposal_id"] == proposal_id and decision["decision"] == "approved"
                for decision in await tx.list("decision")
            )
            self.executed.append(proposal_id)
            proposal["execution"] = "succeeded"
            await tx.put("proposal", proposal_id, proposal)


class Harness:
    @classmethod
    async def open(cls, directory):
        self = cls()
        self.path = directory / "runs.sqlite"
        self.repository = await SQLiteRunRepository.open(self.path)
        self.models, self.worker, self.brain = Models(), Worker(), Brain()
        self.planner = Planner()
        await self.compose()
        async with self.store.transaction() as tx:
            for actor in (CURRAN, JARED):
                await tx.put("member", actor["email"], {**actor, "active": True})
        return self

    async def compose(self):
        self.store = SalesStore(self.repository)
        self.executor = Executor(self.store)
        research = Ready()
        research.repository, research.maintenance = self.repository, False
        self.service = SalesService(
            self.store,
            research,
            self.models,
            SimpleNamespace(configured=True),
            self.worker,
            Ready(),
            self.planner,
            self.executor,
            self.brain,
        )
        await self.service.setup()

    async def restart(self):
        await self.service.shutdown()
        await self.repository.close()
        self.repository = await SQLiteRunRepository.open(self.path)
        await self.compose()

    async def close(self):
        await self.service.shutdown()
        await self.repository.close()

    async def get(self, kind, record_id):
        async with self.store.transaction() as tx:
            return await tx.get(kind, record_id)

    async def records(self, kind):
        async with self.store.transaction() as tx:
            return await tx.list(kind)

    async def begin(self, key="request-1", content="Research Example and add it to my CRM"):
        session = await self.service.chat.create_session(CURRAN)
        message = await self.service.chat.send(CURRAN, session.id, content, key)
        await self.service.chat.process_next()
        return session, message

    async def enrich(self):
        await self.service.scheduler.tick()
        assert self.worker.submitted[-1].mode == "resolve"
        self.worker.complete(ContactReport(company=COMPANY, sources=[SOURCE]))
        await self.service.scheduler.tick()
        assert self.worker.submitted[-1].mode == "enrich"
        self.worker.complete(REPORT.model_copy(deep=True))
        await self.service.scheduler.tick()


@pytest.mark.parametrize("decision", ["approved", "denied"])
def test_named_company_chat_persists_one_shared_task_until_review(tmp_path, decision):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            session, message = await h.begin()
            await h.enrich()
            assert (await h.get("job", message.job_id))["status"] == "completed"
            workspace = await h.service.workspace(JARED)
            assert len(workspace["tasks"]) == 1
            proposal = Proposal.model_validate(workspace["tasks"][0])
            assert proposal.status == "pending" and proposal.execution == "not_started"
            assert proposal.session_id == session.id
            assert proposal.requested_by == CURRAN["email"]
            links = [m for m in workspace["messages"] if m["task_ids"]]
            assert len(links) == 1 and links[0]["task_ids"] == [proposal.id]
            assert links[0]["session_id"] == session.id
            assert h.brain.recalls[0][0] == CURRAN["email"]
            assert len(await h.records("outbox")) == 1
            await h.service._crm_tick()
            assert h.executor.executed == []
            await h.restart()
            restored = await h.service.workspace(CURRAN)
            assert restored["tasks"][0]["id"] == proposal.id
            assert any(m["task_ids"] == [proposal.id] for m in restored["messages"])
            result = await h.service.proposals.decide(
                proposal.id,
                DecisionRequest(version=1, decision=decision),
                JARED,
                "review-1",
            )
            assert result.decided_by == JARED["email"]
            await h.service._crm_tick()
            assert h.executor.executed == ([proposal.id] if decision == "approved" else [])
            if decision == "denied":
                assert (await h.get("suppression", COMPANY.domain))["reason"] == "denied"
        finally:
            await h.close()

    asyncio.run(scenario())


def test_research_only_chat_never_prepares_or_executes_crm(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            h.models.intent.propose_crm = False
            session, message = await h.begin(content="Please research Example")
            await h.enrich()
            workspace = await h.service.workspace(CURRAN)
            assert workspace["tasks"] == []
            assert h.planner.calls == []
            assert h.executor.executed == []
            assert (await h.get("job", message.job_id))["status"] == "completed"
            findings = [m for m in workspace["messages"] if m["id"] == f"report-{message.job_id}"]
            assert len(findings) == 1 and findings[0]["session_id"] == session.id
            assert "Alex Rivera" in findings[0]["content"]
        finally:
            await h.close()

    asyncio.run(scenario())


def test_explicit_company_only_chat_stops_after_resolution_without_contact_enrichment(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            h.models.intent.propose_crm = False
            h.models.intent.enrich_contacts = False
            h.models.intent.target_count = 1
            _, message = await h.begin(content="Research Example only; do not research contacts")
            await h.service.scheduler.tick()
            assert len(h.worker.submitted) == 1
            assert h.worker.submitted[0].mode == "resolve"
            h.worker.complete(ContactReport(company=COMPANY, sources=[SOURCE]))
            await h.service.scheduler.tick()
            saved = await h.get("job", message.job_id)
            assert saved["status"] == "completed"
            assert saved["enrich_contacts"] is False
            assert saved["report"]["contacts"] == []
            assert saved["memory_status"] == "pending"
            assert len(h.worker.submitted) == 1
            assert len(await h.records("outbox")) == 1
            assert await h.records("proposal") == []
            assert h.planner.calls == h.executor.executed == []
        finally:
            await h.close()

    asyncio.run(scenario())


def test_same_message_idempotency_does_not_repeat_routing_or_research(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            session, original = await h.begin()
            replay = await h.service.chat.send(CURRAN, session.id, original.content, "request-1")
            assert replay == original
            await h.service.chat.process_next()
            assert len([call for call in h.models.calls if call[0] is ChatIntent]) == 1
            with pytest.raises(Conflict, match="different request"):
                await h.service.chat.send(
                    CURRAN, session.id, "Research another company", "request-1"
                )
            await h.enrich()
            assert len(h.worker.submitted) == 2
            assert len(await h.records("proposal")) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_pending_domain_is_reused_in_another_chat_without_second_enrichment(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            await h.begin()
            await h.enrich()
            proposal = (await h.records("proposal"))[0]
            second_session, second_message = await h.begin(key="request-2")
            await h.service.scheduler.tick()
            h.worker.complete(ContactReport(company=COMPANY, sources=[SOURCE]))
            await h.service.scheduler.tick()
            assert len(h.worker.submitted) == 3
            assert len(h.planner.calls) == 1
            assert (await h.get("job", second_message.job_id))["status"] == "completed"
            assert len(await h.records("proposal")) == 1
            messages = await h.records("message")
            assert any(
                m["session_id"] == second_session.id and m["task_ids"] == [proposal["id"]]
                for m in messages
            )
        finally:
            await h.close()

    asyncio.run(scenario())


def test_cancellation_during_chat_model_call_never_queues_research(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            h.models.entered, h.models.release = asyncio.Event(), asyncio.Event()
            session = await h.service.chat.create_session(CURRAN)
            message = await h.service.chat.send(CURRAN, session.id, "Research Example", "race")
            routing = asyncio.create_task(h.service.chat.process_next())
            await asyncio.wait_for(h.models.entered.wait(), 1)
            await h.service.scheduler.cancel(message.job_id)
            h.models.release.set()
            await asyncio.wait_for(routing, 1)
            await h.service.scheduler.tick()
            assert (await h.get("job", message.job_id))["status"] == "cancelled"
            assert h.worker.submitted == []
            assert await h.get("message", f"route-{message.job_id}") is None
        finally:
            await h.close()

    asyncio.run(scenario())


def test_cancellation_during_crm_planning_does_not_publish_proposal(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            _, message = await h.begin()
            await h.service.scheduler.tick()
            h.worker.complete(ContactReport(company=COMPANY, sources=[SOURCE]))
            await h.service.scheduler.tick()
            h.worker.complete(REPORT)
            h.planner.entered, h.planner.release = asyncio.Event(), asyncio.Event()
            finish = asyncio.create_task(h.service.scheduler.tick())
            await asyncio.wait_for(h.planner.entered.wait(), 1)
            await h.service.scheduler.cancel(message.job_id)
            h.planner.release.set()
            await asyncio.wait_for(finish, 1)
            assert (await h.get("job", message.job_id))["status"] == "cancelled"
            assert await h.records("proposal") == []
            assert await h.records("outbox") == []
            assert await h.get("message", f"report-{message.job_id}") is None
        finally:
            await h.close()

    asyncio.run(scenario())


def test_explicit_research_retains_longer_denial_suppression(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            await h.begin()
            await h.enrich()
            proposal = (await h.records("proposal"))[0]
            await h.service.proposals.decide(
                proposal["id"], DecisionRequest(version=1, decision="denied"), CURRAN, "deny"
            )
            before = await h.get("suppression", COMPANY.domain)
            h.models.intent.propose_crm = False
            await h.begin(key="research-again", content="Research Example again")
            await h.enrich()
            after = await h.get("suppression", COMPANY.domain)
            assert after["reason"] == "denied"
            assert after["until"] == before["until"]
            assert after["proposal_id"] == proposal["id"]
        finally:
            await h.close()

    asyncio.run(scenario())


def test_restart_interrupts_lost_chat_and_contact_sessions_without_replay(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            _, message = await h.begin()
            await h.service.scheduler.tick()
            assert len(h.worker.submitted) == 1
            async with h.store.transaction() as tx:
                lost_chat = Job(
                    session_id="orphan-session",
                    requested_by=CURRAN["email"],
                    kind="chat",
                    query="Research another company",
                    status="running",
                )
                await tx.put("job", lost_chat.id, lost_chat.model_dump(mode="json"))
            h.worker.statuses.clear()
            await h.restart()
            await h.service.scheduler.tick()
            await h.service.chat.process_next()
            assert (await h.get("job", message.job_id))["status"] == "interrupted"
            assert (await h.get("job", lost_chat.id))["status"] == "interrupted"
            assert len(h.worker.submitted) == 1
            assert len([call for call in h.models.calls if call[0] is ChatIntent]) == 1
            assert await h.records("proposal") == []
        finally:
            await h.close()

    asyncio.run(scenario())


def test_ambiguous_company_reply_preserves_sources_and_original_crm_intent(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            session, original = await h.begin()
            await h.service.scheduler.tick()
            h.worker.complete(ContactReport(candidates=[COMPANY], sources=[SOURCE]))
            await h.service.scheduler.tick()
            assert (await h.get("job", original.job_id))["status"] == "needs_input"
            followup = await h.service.chat.send(CURRAN, session.id, "example.com", "clarification")
            await h.service.chat.process_next()
            selected = await h.get("job", followup.job_id)
            assert (await h.get("job", original.job_id))["status"] == "completed"
            assert selected["kind"] == "enrich" and selected["propose_crm"] is True
            assert selected["sources"][0]["id"] == SOURCE.id
            await h.service.scheduler.tick()
            h.worker.complete(REPORT)
            await h.service.scheduler.tick()
            assert len(await h.records("proposal")) == 1
            assert len([call for call in h.models.calls if call[0] is ChatIntent]) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


def test_concurrent_finish_for_same_domain_publishes_one_proposal_and_two_links(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            first = await h.service.chat.create_session(CURRAN, "First request")
            second = await h.service.chat.create_session(JARED, "Second request")
            jobs = [
                Job(
                    session_id=session.id,
                    requested_by=actor["email"],
                    kind="enrich",
                    query="Research Example",
                    company=COMPANY,
                    propose_crm=True,
                    status="running",
                )
                for session, actor in ((first, CURRAN), (second, JARED))
            ]
            async with h.store.transaction() as tx:
                for job in jobs:
                    await tx.put("job", job.id, job.model_dump(mode="json"))
            h.planner.entered, h.planner.release = asyncio.Event(), asyncio.Event()
            finishes = [asyncio.create_task(h.service.finish_contact(job, REPORT)) for job in jobs]
            await asyncio.wait_for(h.planner.entered.wait(), 1)

            # Keep both CRM-read paths in flight before either publishes a proposal.
            async def both_entered():
                while len(h.planner.calls) != 2:
                    await asyncio.sleep(0)

            await asyncio.wait_for(both_entered(), 1)
            h.planner.release.set()
            await asyncio.wait_for(asyncio.gather(*finishes), 1)
            proposals = await h.records("proposal")
            assert len(proposals) == 1
            assert len(await h.records("outbox")) == 1
            messages = [m for m in await h.records("message") if m["task_ids"]]
            assert {m["session_id"] for m in messages} == {first.id, second.id}
            assert all(m["task_ids"] == [proposals[0]["id"]] for m in messages)
            assert h.executor.executed == []
        finally:
            await h.close()

    asyncio.run(scenario())


def test_deleting_chat_preserves_pending_task_audit_and_review(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            session, _ = await h.begin()
            await h.enrich()
            proposal = (await h.records("proposal"))[0]
            await h.service.chat.update_session(session.id, delete=True)
            workspace = await h.service.workspace(JARED)
            assert workspace["sessions"] == []
            assert workspace["messages"] == []
            assert [task["id"] for task in workspace["tasks"]] == [proposal["id"]]
            await h.service.proposals.decide(
                proposal["id"],
                DecisionRequest(version=1, decision="approved"),
                JARED,
                "review-deleted-chat",
            )
            assert len(await h.records("decision")) == 1
            assert (await h.get("session", session.id))["deleted"] is True
        finally:
            await h.close()

    asyncio.run(scenario())


def test_manual_chat_survives_unavailable_memory_and_retains_outbox_for_retry(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:

            async def unavailable(*args):
                raise RuntimeError("Brain service is unavailable")

            h.brain.recall = unavailable
            h.brain.remember = unavailable
            await h.begin()
            await h.enrich()
            assert len(await h.records("proposal")) == 1
            await h.service._memory_tick()
            items = await h.records("outbox")
            assert len(items) == 1
            assert items[0]["status"] == "pending" and items[0]["attempts"] == 1
            proposal = (await h.records("proposal"))[0]
            assert proposal["status"] == "pending" and proposal["execution"] == "not_started"
            assert h.executor.executed == []
            await h.restart()
            assert (await h.records("outbox"))[0]["id"] == items[0]["id"]
        finally:
            await h.close()

    asyncio.run(scenario())


def test_same_name_candidates_require_a_distinguishing_reply(tmp_path):
    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            session, original = await h.begin()
            await h.service.scheduler.tick()
            namesake_source = Source(
                id="src_0123456789abcdee",
                url="https://namesake.example/about",
                title="Other Example",
                retrieved_at=SOURCE.retrieved_at,
                text="Example operates namesake.example in the US.",
            )
            namesake = Company(
                name="Example",
                domain="namesake.example",
                citations=[
                    Citation(source_id=namesake_source.id, quote=namesake_source.text),
                ],
            )
            h.worker.complete(
                ContactReport(candidates=[COMPANY, namesake], sources=[SOURCE, namesake_source])
            )
            await h.service.scheduler.tick()
            reply = await h.service.chat.send(CURRAN, session.id, "Example", "ambiguous-reply")
            await h.service.chat.process_next()
            current = await h.get("job", reply.job_id)
            assert current["kind"] == "resolve"
            assert current["company"] is None
            assert (await h.get("job", original.job_id))["status"] == "needs_input"
            assert len([call for call in h.models.calls if call[0] is ChatIntent]) == 2
            assert len(h.worker.submitted) == 1
        finally:
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", ["chat", "background"])
def test_complete_research_to_real_crm_executor_journal_after_approval(tmp_path, origin):
    """Only provider responses and readiness are fake; approval and execution are real."""

    async def scenario():
        h = await Harness.open(tmp_path)
        try:
            crm = FakeCRM()
            crm.capabilities = Ready().capabilities
            h.service.crm = crm
            h.service.planner = CRMPlanner(crm, h.store)
            h.service.executor = CRMExecutor(h.store, crm)
            if origin == "chat":
                session, message = await h.begin()
                await h.enrich()
                enrichment_id = message.job_id
                session_id = session.id
            else:
                # Readiness gate has dedicated tests. Supply its ready outcome here so
                # the background chain can run with the local SQLite test database.
                async def dependencies_ready():
                    return dict.fromkeys(
                        ("ready", "postgres", "research", "contacts", "crm", "brain", "auth"),
                        True,
                    )

                h.service.scheduler.capabilities = dependencies_ready
                accepted = Lead(
                    company=COMPANY.name,
                    domain=COMPANY.domain,
                    country="US",
                    employee_count=100,
                    identity_citations=COMPANY.citations,
                    decision_maker="Alex Rivera, CTO",
                    signals=[
                        Signal(
                            kind="leadership",
                            claim="Alex Rivera leads Example as CTO",
                            event_date=None,
                            citations=CONTACT.citations,
                        )
                    ],
                    ai_use_case="Integrate AI into internal engineering operations.",
                    rationale="The CTO owns a relevant engineering purchasing function.",
                    outreach_angle="Discuss a scoped AI engineering integration.",
                    fit_score=85,
                )
                rejected = accepted.model_copy(
                    update={"domain": "low-fit.example", "fit_score": 69}
                )

                async def accepted_discovery(brief, key, *, planning_context=None):
                    run = await h.repository.create(brief, key)
                    run.status, run.stage = "completed", "completed"
                    run.report = ResearchReport(leads=[accepted, rejected], sources=[SOURCE])
                    await h.repository.save(run)
                    return run

                h.service.research.create = accepted_discovery
                await h.service.update_automation({"enabled": True, "daily_discoveries": 1})
                await h.service.scheduler.tick()  # Persisted background discovery starts.
                await h.service.scheduler.tick()  # Qualified lead dispatches to contacts.
                assert len(h.worker.submitted) == 1
                assert h.worker.submitted[0].mode == "enrich"
                jobs = await h.records("job")
                discovery = next(job for job in jobs if job["kind"] == "discovery")
                enrichment = next(job for job in jobs if job["kind"] == "enrich")
                assert discovery["status"] == "completed"
                assert enrichment["origin"] == "background"
                assert enrichment["parent_id"] == discovery["id"]
                assert enrichment["budget_day"] is not None
                assert enrichment["company"]["domain"] == COMPANY.domain
                enrichment_id, session_id = enrichment["id"], enrichment["session_id"]
                h.worker.complete(REPORT.model_copy(deep=True))
                await h.service.scheduler.tick()
            assert (await h.get("job", enrichment_id))["status"] == "completed"
            workspace = await h.service.workspace(JARED)
            assert len(workspace["tasks"]) == 1
            proposal = Proposal.model_validate(workspace["tasks"][0])
            assert proposal.session_id == session_id
            assert proposal.job_id == enrichment_id
            assert proposal.status == "pending" and proposal.execution == "not_started"
            assert proposal.contacts[0].name == CONTACT.name
            assert any(
                message["session_id"] == session_id and message["task_ids"] == [proposal.id]
                for message in workspace["messages"]
            )
            assert {operation.kind for operation in proposal.operations} == {
                "company",
                "contact",
                "note",
                "association",
            }
            assert crm.writes == []
            assert await h.records("crm_operation") == []
            await h.service._crm_tick()
            with pytest.raises(ApprovalRequired):
                await h.service.executor.execute(proposal.id)
            assert crm.writes == []
            approved = await h.service.proposals.decide(
                proposal.id,
                DecisionRequest(version=proposal.version, decision="approved"),
                JARED,
                f"approve-{origin}",
            )
            assert approved.execution == "queued"
            assert crm.writes == []  # Approval commits before the deterministic executor starts.
            await h.service._crm_tick()
            completed = Proposal.model_validate(await h.get("proposal", proposal.id))
            journal = await h.records("crm_operation")
            assert completed.execution == "succeeded"
            assert completed.decided_by == JARED["email"]
            assert len(crm.companies) == len(crm.contacts) == 1
            assert len(crm.notes) == 2
            assert ("contact-1", "company-1") in crm.associations
            assert "email" not in crm.contacts["contact-1"]["properties"]
            assert {entry["id"] for entry in journal} == {op.id for op in proposal.operations}
            assert all(
                entry["status"] == "succeeded"
                and entry["result_id"]
                and entry["proposal_id"] == proposal.id
                for entry in journal
            )
            assert all(op.status == "succeeded" and op.result_id for op in completed.operations)
            assert {entry["result_id"] for entry in journal if entry["kind"] == "company"} == {
                "company-1",
            }
            assert {entry["result_id"] for entry in journal if entry["kind"] == "contact"} == {
                "contact-1",
            }
            writes = len(crm.writes)
            await h.service._crm_tick()
            await h.service.executor.execute(proposal.id)
            assert len(crm.writes) == writes
            # A new coordinator can read all terminal IDs and audits without re-executing.
            await h.restart()
            assert (await h.get("proposal", proposal.id))["execution"] == "succeeded"
            assert len(await h.records("crm_operation")) == len(journal)
            assert len(await h.records("decision")) == 1
        finally:
            await h.close()

    asyncio.run(scenario())
