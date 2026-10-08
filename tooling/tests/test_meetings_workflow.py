"""Durable customer notes, human approval and provider-safe issue publication."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.orchestrator.coordinator import NotConfigured
from apps.orchestrator.meetings.demo import demo_notes, demo_transcript
from apps.orchestrator.meetings.evidence import validate_evidence
from apps.orchestrator.meetings.models import (
    MeetingCreate,
    TaskDecision,
    TaskEdit,
    TranscriptImport,
)
from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure
from apps.orchestrator.meetings.service import MeetingService
from apps.orchestrator.meetings.store import MeetingStore
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.sqlite import SQLiteRunRepository

ACTOR = {"email": "curran@toirinc.com", "workspace_id": "toir", "role": "sales"}
SECRET = "whsec_MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class FakeRecall:
    missing = []
    workspace_secret = SECRET
    dashboard_secret = SECRET

    def __init__(self):
        self.calls = []
        self.final_segments = demo_transcript().segments
        self.completed_requests = []
        self.final_available = True

    async def join(self, meeting):
        self.calls.append(deepcopy(meeting))
        return {"id": "fixture-bot"}

    async def transcript(self, transcript_id):
        return self.final_segments

    async def completed_transcript(self, bot_id):
        self.completed_requests.append(bot_id)
        return self.final_segments if self.final_available else None

    async def close(self):
        pass


class FakeAgent:
    async def configured(self):
        return True

    async def analyze(self, request):
        return validate_evidence(demo_notes(), request)

    async def close(self):
        pass


class FakeGitHub:
    repository = "curranToir/october-7-th-hack-a-ton"
    configured = True

    def __init__(self):
        self.calls = []
        self.failure = None
        self.match = None

    async def publish(self, task):
        self.calls.append(deepcopy(task))
        await asyncio.sleep(0)
        if self.failure:
            raise self.failure
        return f"https://github.com/{self.repository}/issues/42"

    async def reconcile(self, task):
        return self.match

    async def close(self):
        pass


class FakeSubjects:
    def __init__(self):
        self.calls = []
        self.polls = []
        self.failure = None
        self.result = {"status": "running"}

    async def configured(self):
        return True

    async def submit(self, mention):
        self.calls.append(deepcopy(mention))
        if self.failure:
            raise self.failure
        return {"status": "running", "task_id": mention["task_id"]}

    async def get(self, identifier):
        self.polls.append(identifier)
        return deepcopy(self.result)

    async def close(self):
        pass


class FakeResearchRepository:
    def __init__(self):
        self.busy = False
        self.runs = {}

    async def active(self):
        return self.busy

    async def get(self, identifier):
        return self.runs.get(identifier)


class FakeCoordinator:
    maintenance = False

    def __init__(self):
        self.repository = FakeResearchRepository()
        self.calls = []
        self.failure = None

    async def create(self, brief, key):
        self.calls.append((brief, key))
        if self.failure:
            raise self.failure
        return SimpleNamespace(id="research-run")


@asynccontextmanager
async def meeting_stack(tmp_path):
    path = tmp_path / "meetings.sqlite"
    repository = await SQLiteRunRepository.open(path)
    store = MeetingStore(repository)
    recall, agent, github, coordinator = FakeRecall(), FakeAgent(), FakeGitHub(), FakeCoordinator()
    subjects = FakeSubjects()
    service = MeetingService(store, recall, agent, github, coordinator, subjects=subjects)
    await service.setup()
    try:
        yield SimpleNamespace(
            service=service,
            store=store,
            github=github,
            recall=recall,
            coordinator=coordinator,
            subjects=subjects,
            repository=repository,
            path=path,
        )
    finally:
        await service.close()
        await repository.close()


async def demo_task(stack, key="demo-call"):
    await stack.service.capture.create("demo", None, key, ACTOR)
    await stack.service.processing.analysis_tick()
    return next(task for task in await stack.store.list("task") if task["meeting_id"] == key)


def test_demo_is_durable_and_never_publishes_before_human_approval(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            await stack.service.tasks.publish_one()
            meeting = await stack.store.get("meeting", "demo-call")
            assert meeting["status"] == "ready"
            assert meeting["owner_email"] == ACTOR["email"]
            assert meeting["notes"]["customer"].endswith("(fictional demo)")
            assert len(meeting["transcript"]) == 5
            assert meeting["research"][0]["name"] == "Linear"
            assert meeting["research"][0]["status"] == "queued"
            assert task["status"] == "pending" and task["version"] == 1
            assert "fictional customer report" in task["body"]
            assert "Alex · Example customer · 00:08" in task["body"]
            assert not stack.github.calls and not stack.recall.calls
            assert not stack.coordinator.calls
            assert not await stack.store.list("decision")
            replay = await stack.service.capture.create("demo", None, "demo-call", ACTOR)
            assert replay == meeting
            await stack.service.processing.analysis_tick()
            assert len(await stack.store.list("task")) == 1
            request = TranscriptImport(**demo_transcript().model_dump(), consent_confirmed=True)
            with pytest.raises(Conflict, match="different call"):
                await stack.service.capture.create("import", request, "demo-call", ACTOR)
            path = stack.path
        reopened = await SQLiteRunRepository.open(path)
        try:
            durable = MeetingStore(reopened)
            assert await durable.get("task", task["id"]) == task
            assert await durable.get("meeting", "demo-call") == meeting
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_edit_requires_latest_version_and_approval_freezes_exact_reviewed_content(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            edit = TaskEdit(
                version=1, title="CSV exports omit filtered rows", body="Reviewed issue body. " * 3
            )
            updated = await stack.service.tasks.edit(task["id"], edit)
            assert updated["version"] == 2
            assert updated["body"].count(f"<!-- toir-meeting-task:{task['id']} -->") == 1
            with pytest.raises(Conflict, match="changed"):
                await stack.service.tasks.edit(task["id"], edit)
            with pytest.raises(Conflict, match="latest version"):
                await stack.service.tasks.decide(
                    task["id"],
                    TaskDecision(version=1, decision="approve"),
                    ACTOR,
                )
            assert not await stack.store.list("decision")
            approved = await stack.service.tasks.decide(
                task["id"],
                TaskDecision(version=2, decision="approve"),
                ACTOR,
            )
            assert approved["status"] == "approved" and not stack.github.calls
            decision = (await stack.store.list("decision"))[0]
            assert decision["actor"] == ACTOR["email"]
            assert decision["body"] == updated["body"]
            assert decision["title"] == updated["title"]
            with pytest.raises(Conflict):
                await stack.service.tasks.edit(task["id"], edit.model_copy(update={"version": 2}))
            with pytest.raises(Conflict):
                await stack.service.tasks.decide(
                    task["id"],
                    TaskDecision(version=2, decision="reject"),
                    ACTOR,
                )
            async with stack.store.transaction() as tx:
                with pytest.raises(ValueError, match="immutable"):
                    await tx.put("decision", f"{task['id']}:2", {**decision, "body": "changed"})
            await stack.service.tasks.publish_one()
            assert len(stack.github.calls) == 1
            assert stack.github.calls[0]["body"] == decision["body"]
            assert stack.github.calls[0]["title"] == decision["title"]
            assert stack.github.calls[0]["repository"] == decision["repository"]

    asyncio.run(scenario())


def test_concurrent_and_repeated_approval_publishes_only_once_across_connections(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            second_repo = await SQLiteRunRepository.open(stack.path)
            try:
                second = MeetingService(
                    MeetingStore(second_repo),
                    stack.recall,
                    FakeAgent(),
                    stack.github,
                    stack.coordinator,
                    subjects=stack.subjects,
                )
                decision = TaskDecision(version=1, decision="approve")
                results = await asyncio.gather(
                    *(
                        service.tasks.decide(task["id"], decision, ACTOR)
                        for service in (stack.service, second, stack.service, second)
                    )
                )
                assert all(result["status"] == "approved" for result in results)
                assert len(await stack.store.list("decision")) == 1
                await asyncio.gather(stack.service.tasks.publish_one(), second.tasks.publish_one())
                published = await stack.store.get("task", task["id"])
                assert published["status"] == "published" and published["issue_url"].endswith("/42")
                assert await stack.service.tasks.decide(task["id"], decision, ACTOR) == published
                await asyncio.gather(stack.service.tasks.publish_one(), second.tasks.publish_one())
                assert len(stack.github.calls) == 1
            finally:
                await second_repo.close()

    asyncio.run(scenario())


def test_rejection_is_final_and_github_configuration_is_required_only_for_approval(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            stack.github.configured = False
            with pytest.raises(Conflict, match="Connect GitHub"):
                await stack.service.tasks.decide(
                    task["id"],
                    TaskDecision(version=1, decision="approve"),
                    ACTOR,
                )
            rejected = await stack.service.tasks.decide(
                task["id"],
                TaskDecision(version=1, decision="reject"),
                ACTOR,
            )
            assert rejected["status"] == "rejected"
            await stack.service.tasks.publish_one()
            with pytest.raises(Conflict):
                await stack.service.tasks.retry(task["id"])
            assert not stack.github.calls
            assert len(await stack.store.list("decision")) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("interrupted", [False, True])
def test_uncertain_publication_is_never_automatically_retried_after_restart(tmp_path, interrupted):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            await stack.service.tasks.decide(
                task["id"],
                TaskDecision(version=1, decision="approve"),
                ACTOR,
            )
            if interrupted:
                async with stack.store.transaction() as tx:
                    current = await tx.get("task", task["id"])
                    current["status"] = "publishing"
                    await tx.put("task", task["id"], current)
            else:
                stack.github.failure = OutcomeUnknown("Connection lost after dispatch")
                await stack.service.tasks.publish_one()
            before = len(stack.github.calls)
            await stack.service.setup()
            assert (await stack.store.get("task", task["id"]))["status"] == "publish_unknown"
            for _ in range(3):
                await stack.service.tasks.publish_one()
            with pytest.raises(Conflict):
                await stack.service.tasks.retry(task["id"])
            missing = await stack.service.tasks.reconcile(task["id"])
            assert missing["status"] == "publish_unknown"
            assert "automatic resubmission is disabled" in missing["error"]
            stack.github.match = "https://github.com/example/repository/issues/100"
            reconciled = await stack.service.tasks.reconcile(task["id"])
            assert reconciled["status"] == "published"
            assert reconciled["issue_url"] == stack.github.match
            assert len(stack.github.calls) == before

    asyncio.run(scenario())


def test_confirmed_github_failure_retains_approval_for_explicit_retry(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            await stack.service.tasks.decide(
                task["id"],
                TaskDecision(version=1, decision="approve"),
                ACTOR,
            )
            stack.github.failure = ProviderFailure("GitHub rejected the issue (HTTP 403)")
            await stack.service.tasks.publish_one()
            assert (await stack.store.get("task", task["id"]))["status"] == "publish_failed"
            await stack.service.tasks.publish_one()
            assert len(stack.github.calls) == 1
            stack.github.failure = None
            await stack.service.tasks.retry(task["id"])
            await stack.service.tasks.publish_one()
            published = await stack.store.get("task", task["id"])
            assert published["status"] == "published" and published["version"] == 1
            assert len(await stack.store.list("decision")) == 1
            assert len(stack.github.calls) == 2

    asyncio.run(scenario())


def test_subject_research_preserves_mentions_through_busy_and_unconfigured_states(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            task = await demo_task(stack)
            before = await stack.store.get("meeting", "demo-call")
            stack.subjects.failure = Conflict("Research worker is busy")
            await stack.service.processing.research_tick()
            queued = await stack.store.get("meeting", "demo-call")
            assert queued["research"][0]["status"] == "queued"
            assert queued["research"][0]["name"] == "Linear"
            first_id = queued["research"][0]["task_id"]
            stack.subjects.failure = NotConfigured("Research is unavailable")
            await stack.service.processing.research_tick()
            blocked = await stack.store.get("meeting", "demo-call")
            assert blocked["research"][0]["status"] == "blocked"
            assert blocked["notes"] == before["notes"]
            assert await stack.store.get("task", task["id"]) == task
            stack.subjects.failure = None
            await stack.service.processing.retry("demo-call")
            await stack.service.processing.research_tick()
            running = await stack.store.get("meeting", "demo-call")
            mention = running["research"][0]
            assert mention["status"] == "running" and mention["task_id"] != first_id
            assert mention["name"] == "Linear"
            assert stack.subjects.calls[-1]["task_id"] == mention["task_id"]
            assert "subject:" + mention["task_id"] in stack.service.active_operations
            stack.subjects.result = {
                "status": "completed",
                "summary": "Linear develops issue tracking software.",
                "sources": [
                    {
                        "id": "src_0123456789abcdef",
                        "title": "Linear",
                        "url": "https://linear.app",
                        "retrieved_at": "2026-10-07T12:00:00Z",
                    }
                ],
                "facts": [
                    {
                        "kind": "product",
                        "claim": "Linear develops issue tracking software.",
                        "citations": [
                            {
                                "source_id": "src_0123456789abcdef",
                                "quote": "Issue tracking software for teams",
                            }
                        ],
                    }
                ],
                "gaps": [],
                "identity_status": "resolved",
                "error": None,
            }
            await stack.service.processing.research_tick()
            completed = (await stack.store.get("meeting", "demo-call"))["research"][0]
            assert completed["status"] == "completed"
            assert completed["summary"] == stack.subjects.result["summary"]
            assert completed["sources"] == stack.subjects.result["sources"]
            assert not stack.service.active_operations
            await stack.service.processing.research_tick()
            assert len(stack.subjects.calls) == 3
            assert not stack.github.calls and not stack.coordinator.calls

    asyncio.run(scenario())


def test_lost_subject_worker_result_requires_explicit_retry_with_fresh_remote_id(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            await demo_task(stack)
            await stack.service.processing.research_tick()
            first = (await stack.store.get("meeting", "demo-call"))["research"][0]
            assert first["status"] == "running"
            stack.subjects.result = None
            await stack.service.processing.research_tick()
            failed = (await stack.store.get("meeting", "demo-call"))["research"][0]
            assert failed["status"] == "failed" and failed["name"] == "Linear"
            assert not stack.service.active_operations
            await stack.service.processing.research_tick()
            assert len(stack.subjects.calls) == 1
            await stack.service.processing.retry("demo-call")
            await stack.service.processing.research_tick()
            retried = (await stack.store.get("meeting", "demo-call"))["research"][0]
            assert retried["status"] == "running"
            assert retried["task_id"] != first["task_id"]
            assert len(stack.subjects.calls) == 2

    asyncio.run(scenario())


def test_subject_worker_recovery_replays_submitting_id_and_polls_during_maintenance(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            await demo_task(stack)
            async with stack.store.transaction() as tx:
                meeting = await tx.get("meeting", "demo-call")
                mention = meeting["research"][0]
                identifier = mention["id"]
                mention.update(status="submitting", task_id=identifier)
                await tx.put("meeting", "demo-call", meeting)
            await stack.service.setup()
            await stack.service.processing.research_tick()
            assert stack.subjects.calls[-1]["task_id"] == identifier
            stack.coordinator.maintenance = True
            await stack.service.processing.research_tick()
            assert stack.subjects.polls == [identifier]
            assert len(stack.subjects.calls) == 1
            await stack.service.setup()
            assert "subject:" + identifier in stack.service.active_operations

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["quote", "segment", "subject"])
def test_evidence_rejects_fabricated_quote_segment_and_unmentioned_subject(corruption):
    notes = demo_notes()
    if corruption == "quote":
        notes.issues[0].evidence[0].quote = "This was never said by the customer."
    elif corruption == "segment":
        notes.issues[0].evidence[0].segment_id = "invented-segment"
    else:
        notes.mentions[0].name = "An unrelated company"
    with pytest.raises(ValueError):
        validate_evidence(notes, demo_transcript())


def test_analysis_failure_preserves_transcript_for_explicit_retry(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = TranscriptImport(**demo_transcript().model_dump(), consent_confirmed=True)
            saved = await stack.service.capture.create("import", request, "import-call", ACTOR)
            analyze = stack.service.agent.analyze

            async def unavailable(request):
                raise ValueError("Provider response did not contain valid transcript evidence")

            stack.service.agent.analyze = unavailable
            await stack.service.processing.analysis_tick()
            failed = await stack.store.get("meeting", "import-call")
            assert failed["status"] == "analysis_failed"
            assert failed["transcript"] == saved["transcript"]
            assert failed["notes"] is None and not await stack.store.list("task")
            stack.service.agent.analyze = analyze
            await stack.service.processing.retry("import-call")
            await stack.service.processing.analysis_tick()
            assert (await stack.store.get("meeting", "import-call"))["status"] == "ready"
            assert len(await stack.store.list("task")) == 1
            assert not stack.github.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["done", "call_ended"])
def test_explicit_retry_recovers_completed_transcript_after_missed_final_webhook(tmp_path, status):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://zoom.us/j/12345",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "missed-final", ACTOR)
            await stack.service.processing.capture_tick()
            async with stack.store.transaction() as tx:
                current = await tx.get("meeting", "missed-final")
                current["status"] = status
                await tx.put("meeting", "missed-final", current)
            stack.recall.final_available = False
            with pytest.raises(Conflict, match="not available yet"):
                await stack.service.processing.retry("missed-final")
            assert await stack.store.get("meeting", "missed-final") == current
            stack.recall.final_available = True
            recovered = await stack.service.processing.retry("missed-final")
            assert recovered["status"] == "analysis_queued"
            assert recovered["final_transcript"] is True
            assert recovered["transcript"] == [s.model_dump() for s in stack.recall.final_segments]
            assert stack.recall.completed_requests == ["fixture-bot", "fixture-bot"]
            assert len(stack.recall.calls) == 1  # Recovery never creates another bot.
            await stack.service.processing.analysis_tick()
            assert (await stack.store.get("meeting", "missed-final"))["status"] == "ready"
            assert len(await stack.store.list("task")) == 1
            assert not stack.github.calls
            with pytest.raises(Conflict):
                await stack.service.processing.retry("missed-final")
            assert len(stack.recall.completed_requests) == 2

    asyncio.run(scenario())


def test_completed_transcript_recovery_cannot_overwrite_a_concurrent_final_webhook(tmp_path):
    async def scenario():
        async with meeting_stack(tmp_path) as stack:
            request = MeetingCreate(
                title="Customer call",
                meeting_url="https://zoom.us/j/12345",
                consent_confirmed=True,
            )
            await stack.service.capture.create("zoom", request, "missed-final", ACTOR)
            await stack.service.processing.capture_tick()
            async with stack.store.transaction() as tx:
                current = await tx.get("meeting", "missed-final")
                current["status"] = "done"
                await tx.put("meeting", "missed-final", current)
            started, release = asyncio.Event(), asyncio.Event()

            async def paused_fetch(bot_id):
                started.set()
                await release.wait()
                return stack.recall.final_segments

            stack.recall.completed_transcript = paused_fetch
            worker = asyncio.create_task(stack.service.processing.retry("missed-final"))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                webhook_final = [stack.recall.final_segments[0].model_dump()]
                # Represents the signed final event's transaction while recovery HTTP is in flight.
                async with stack.store.transaction() as tx:
                    current = await tx.get("meeting", "missed-final")
                    current.update(
                        final_transcript=True, transcript=webhook_final, status="analyzing"
                    )
                    await tx.put("meeting", "missed-final", current)
            finally:
                release.set()
                recovered = await worker
            assert recovered == current
            assert await stack.store.get("meeting", "missed-final") == current
            assert len(stack.recall.calls) == 1

    asyncio.run(scenario())
