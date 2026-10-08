"""Two durable worker slots, with explicit recovery and bounded background dispatch.

Only persisted running jobs reserve a slot. Proposal approval is a separate concern.
All network/model calls happen outside a store transaction. Research v1's existing
one-active-run invariant remains authoritative for its worker slot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from apps.orchestrator.agents.research import AgentFailure
from apps.orchestrator.coordinator import NotConfigured
from apps.orchestrator.models.research import Brief, Run, Source
from apps.orchestrator.sales.contact_client import ContactStatus
from apps.orchestrator.sales.models import (
    Automation,
    Company,
    ContactReport,
    ContactTask,
    Job,
    Message,
    Session,
    uid,
)
from apps.orchestrator.storage.ports import Conflict

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "needs_input"}
CONTACT_KINDS = {"resolve", "enrich"}


def moment(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def combine_sources(*groups: list[Source]) -> list[Source]:
    sources = {source.id: source for group in groups for source in group}
    return list(sources.values())[:100]


class SalesScheduler:
    def __init__(
        self,
        store,
        research_coordinator,
        contacts,
        *,
        capabilities: Callable[[], Awaitable[dict]],
        finish_contact: Callable[[Job, ContactReport], Awaitable[None]],
        review_report: Callable[[Job, ContactReport], Awaitable[ContactReport]] | None = None,
        finished_discovery: Callable[[Job, Run], Awaitable[None]] | None = None,
        planning_context: Callable[[Job], Awaitable[dict]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.research = research_coordinator
        self.contacts = contacts
        self.capabilities = capabilities
        self.finish_contact = finish_contact
        self.review_report = review_report
        self.finished_discovery = finished_discovery
        self.planning_context = planning_context
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tick_lock = asyncio.Lock()
        self.stopping = False

    def now(self) -> datetime:
        return self.clock().astimezone(UTC)

    def day(self) -> str:
        return self.now().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()

    async def _jobs(self) -> list[Job]:
        async with self.store.transaction() as tx:
            return [Job.model_validate(value) for value in await tx.list("job")]

    async def _automation(self) -> Automation:
        async with self.store.transaction() as tx:
            value = await tx.get("automation", "default")
            return Automation.model_validate(value) if value else Automation()

    async def _save(self, job: Job) -> bool:
        """A user cancellation always wins over an in-flight poll or review."""
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if current is None or current["status"] == "cancelled":
                return False
            # Memory delivery has its own loop; never overwrite a newer acknowledgment.
            job.memory_status = current.get("memory_status", job.memory_status)
            job.updated_at = self.now().isoformat()
            await tx.put("job", job.id, job.model_dump(mode="json"))
            return True

    async def _still_running(self, job: Job) -> bool:
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            return current is not None and current["status"] == "running"

    async def _message(self, tx, job: Job, content: str, task_ids: list[str] | None = None):
        message_id = str(uuid5(NAMESPACE_URL, f"toir/job/{job.id}/{job.status}"))
        if await tx.get("message", message_id):
            return
        message = Message(
            id=message_id,
            session_id=job.session_id,
            role="assistant",
            content=content[:12000],
            job_id=job.id,
            task_ids=task_ids or [],
        )
        await tx.put("message", message.id, message.model_dump(mode="json"))

    async def _terminal(self, job: Job, status: str, message: str):
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if not current or current["status"] == "cancelled":
                return
            job.memory_status = current.get("memory_status", job.memory_status)
            job.status = status
            job.error = message if status in {"failed", "interrupted"} else None
            job.progress = message
            job.updated_at = self.now().isoformat()
            await tx.put("job", job.id, job.model_dump(mode="json"))
            await self._message(tx, job, message)

    async def tick(self):
        """Reconcile existing work, then dispatch at most one task in each free slot."""
        async with self.tick_lock:
            if self.stopping:
                return
            running = [job for job in await self._jobs() if job.status == "running"]
            await asyncio.gather(*(self._poll(job) for job in running))
            if self.stopping or self.research.maintenance:
                return
            capabilities = await self.capabilities()
            automation = await self._automation()
            background_ready = automation.enabled and all(
                capabilities.get(key) is True
                for key in ("ready", "postgres", "research", "contacts", "crm", "brain", "auth")
            )
            if background_ready:
                await self._queue_discovery(automation)
            for kind, capability in (("discovery", "research"), ("contacts", "contacts")):
                if capabilities.get(capability) is not True:
                    continue
                if kind == "discovery" and await self.research.repository.active():
                    continue
                job = await self._claim(kind, automation, background_ready)
                if not job:
                    continue
                if kind == "discovery":
                    await self._dispatch_discovery(job)
                else:
                    await self._dispatch_contacts(job)

    async def run(self, interval: float = 2):
        while not self.stopping:
            await self.tick()
            await asyncio.sleep(interval)

    async def shutdown(self):
        # No worker cancellation. The persisted IDs/deadlines allow startup recovery.
        self.stopping = True

    async def _queue_discovery(self, automation: Automation):
        async with self.store.transaction() as tx:
            jobs = [Job.model_validate(value) for value in await tx.list("job")]
            if any(
                job.kind == "discovery"
                and job.origin == "background"
                and job.status in {"queued", "running"}
                for job in jobs
            ):
                return
            if self._usage(jobs, "discovery") >= automation.daily_discoveries:
                return
            session_id = str(uuid5(NAMESPACE_URL, "toir/continuous-prospecting"))
            if not await tx.get("session", session_id):
                session = Session(
                    id=session_id,
                    title="Continuous prospecting",
                    owner=automation.owner,
                    automation=True,
                )
                await tx.put("session", session.id, session.model_dump(mode="json"))
            job = Job(
                session_id=session_id,
                requested_by=automation.owner,
                kind="discovery",
                origin="background",
                query=automation.request,
                propose_crm=True,
            )
            await tx.put("job", job.id, job.model_dump(mode="json"))

    def _usage(self, jobs: list[Job], kind: str) -> int:
        return sum(
            job.origin == "background"
            and job.budget_day == self.day()
            and (job.kind == "discovery" if kind == "discovery" else job.kind in CONTACT_KINDS)
            for job in jobs
        )

    async def _claim(self, slot: str, automation: Automation, background_ready: bool):
        async with self.store.transaction() as tx:
            # A drain/shutdown can begin while this tick waits for the store lock.
            if self.stopping or self.research.maintenance:
                return None
            jobs = [Job.model_validate(value) for value in await tx.list("job")]
            kinds = {"discovery"} if slot == "discovery" else CONTACT_KINDS
            if any(job.status == "running" and job.kind in kinds for job in jobs):
                return None
            queue = sorted(
                (job for job in jobs if job.status == "queued" and job.kind in kinds),
                key=lambda job: (job.origin != "chat", job.created_at, job.id),
            )
            for job in queue:
                if job.origin == "background":
                    limit = (
                        automation.daily_discoveries
                        if slot == "discovery"
                        else automation.daily_enrichments
                    )
                    if not background_ready or self._usage(jobs, slot) >= limit:
                        continue
                discovery_child = any(
                    parent.id == job.parent_id and parent.kind == "discovery" for parent in jobs
                )
                if (
                    job.company
                    and (job.origin == "background" or discovery_child)
                    and await self._suppressed(tx, job.company.domain, jobs)
                ):
                    # Denial or a proposal can arrive after discovery enqueues this child.
                    # Explicit retries parent an enrichment job and retain their bypass.
                    pending = [
                        proposal["id"]
                        for proposal in await tx.list("proposal")
                        if proposal["company"]["domain"] == job.company.domain
                        and proposal["status"] == "pending"
                    ]
                    job.status = "completed"
                    job.progress = (
                        "Skipped: company recently researched, denied, or awaiting review"
                    )
                    job.updated_at = self.now().isoformat()
                    await tx.put("job", job.id, job.model_dump(mode="json"))
                    await self._message(tx, job, job.progress, pending)
                    continue
                if job.deadline_at and moment(job.deadline_at) <= self.now():
                    job.status = "failed"
                    job.error = job.progress = "Research reached its ten-minute time limit"
                    await tx.put("job", job.id, job.model_dump(mode="json"))
                    await self._message(tx, job, job.progress)
                    continue
                job.status = "running"
                job.deadline_at = (
                    job.deadline_at or (self.now() + timedelta(minutes=10)).isoformat()
                )
                job.budget_day = self.day() if job.origin == "background" else None
                job.updated_at = self.now().isoformat()
                job.progress = (
                    "Dispatching company research"
                    if slot == "discovery"
                    else "Dispatching contact research"
                )
                if slot == "discovery":
                    job.research_brief = job.research_brief or Brief(
                        request=job.query,
                        geography=automation.geography,
                        employee_min=automation.employee_min,
                        employee_max=automation.employee_max,
                        target_count=job.target_count,
                    )
                else:
                    job.task_id = uid()
                await tx.put("job", job.id, job.model_dump(mode="json"))
                return job
            return None

    async def _suppressed(self, tx, domain: str, jobs: list[Job], *, exclude_id=None) -> bool:
        suppression = await tx.get("suppression", domain)
        if suppression and moment(suppression["until"]) > self.now():
            return True
        for proposal in await tx.list("proposal"):
            if proposal["company"]["domain"] == domain and proposal["status"] == "pending":
                return True
        return any(
            job.id != exclude_id
            and job.company
            and job.company.domain == domain
            and job.status == "completed"
            and job.report is not None
            and moment(job.updated_at) > self.now() - timedelta(days=7)
            for job in jobs
        )

    async def _dispatch_discovery(self, job: Job):
        if not await self._still_running(job):
            return
        try:
            context = await self.planning_context(job) if self.planning_context else None
            if not await self._still_running(job):
                return
            options = {"planning_context": context} if context is not None else {}
            run = await self.research.create(
                job.research_brief, f"sales-discovery:{job.id}", **options,
            )
        except (Conflict, NotConfigured):
            # Admission failed before paid work: leave it queued until the slot/config returns.
            job.status, job.progress = "queued", "Waiting for company research availability"
            job.deadline_at, job.budget_day = None, None
            await self._save(job)
            return
        job.research_run_id = run.id
        job.deadline_at = run.deadline_at
        if not await self._save(job):
            # Cancellation may arrive between admission and the persisted run link.
            await self.research.cancel(run)

    async def _dispatch_contacts(self, job: Job):
        if not await self._still_running(job):
            return
        task = ContactTask(
            task_id=job.task_id,
            run_id=job.id,
            mode=job.kind,
            query=job.query,
            company=job.company,
            deadline_at=job.deadline_at,
            prior_sources=job.sources,
            usage=job.usage,
        )
        try:
            await self.contacts.submit(task)
        except AgentFailure:
            # Submit might have succeeded. Never repeat it blindly; poll the persisted ID.
            job.progress = "Checking whether the contact worker accepted this task"
            await self._save(job)
        if not await self._still_running(job):
            # Cancellation can race the HTTP submit before the worker knows this ID.
            try:
                await self.contacts.cancel(job.task_id)
            except AgentFailure:
                pass

    async def _poll(self, job: Job):
        try:
            if job.kind == "discovery":
                await self._poll_discovery(job)
            elif job.kind in CONTACT_KINDS:
                await self._poll_contacts(job)
        except AgentFailure:
            # A transient unreachable worker is different from a confirmed missing session.
            job.progress = "Waiting to reconnect to the existing worker task"
            await self._save(job)
        except Exception:
            await self._terminal(
                job, "failed", "Research failed validation; saved evidence is available for retry"
            )

    async def _poll_discovery(self, job: Job):
        if not job.research_run_id:
            # Recover the create-to-link crash window using the repository's idempotency key.
            run = (
                await self.research.repository.replay(
                    job.research_brief, f"sales-discovery:{job.id}"
                )
                if job.research_brief
                else None
            )
            if not run:
                await self._terminal(
                    job,
                    "interrupted",
                    "Company research dispatch was interrupted; retry explicitly",
                )
                return
            job.research_run_id, job.deadline_at = run.id, run.deadline_at
            if not await self._save(job):
                return
        run = await self.research.repository.get(job.research_run_id)
        if not run:
            await self._terminal(
                job,
                "interrupted",
                "The saved company research run is unavailable; retry explicitly",
            )
            return
        job.sources = combine_sources(job.sources, run.report.sources)
        job.usage = run.usage
        job.progress = run.stage
        if run.status == "completed":
            if self.finished_discovery:
                await self.finished_discovery(job, run)
            await self._enqueue_leads(job, run)
        elif run.status in TERMINAL:
            await self._terminal(job, run.status, run.error or f"Company research {run.status}")
        else:
            await self._save(job)

    async def _enqueue_leads(self, job: Job, run: Run):
        automation = await self._automation()
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if not current or current["status"] != "running":
                return
            job.memory_status = current.get("memory_status", job.memory_status)
            if not job.enrich_contacts:
                job.status = "completed"
                job.progress = "Company research complete; contact research was not requested"
                job.updated_at = self.now().isoformat()
                await tx.put("job", job.id, job.model_dump(mode="json"))
                await self._message(tx, job, job.progress)
                return
            jobs = [Job.model_validate(value) for value in await tx.list("job")]
            queued = 0
            task_ids = []
            proposals = await tx.list("proposal")
            for lead in run.report.leads:
                if lead.fit_score < automation.fit_threshold:
                    continue
                company = Company(
                    name=lead.company,
                    domain=lead.domain,
                    country=lead.country,
                    employee_count=lead.employee_count,
                    citations=lead.identity_citations,
                    description=lead.rationale,
                    fit_score=lead.fit_score,
                    sales_angle=lead.outreach_angle,
                )
                pending = next(
                    (
                        p
                        for p in proposals
                        if p["company"]["domain"] == company.domain and p["status"] == "pending"
                    ),
                    None,
                )
                if pending:
                    task_ids.append(pending["id"])
                    continue
                # Discovery is broad prospecting even when requested in chat. Only
                # explicitly named-company resolve/enrich jobs bypass suppression.
                if await self._suppressed(tx, company.domain, jobs):
                    continue
                if any(
                    j.company
                    and j.company.domain == company.domain
                    and j.status in {"queued", "running"}
                    for j in jobs
                ):
                    continue
                child = Job(
                    id=str(uuid5(NAMESPACE_URL, f"toir/enrich/{job.id}/{company.domain}")),
                    session_id=job.session_id,
                    requested_by=job.requested_by,
                    kind="enrich",
                    origin=job.origin,
                    query=f"Find decision-makers at {company.name} ({company.domain})",
                    propose_crm=job.propose_crm,
                    company=company,
                    sources=job.sources,
                    parent_id=job.id,
                )
                if not await tx.get("job", child.id):
                    await tx.put("job", child.id, child.model_dump(mode="json"))
                    jobs.append(child)
                    queued += 1
            job.status, job.progress = (
                "completed",
                f"Company research complete; {queued} companies queued for contact research",
            )
            job.updated_at = self.now().isoformat()
            await tx.put("job", job.id, job.model_dump(mode="json"))
            await self._message(tx, job, job.progress, task_ids)

    async def _poll_contacts(self, job: Job):
        if job.report is not None:
            await self._finish_contacts(job)
            return
        if not job.task_id:
            await self._terminal(
                job, "interrupted", "Contact research has no saved worker session; retry explicitly"
            )
            return
        if job.deadline_at and moment(job.deadline_at) <= self.now():
            try:
                await self.contacts.cancel(job.task_id)
            except AgentFailure:
                pass
            await self._terminal(
                job,
                "failed",
                "Contact research reached its ten-minute time limit; saved evidence is available",
            )
            return
        value = await self.contacts.get(job.task_id)
        if value is None:
            await self._terminal(
                job,
                "interrupted",
                "Contact worker session was lost; retry explicitly using saved evidence",
            )
            return
        status = ContactStatus.model_validate(value)
        if status.task_id != job.task_id or status.run_id != job.id:
            raise ValueError("Worker returned a different task")
        job.sources = combine_sources(job.sources, status.sources)
        job.usage = status.usage
        job.progress = status.progress
        if status.status == "completed":
            if status.report is None:
                raise ValueError("Completed worker task has no report")
            report = status.report.model_copy(
                update={"sources": combine_sources(job.sources, status.report.sources)}
            )
            if self.review_report:
                remaining = (moment(job.deadline_at) - self.now()).total_seconds()
                async with asyncio.timeout(max(0, remaining)):
                    report = await self.review_report(job, report)
            if job.kind == "resolve":
                await self._resolved(job, report)
            else:
                job.report = report
                job.progress = "Saving verified contact research"
                if await self._save(job):
                    await self._finish_contacts(job)
        elif status.status in {"failed", "cancelled"}:
            await self._terminal(
                job, status.status, f"Contact research {status.status}; saved evidence is available"
            )
        else:
            await self._save(job)

    async def _resolved(self, job: Job, report: ContactReport):
        job.sources = report.sources
        if report.company is None:
            job.candidates = report.candidates
            choices = "; ".join(f"{c.name} ({c.domain})" for c in report.candidates)
            message = (
                f"Choose the company by replying with its domain: {choices}"
                if choices
                else "I could not verify this company. Reply with its website or a specific name."
            )
            await self._terminal(job, "needs_input", message)
            return
        job.company = report.company
        if not job.enrich_contacts:
            job.report = report
            if await self._save(job):
                await self._finish_contacts(job)
            return
        job.kind, job.status, job.task_id = "enrich", "queued", None
        job.progress = "Company verified; queued for decision-maker research"
        async with self.store.transaction() as tx:
            current = await tx.get("job", job.id)
            if not current or current["status"] == "cancelled":
                return
            pending = next(
                (
                    p
                    for p in await tx.list("proposal")
                    if p["company"]["domain"] == job.company.domain and p["status"] == "pending"
                ),
                None,
            )
            if pending and job.propose_crm:
                job.status, job.progress = (
                    "completed",
                    "This company already has a CRM proposal awaiting approval",
                )
                await self._message(tx, job, job.progress, [pending["id"]])
            job.updated_at = self.now().isoformat()
            await tx.put("job", job.id, job.model_dump(mode="json"))

    async def _finish_contacts(self, job: Job):
        if not await self._still_running(job):
            return
        await self.finish_contact(job, job.report)
        job.status = "completed"
        job.progress = (
            "Contact research completed" if job.enrich_contacts else "Company research completed"
        )
        await self._save(job)

    async def cancel(self, job_id: str) -> Job:
        async with self.store.transaction() as tx:
            value = await tx.get("job", job_id)
            if not value:
                raise KeyError(job_id)
            job = Job.model_validate(value)
            if job.status in TERMINAL - {"needs_input"}:
                return job
            job.status, job.progress = "cancelled", "Research cancelled by the user"
            job.updated_at = self.now().isoformat()
            await tx.put("job", job.id, job.model_dump(mode="json"))
            await self._message(tx, job, job.progress)
        if job.kind == "discovery":
            run = (
                await self.research.repository.get(job.research_run_id)
                if job.research_run_id
                else None
            )
            if run:
                await self.research.cancel(run)
        elif job.task_id:
            try:
                await self.contacts.cancel(job.task_id)
            except AgentFailure:
                pass
        return job

    async def retry(self, job_id: str) -> Job:
        async with self.store.transaction() as tx:
            value = await tx.get("job", job_id)
            if not value:
                raise KeyError(job_id)
            old = Job.model_validate(value)
            if old.status not in {"failed", "interrupted", "cancelled"}:
                raise Conflict("Only interrupted, failed or cancelled research can be retried")
            for value in await tx.list("job"):
                existing = Job.model_validate(value)
                if existing.parent_id == old.id and existing.status in {"queued", "running"}:
                    return existing
            # An explicit retry is a fresh user attempt; preserve evidence, never a lost ID.
            job = Job(
                session_id=old.session_id,
                requested_by=old.requested_by,
                kind=old.kind,
                origin="chat",
                query=old.query,
                propose_crm=old.propose_crm,
                target_count=old.target_count,
                enrich_contacts=old.enrich_contacts,
                company=old.company,
                sources=old.sources,
                parent_id=old.id,
                research_brief=old.research_brief,
            )
            await tx.put("job", job.id, job.model_dump(mode="json"))
            return job
