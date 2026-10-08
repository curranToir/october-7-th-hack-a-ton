"""Composition and lifecycle for sales workflows; domain code lives in small modules."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

from apps.orchestrator.sales.brain import (
    deliver_memory,
    enqueue_discovery,
    enqueue_memory,
    enqueue_research,
)
from apps.orchestrator.sales.chat import ChatService
from apps.orchestrator.sales.evidence import review_report
from apps.orchestrator.sales.models import (
    Automation,
    ContactReport,
    Job,
    Message,
    Proposal,
    timestamp,
)
from apps.orchestrator.sales.proposals import ProposalService
from apps.orchestrator.sales.scheduler import SalesScheduler


class SalesService:
    def __init__(self, store, research, models, auth, contacts, crm, planner, executor, brain):
        self.store, self.research, self.models, self.auth = store, research, models, auth
        self.contacts, self.crm, self.planner, self.executor, self.brain = (
            contacts,
            crm,
            planner,
            executor,
            brain,
        )
        self.chat = ChatService(store, models, brain)
        self.proposals = ProposalService(store)
        self.scheduler = SalesScheduler(
            store,
            research,
            contacts,
            capabilities=self.capabilities,
            finish_contact=self.finish_contact,
            review_report=lambda job, report: review_report(models, job, report),
            finished_discovery=self.finish_discovery,
            planning_context=self.discovery_context,
        )
        self.stopping = False
        self.tasks = []
        self.capability_cache = None
        self.capability_time = 0.0
        self.capability_lock = asyncio.Lock()
        self.last_error = None

    async def setup(self):
        await self.store.setup()
        async with self.store.transaction() as tx:
            if not await tx.get("automation", "default"):
                automation = Automation()
                await tx.put("automation", "default", automation.model_dump(mode="json"))
        await self.chat.recover()
        await self.executor.recover()

    async def capabilities(self, *, refresh=False):
        async with self.capability_lock:
            if (
                self.capability_cache
                and not refresh
                and time.monotonic() - self.capability_time < 30
            ):
                result = dict(self.capability_cache)
            else:

                async def check(name, call):
                    try:
                        async with asyncio.timeout(15):
                            response = await call()
                        ready = bool(response.get("ready", response.get("configured", False)))
                        reasons = response.get(
                            "reasons",
                            response.get("missing", response.get("missing_credentials", [])),
                        )
                        return name, ready, list(reasons) if isinstance(reasons, list) else []
                    except Exception:
                        return name, False, [f"{name.capitalize()} service is unavailable"]

                results = await asyncio.gather(
                    check("research", self.research.capabilities),
                    check("contacts", self.contacts.capabilities),
                    check("crm", self.crm.capabilities),
                    check("brain", self.brain.capabilities),
                )
                result = {
                    "postgres": self.store.is_postgres,
                    "auth": self.auth.configured,
                    "reasons": [],
                }
                for name, ready, reasons in results:
                    result[name] = ready
                    if not ready:
                        result["reasons"].extend(reasons or [f"{name.capitalize()} is not ready"])
                if not result["postgres"]:
                    result["reasons"].append(
                        "Continuous prospecting requires the Postgres workflow database"
                    )
                if not result["auth"]:
                    result["reasons"].append("Scalekit sign-in is not configured")
                result["ready"] = all(
                    result[key]
                    for key in ("postgres", "auth", "research", "contacts", "crm", "brain")
                )
                self.capability_cache, self.capability_time = result, time.monotonic()
            result = {**result, "reasons": list(result["reasons"])}
            # Manual public research has no dependency on CRM write attestation.
            # `ready` retains its stricter meaning for autonomous background dispatch.
            result["research_ready"] = all(
                result[key] for key in ("postgres", "auth", "research")
            )
            if self.research.maintenance:
                result["ready"] = False
                result["research_ready"] = False
                result["reasons"].append("Workflow is paused for maintenance")
            if self.last_error:
                result["reasons"].append(self.last_error)
            return result

    async def discovery_context(self, job: Job) -> dict:
        try:
            return await self.brain.recall(job.requested_by, job.query, job.session_id)
        except RuntimeError:
            # Public research continues if private memory is unavailable. Never
            # substitute a different user or publish unfiltered recalled data.
            return {"context": [], "unavailable": True}

    async def finish_discovery(self, job: Job, run):
        """Publish accepted findings even if contact enrichment is unavailable."""
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if not current or current["status"] != "running":
                return
            await enqueue_discovery(tx, job, run)
            source_urls = {source.id: str(source.url) for source in run.report.sources}
            lines = [run.report.summary or f"{len(run.report.leads)} companies qualified."]
            for lead in run.report.leads:
                citations = lead.identity_citations + [
                    citation for signal in lead.signals for citation in signal.citations
                ]
                urls = list(dict.fromkeys(
                    source_urls[citation.source_id] for citation in citations
                    if citation.source_id in source_urls
                ))[:3]
                lines.append(
                    f"{lead.company} ({lead.domain}) — {lead.rationale}\n"
                    f"AI opportunity (hypothesis): {lead.ai_use_case[:400]}\n"
                    + "Sources: " + ", ".join(urls)
                )
            if run.report.gaps:
                lines.append("Research gaps: " + "; ".join(run.report.gaps))
            message = Message(
                id=f"discovery-report-{job.id}", session_id=job.session_id,
                role="assistant", content="\n\n".join(lines)[:12000], job_id=job.id,
            )
            await tx.put("message", message.id, message.model_dump(mode="json"))

    async def workspace(self, actor):
        capabilities = await self.capabilities()
        async with self.store.transaction() as tx:
            sessions = [s for s in await tx.list("session") if not s.get("deleted")]
            ids = {s["id"] for s in sessions}
            return {
                "user": actor,
                "sessions": sorted(sessions, key=lambda s: s["created_at"], reverse=True),
                "messages": sorted(
                    (m for m in await tx.list("message") if m["session_id"] in ids),
                    key=lambda m: m["created_at"],
                ),
                "tasks": sorted(
                    await tx.list("proposal"), key=lambda p: p["created_at"], reverse=True
                ),
                "jobs": sorted(await tx.list("job"), key=lambda j: j["created_at"], reverse=True),
                "automation": await tx.get("automation", "default"),
                "capabilities": capabilities,
            }

    async def finish_contact(self, job: Job, report: ContactReport):
        if not report.company:
            raise ValueError("Company identity was not verified")
        async with self.store.transaction() as tx:
            existing = next(
                (
                    p
                    for p in await tx.list("proposal")
                    if p["job_id"] == job.id
                    or (
                        p["status"] == "pending" and p["company"]["domain"] == report.company.domain
                    )
                ),
                None,
            )
        proposal = None
        if job.propose_crm:
            proposal = (
                Proposal.model_validate(existing)
                if existing
                else await self.planner.plan(
                    report,
                    session_id=job.session_id,
                    job_id=job.id,
                    requested_by=job.requested_by,
                )
            )
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if not current or current["status"] != "running":
                return
            if proposal:
                # Another path may have finished while CRM reads were in flight.
                duplicate = next(
                    (
                        p
                        for p in await tx.list("proposal")
                        if p["job_id"] == job.id
                        or (
                            p["status"] == "pending"
                            and p["company"]["domain"] == report.company.domain
                        )
                    ),
                    None,
                )
                if duplicate:
                    proposal = Proposal.model_validate(duplicate)
                elif not await tx.get("proposal", proposal.id):
                    await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
                    await enqueue_memory(tx, proposal)
            else:
                await enqueue_research(tx, job, report)
            lines = [
                report.summary or f"Researched {report.company.name} ({report.company.domain})."
            ]
            for contact in report.contacts:
                lines.append(
                    f"{contact.name} — {contact.title}. {contact.buying_relevance}"
                    + (f" LinkedIn: {contact.linkedin_url}" if contact.linkedin_url else "")
                )
            if proposal:
                lines.append(
                    "The CRM proposal is ready in this session and Tasks. "
                    "No CRM changes have been made."
                )
            if report.gaps:
                lines.append("Research gaps: " + "; ".join(report.gaps[:5]))
            message = Message(
                id=f"report-{job.id}",
                session_id=job.session_id,
                role="assistant",
                content="\n\n".join(lines)[:12000],
                task_ids=[proposal.id] if proposal else [],
                job_id=job.id,
            )
            await tx.put("message", message.id, message.model_dump(mode="json"))
            previous = await tx.get("suppression", report.company.domain)
            researched_until = datetime.now(UTC) + timedelta(days=7)
            if not previous or datetime.fromisoformat(previous["until"]) < researched_until:
                await tx.put(
                    "suppression",
                    report.company.domain,
                    {
                        "workspace_id": "toir",
                        "domain": report.company.domain,
                        "reason": "researched",
                        "until": researched_until.isoformat(),
                        "job_id": job.id,
                    },
                )

    async def update_automation(self, data):
        allowed = {
            "enabled",
            "request",
            "employee_min",
            "employee_max",
            "fit_threshold",
            "daily_enrichments",
            "daily_discoveries",
        }
        if not data or set(data) - allowed:
            raise ValueError("Only automation settings may be updated")
        async with self.store.transaction() as tx:
            previous = await tx.get("automation", "default") or Automation().model_dump(mode="json")
            automation = Automation.model_validate({**previous, **data, "updated_at": timestamp()})
            await tx.put("automation", automation.id, automation.model_dump(mode="json"))
        return automation

    async def maintenance_status(self):
        async with self.store.transaction() as tx:
            jobs = [j for j in await tx.list("job") if j["status"] == "running"]
            crm = [p for p in await tx.list("proposal") if p["execution"] == "running"]
        return {
            "storage_backend": "postgres" if self.store.is_postgres else "sqlite",
            "active_jobs": [j["id"] for j in jobs],
            "active_contact_task_id": next(
                (j.get("task_id") for j in jobs if j["kind"] in {"resolve", "enrich"}), None
            ),
            "active_crm_operations": len(crm),
        }

    def start(self):
        self.tasks = [
            asyncio.create_task(self._loop(self._chat_tick), name="sales-chat"),
            asyncio.create_task(self._loop(self.scheduler.tick), name="sales-scheduler"),
            asyncio.create_task(self._loop(self._crm_tick), name="sales-crm"),
            asyncio.create_task(self._loop(self._memory_tick, 15), name="sales-memory"),
        ]

    async def _loop(self, work, interval=2):
        while not self.stopping:
            try:
                await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Surface an operational fault without leaking provider response bodies.
                self.last_error = (
                    "A workflow operation failed; saved jobs remain available for review."
                )
            await asyncio.sleep(interval)

    async def _chat_tick(self):
        if not self.research.maintenance:
            await self.chat.process_next(can_dispatch=lambda: not self.research.maintenance)

    async def _crm_tick(self):
        if self.research.maintenance:
            return
        async with self.store.transaction() as tx:
            pending = next(
                (
                    p
                    for p in await tx.list("proposal")
                    if p["status"] == "approved" and p["execution"] == "queued"
                ),
                None,
            )
        if pending:
            await self.executor.execute(pending["id"])

    async def _memory_tick(self):
        if self.research.maintenance:
            return
        async with self.store.transaction() as tx:
            for item in await tx.list("proposal"):
                await enqueue_memory(tx, Proposal.model_validate(item))
        ready = (await self.capabilities())["brain"]
        await deliver_memory(self.store, self.brain, ready=ready)

    async def shutdown(self):
        self.stopping = True
        await self.scheduler.shutdown()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
