"""Versioned human decisions and a durable GitHub outbox."""

from apps.orchestrator.meetings.models import TaskDecision, TaskEdit
from apps.orchestrator.meetings.providers import OutcomeUnknown, ProviderFailure
from apps.orchestrator.models.research import now
from apps.orchestrator.storage.ports import Conflict


class MeetingTasks:
    def __init__(self, store, github):
        self.store, self.github = store, github

    async def edit(self, identifier, edit: TaskEdit):
        async with self.store.transaction() as tx:
            task = await tx.get("task", identifier)
            if not task:
                raise LookupError()
            if task["status"] != "pending" or task["version"] != edit.version:
                raise Conflict("The task changed; refresh before editing")
            marker = f"<!-- toir-meeting-task:{identifier} -->"
            task.update(
                title=edit.title,
                body=edit.body.replace(marker, "").rstrip() + "\n\n" + marker,
                version=task["version"] + 1,
            )
            await tx.put("task", identifier, task)
            return task

    async def decide(self, identifier, decision: TaskDecision, actor):
        async with self.store.transaction() as tx:
            task = await tx.get("task", identifier)
            if not task:
                raise LookupError()
            key = f"{identifier}:{decision.version}"
            existing = await tx.get("decision", key)
            if existing and existing["decision"] == decision.decision:
                return task
            if task["status"] != "pending" or task["version"] != decision.version:
                raise Conflict("The task changed; review the latest version before deciding")
            if decision.decision == "approve" and not self.github.configured:
                raise Conflict("Connect GitHub before approving this issue")
            task.update(
                status="approved" if decision.decision == "approve" else "rejected",
                decided_by=actor["email"],
                decided_at=now(),
                error=None,
            )
            await tx.put(
                "decision",
                key,
                {
                    "task_id": identifier,
                    "version": task["version"],
                    "decision": decision.decision,
                    "actor": actor["email"],
                    "created_at": task["decided_at"],
                    "title": task["title"],
                    "body": task["body"],
                    "repository": task["repository"],
                },
            )
            await tx.put("task", identifier, task)
            return task

    async def publish_one(self):
        async with self.store.transaction() as tx:
            pending = [t for t in await tx.list("task") if t["status"] == "approved"]
            if not pending:
                return
            task = pending[0]
            task["status"] = "publishing"
            await tx.put("task", task["id"], task)
        try:
            url = await self.github.publish(task)
            task.update(status="published", issue_url=url, error=None)
        except OutcomeUnknown as error:
            task.update(status="publish_unknown", error=str(error))
        except ProviderFailure as error:
            task.update(status="publish_failed", error=str(error))
        async with self.store.transaction() as tx:
            await tx.put("task", task["id"], task)

    async def retry(self, identifier):
        async with self.store.transaction() as tx:
            task = await tx.get("task", identifier)
            if not task:
                raise LookupError()
            if task["status"] != "publish_failed":
                raise Conflict("Only a confirmed failed publication can be retried")
            task.update(status="approved", error=None)
            await tx.put("task", identifier, task)
            return task

    async def reconcile(self, identifier):
        task = await self.store.get("task", identifier)
        if not task:
            raise LookupError()
        if task["status"] != "publish_unknown":
            raise Conflict("Only uncertain GitHub publications need reconciliation")
        url = await self.github.reconcile(task)
        async with self.store.transaction() as tx:
            current = await tx.get("task", identifier)
            if current["status"] == "publish_unknown":
                if url:
                    current.update(status="published", issue_url=url, error=None)
                else:
                    current["error"] = (
                        "No matching issue found in the latest 1,000 issues. "
                        "Check GitHub manually; automatic resubmission is disabled."
                    )
                await tx.put("task", identifier, current)
            return current
