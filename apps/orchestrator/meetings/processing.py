"""Capture scheduling, notes and independent, source-backed mention research."""

import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.coordinator import NotConfigured
from apps.orchestrator.meetings.demo import demo_notes
from apps.orchestrator.meetings.evidence import issue_body, validate_evidence
from apps.orchestrator.meetings.models import AnalysisRequest
from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure, RetryLater
from apps.orchestrator.models.research import now
from apps.orchestrator.storage.ports import Conflict


class MeetingProcessing:
    def __init__(self, store, recall, agent, github, coordinator, subjects):
        self.store, self.recall, self.agent = store, recall, agent
        self.github, self.coordinator = github, coordinator
        self.subjects = subjects
        self.active_subjects: set[str] = set()

    async def capture_tick(self):
        for meeting in await self.store.list("meeting"):
            if meeting["status"] != "scheduled" or meeting.get("next_attempt", 0) > time.time():
                continue
            async with self.store.transaction() as tx:
                meeting = await tx.get("meeting", meeting["id"])
                if meeting["status"] != "scheduled":
                    continue
                meeting.update(status="joining", join_attempts=meeting["join_attempts"] + 1)
                await tx.put("meeting", meeting["id"], meeting)
            try:
                bot = await self.recall.join(meeting)
                if not isinstance(bot.get("id"), str) or not bot["id"]:
                    raise OutcomeUnknown("Recall returned no bot receipt; check the dashboard")
                patch = {"bot_id": bot["id"], "error": None}
            except RetryLater as error:
                patch = {
                    "status": "scheduled" if meeting["join_attempts"] < 10 else "capture_failed",
                    "next_attempt": time.time() + error.delay,
                    "error": str(error),
                }
            except OutcomeUnknown as error:
                patch = {"status": "join_unknown", "error": str(error)}
            except ProviderFailure as error:
                patch = {"status": "capture_failed", "error": str(error)}
            async with self.store.transaction() as tx:
                current = await tx.get("meeting", meeting["id"])
                if current["status"] == "joining" and not current.get("bot_id"):
                    current.update(patch)
                elif "bot_id" in patch and not current.get("bot_id"):
                    current["bot_id"] = patch["bot_id"]
                await tx.put("meeting", current["id"], current)

    async def analysis_tick(self):
        for meeting in await self.store.list("meeting"):
            if meeting["status"] != "analysis_queued":
                continue
            async with self.store.transaction() as tx:
                meeting = await tx.get("meeting", meeting["id"])
                if meeting["status"] != "analysis_queued":
                    continue
                meeting["status"] = "analyzing"
                await tx.put("meeting", meeting["id"], meeting)
            try:
                request = AnalysisRequest(title=meeting["title"], segments=meeting["transcript"])
                notes = (
                    validate_evidence(demo_notes(), request)
                    if meeting["source"] == "demo"
                    else await self.agent.analyze(request)
                )
                async with self.store.transaction() as tx:
                    current = await tx.get("meeting", meeting["id"])
                    current.update(status="ready", notes=notes.model_dump(), error=None)
                    current["research"] = [
                        {
                            "id": str(uuid5(NAMESPACE_URL, f"meeting:{meeting['id']}:mention:{i}")),
                            **m.model_dump(),
                            "status": "queued",
                            "run_id": None,
                            "error": None,
                        }
                        for i, m in enumerate(notes.mentions)
                    ]
                    for index, issue in enumerate(notes.issues):
                        identifier = str(
                            uuid5(NAMESPACE_URL, f"meeting:{meeting['id']}:issue:{index}")
                        )
                        task = {
                            "id": identifier,
                            "meeting_id": meeting["id"],
                            "title": issue.title,
                            "body": issue_body(issue, meeting)
                            + f"\n\n<!-- toir-meeting-task:{identifier} -->",
                            "repository": self.github.repository,
                            "version": 1,
                            "status": "pending",
                            "source": meeting["source"],
                            "created_at": now(),
                            "issue_url": None,
                            "error": None,
                        }
                        await tx.put("task", identifier, task)
                    await tx.put("meeting", current["id"], current)
            except Exception:
                async with self.store.transaction() as tx:
                    current = await tx.get("meeting", meeting["id"])
                    current.update(
                        status="analysis_failed",
                        error="Analysis failed. The saved transcript is available for retry.",
                    )
                    await tx.put("meeting", current["id"], current)

    async def research_tick(self):
        for meeting in await self.store.list("meeting"):
            for mention in meeting.get("research", []):
                status = mention["status"]
                if status not in {"queued", "submitting", "running"}:
                    continue
                if self.coordinator.maintenance and status != "running":
                    continue
                identifier = mention.get("task_id") or mention["id"]
                try:
                    if status == "running":
                        result = await self.subjects.get(identifier)
                        if result is None:
                            raise AgentFailure("Research worker restarted; retry the saved subject")
                    else:
                        # Save the stable remote task ID before sending, so restart can replay.
                        async with self.store.transaction() as tx:
                            current = await tx.get("meeting", meeting["id"])
                            stored = next(
                                m for m in current["research"] if m["id"] == mention["id"]
                            )
                            stored.update(status="submitting", task_id=identifier)
                            await tx.put("meeting", current["id"], current)
                        result = await self.subjects.submit({**mention, "task_id": identifier})
                    patch = {
                        key: result[key]
                        for key in (
                            "status",
                            "summary",
                            "facts",
                            "sources",
                            "gaps",
                            "identity_status",
                            "error",
                        )
                        if key in result
                    }
                    patch["task_id"] = identifier
                except Conflict:
                    patch = {"status": "queued", "task_id": identifier}
                except NotConfigured:
                    patch = {
                        "status": "blocked",
                        "error": "Configure the research agent, "
                        "Respan and Scalekit Exa, then retry research.",
                    }
                except AgentFailure:
                    patch = {
                        "status": "failed" if status == "running" else "blocked",
                        "error": "Research was interrupted or is unavailable; retry the subject.",
                    }
                async with self.store.transaction() as tx:
                    current = await tx.get("meeting", meeting["id"])
                    next(m for m in current["research"] if m["id"] == mention["id"]).update(patch)
                    await tx.put("meeting", current["id"], current)
                if patch["status"] == "running":
                    self.active_subjects.add(identifier)
                else:
                    self.active_subjects.discard(identifier)
                if status != "running":
                    return  # At most one new subject admission per tick.

    async def retry(self, identifier):
        existing = await self.store.get("meeting", identifier)
        if not existing:
            raise LookupError()
        if existing["status"] in {"done", "call_ended"} and existing.get("bot_id"):
            segments = await self.recall.completed_transcript(existing["bot_id"])
            if segments is None:
                raise Conflict("The completed transcript is not available yet; check again later")
            AnalysisRequest(title=existing["title"], segments=segments)
            async with self.store.transaction() as tx:
                current = await tx.get("meeting", identifier)
                if not current.get("final_transcript"):
                    current.update(
                        transcript=[s.model_dump() for s in segments],
                        final_transcript=True,
                        status="analysis_queued",
                        error=None,
                    )
                    await tx.put("meeting", identifier, current)
                return current
        async with self.store.transaction() as tx:
            meeting = await tx.get("meeting", identifier)
            if not meeting:
                raise LookupError()
            if meeting["status"] == "analysis_failed":
                meeting.update(status="analysis_queued", error=None)
            elif meeting["status"] == "capture_failed":
                retried = False
                for event in await tx.list("event"):
                    if event["status"] == "failed" and event["payload"]["data"]["bot"][
                        "id"
                    ] == meeting.get("bot_id"):
                        event.update(status="queued", attempts=0, next_attempt=0)
                        await tx.put("event", event["id"], event)
                        retried = True
                if not retried:
                    raise Conflict("Check Recall for the capture failure, or import a transcript")
                meeting.update(error=None)
            else:
                blocked = [
                    m
                    for m in meeting["research"]
                    if m["status"] in {"blocked", "failed", "cancelled"}
                ]
                if not blocked:
                    raise Conflict("There is no retryable work on this meeting")
                for mention in blocked:
                    mention.update(status="queued", error=None, task_id=str(uuid4()))
            await tx.put("meeting", identifier, meeting)
            return meeting
