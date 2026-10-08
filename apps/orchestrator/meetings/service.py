"""Small lifecycle facade; domain components own the workflow."""

import asyncio
import logging

from apps.orchestrator.meetings.capture import MeetingCapture
from apps.orchestrator.meetings.processing import MeetingProcessing
from apps.orchestrator.meetings.subject_client import SubjectResearchClient
from apps.orchestrator.meetings.tasks import MeetingTasks


class MeetingService:
    def __init__(self, store, recall, agent, github, coordinator, subjects=None):
        self.store, self.recall, self.agent = store, recall, agent
        self.github, self.coordinator = github, coordinator
        self.capture = MeetingCapture(store, recall)
        self.subjects = subjects or SubjectResearchClient()
        self.processing = MeetingProcessing(
            store, recall, agent, github, coordinator, self.subjects
        )
        self.tasks = MeetingTasks(store, github)
        self.workers = []
        self.local_operations: set[str] = set()

    @property
    def active_operations(self):
        return self.local_operations | {f"subject:{i}" for i in self.processing.active_subjects}

    async def setup(self):
        await self.store.setup()
        async with self.store.transaction() as tx:
            for meeting in await tx.list("meeting"):
                for mention in meeting.get("research", []):
                    if mention["status"] == "running":
                        self.processing.active_subjects.add(mention.get("task_id", mention["id"]))
                if meeting["status"] == "analyzing":
                    meeting["status"] = "analysis_queued"
                if meeting["status"] == "joining" and not meeting.get("bot_id"):
                    meeting.update(
                        status="join_unknown",
                        error="The service restarted during "
                        "bot creation. Check Recall before adding another bot.",
                    )
                await tx.put("meeting", meeting["id"], meeting)
            for task in await tx.list("task"):
                if task["status"] == "publishing":
                    task.update(
                        status="publish_unknown",
                        error="The service restarted during "
                        "publication. Reconcile the GitHub result before retrying.",
                    )
                    await tx.put("task", task["id"], task)

    def start(self):
        async def loop(tick):
            while True:
                if not self.coordinator.maintenance or tick == self.processing.research_tick:
                    self.local_operations.add(tick.__name__)
                    try:
                        await tick()
                    except Exception:
                        # Durable state remains retryable. Never log transcript/provider bodies.
                        logging.getLogger(__name__).error(
                            "Meeting worker tick failed: %s", tick.__name__
                        )
                    finally:
                        self.local_operations.discard(tick.__name__)
                await asyncio.sleep(2)

        self.workers = [
            asyncio.create_task(loop(tick), name=f"meetings-{tick.__name__}")
            for tick in (
                self.capture.event_tick,
                self.processing.capture_tick,
                self.processing.analysis_tick,
                self.processing.research_tick,
                self.tasks.publish_one,
            )
        ]

    async def close(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await self.recall.close()
        await self.agent.close()
        await self.github.close()
        await self.subjects.close()

    async def workspace(self):
        meetings = await self.store.list("meeting")
        analysis = await self.agent.configured()
        github_ready = (
            await self.github.ready() if hasattr(self.github, "ready") else self.github.configured
        )
        missing = self.recall.missing.copy()
        if not analysis:
            missing.append("Meeting agent / RESPAN_API_KEY")
        if not github_ready:
            missing.append("GitHub connection")
        return {
            "meetings": sorted(meetings, key=lambda m: m["created_at"], reverse=True),
            "tasks": sorted(
                await self.store.list("task"), key=lambda t: t["created_at"], reverse=True
            ),
            "capabilities": {
                "owner_email": self.capture.owner,
                "repository": self.github.repository,
                "zoom_ready": not self.recall.missing,
                "analysis_ready": analysis,
                "github_ready": github_ready,
                "missing": missing,
            },
        }
