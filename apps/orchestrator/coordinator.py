"""Run ownership and lifecycle. Graph nodes own research decisions, not scheduling."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace

from apps.orchestrator.agents.research import AgentCancelled, AgentFailure, SessionLost
from apps.orchestrator.integrations.models import ModelUnavailable
from apps.orchestrator.models.research import Brief, Run
from apps.orchestrator.storage.ports import Conflict


class NotConfigured(Exception):
    pass


class Coordinator:
    def __init__(self, repository, graph, models, agent, data_dir: Path):
        self.repository = repository
        self.graph = graph
        self.models = models
        self.agent = agent
        self.data_dir = data_dir
        self.tasks: dict[str, asyncio.Task] = {}
        self.stopping = False
        self.admission_lock = asyncio.Lock()
        path = data_dir / "maintenance.json"
        self.maintenance = (
            json.loads(path.read_text()).get("enabled", False) if path.exists() else False
        )

    async def capabilities(self) -> dict:
        missing = [] if self.models.client else ["RESPAN_API_KEY"]
        try:
            agent = await self.agent.capabilities()
            # The worker's v1 contract calls this missing_credentials. Keep the
            # browser-facing aggregate named missing for the existing UI.
            missing.extend(agent.get("missing_credentials", []))
        except AgentFailure:
            agent = {"configured": False, "missing": ["Research agent unavailable"]}
            missing.extend(agent["missing"])
        return {
            "configured": not missing and bool(agent.get("configured")),
            "missing": sorted(set(missing)),
            "agent": "research",
            "maintenance": self.maintenance,
        }

    async def create(
        self, brief: Brief, key: str, parent_id: str | None = None,
        *, planning_context: dict | None = None,
    ) -> Run:
        async with self.admission_lock:
            existing = await self.repository.replay(brief, key, parent_id)
            if existing:
                return existing
            if self.maintenance:
                raise Conflict("Research is paused for maintenance")
            if not (await self.capabilities())["configured"]:
                raise NotConfigured(
                    "Research requires a configured Respan and Scalekit Exa connection"
                )
            run = await self.repository.create(brief, key, parent_id)
            if run.status == "queued" and run.id not in self.tasks:
                self.start(run, planning_context=planning_context)
            return run

    def start(self, run: Run, *, planning_context: dict | None = None):
        task = asyncio.create_task(
            self.execute(run, planning_context=planning_context), name=f"research-{run.id}",
        )
        self.tasks[run.id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run.id, None))

    async def recover(self):
        run = await self.repository.active()
        if not run:
            return
        if run.task_id and datetime.fromisoformat(run.deadline_at) > datetime.now(UTC):
            try:
                if await self.agent.get(run.task_id):
                    await self.repository.event(
                        run.id, "recovering", "Reconnecting to existing agent task"
                    )
                    self.start(run)
                    return
            except AgentFailure:
                pass
        run.status, run.stage = "interrupted", "interrupted"
        run.error = (
            "Coordinator restarted without a recoverable agent task. Retry using saved evidence."
        )
        await self.repository.save(run)
        await self.repository.event(run.id, run.stage, run.error)

    async def stop_agent(self, run: Run):
        if run.task_id:
            try:
                await self.agent.cancel(run.task_id)
            except AgentFailure:
                # The worker also enforces the original absolute deadline.
                await self.repository.event(
                    run.id, "cancelling", "Agent unreachable; its deadline remains enforced"
                )

    async def execute(self, run: Run, *, planning_context: dict | None = None):
        run.status = "running"
        await self.repository.save(run)
        try:
            remaining = (
                datetime.fromisoformat(run.deadline_at) - datetime.now(UTC)
            ).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                with trace.get_tracer("toir.coordinator").start_as_current_span(
                    "research.run"
                ) as span:
                    span.set_attribute("run.id", run.id)
                    span.set_attribute("respan.threads.thread_identifier", run.id)
                    context = span.get_span_context()
                    run.trace_id = f"{context.trace_id:032x}" if context.is_valid else None
                    await self.repository.save(run)
                    await self.graph.ainvoke(
                        {"run": run.model_dump(mode="json"), "follow_up_queries": [],
                         "planning_context": planning_context or {}},
                        config={
                            "configurable": {"thread_id": run.id},
                            "recursion_limit": 20,
                            "metadata": {"run_id": run.id, "customer_identifier": "toir"},
                        },
                    )
            return
        except asyncio.CancelledError:
            if self.stopping:
                return  # Leave the remote task and durable run for startup reconciliation.
            status, message = "cancelled", "Research cancelled by the user"
        except TimeoutError:
            status, message = (
                "failed",
                "Research reached its time limit; saved evidence is available for retry",
            )
        except SessionLost as error:
            status, message = "interrupted", str(error)
        except AgentCancelled as error:
            status, message = "cancelled", str(error)
        except (AgentFailure, ModelUnavailable) as error:
            status, message = "failed", str(error)
        except Exception:
            status, message = (
                "failed",
                "Research failed an internal validation; saved evidence is available for retry",
            )
        current = await self.repository.get(run.id)
        if current:
            await self.stop_agent(current)
            current.status, current.stage, current.error = status, status, message
            await self.repository.save(current)
            await self.repository.event(current.id, status, message)

    async def cancel(self, run: Run) -> Run:
        if run.status not in {"queued", "running"}:
            return run
        task = self.tasks.get(run.id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        current = await self.repository.get(run.id)
        if current and current.status in {"queued", "running"}:
            run = current
            await self.stop_agent(run)
            run.status, run.stage = "cancelled", "cancelled"
            await self.repository.save(run)
        return await self.repository.get(run.id)

    async def maintenance_status(self):
        active = await self.repository.active()
        meetings = getattr(self, "meeting_service", None)
        return {
            "enabled": self.maintenance, "active_run_id": active.id if active else None,
            "active_meeting_operations": sorted(meetings.active_operations) if meetings else [],
        }

    async def set_maintenance(self, enabled: bool):
        async with self.admission_lock:
            path = self.data_dir / "maintenance.json"

            def write():
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps({"enabled": enabled}))
                temp.replace(path)

            await asyncio.to_thread(write)
            self.maintenance = enabled
        return await self.maintenance_status()

    async def shutdown(self):
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
