"""Execute persisted, version-specific approvals with a durable operation journal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from apps.orchestrator.sales.crm import CRMError, ScalekitCRM, canonical_domain, same_value
from apps.orchestrator.sales.crm_planner import linkedin_key
from apps.orchestrator.sales.models import CRMOperation, Proposal, timestamp
from apps.orchestrator.sales.proposals import require_member


class ApprovalRequired(CRMError):
    pass


def selected_operations(proposal: Proposal) -> list[CRMOperation]:
    excluded = set(proposal.excluded_operation_ids)
    excluded.update(
        op.id for op in proposal.operations if op.contact_id in proposal.excluded_contact_ids
    )
    while True:
        blocked = {op.id for op in proposal.operations if set(op.depends_on) & excluded}
        if blocked <= excluded:
            break
        excluded |= blocked
    return [op for op in proposal.operations if op.id not in excluded]


def approved_content(proposal: Proposal) -> dict:
    """Runtime status cannot widen an approved target, field set, value, or dependency."""
    data = proposal.model_dump(mode="json")
    return {
        "company": data["company"],
        "contacts": data["contacts"],
        "excluded_contact_ids": data["excluded_contact_ids"],
        "excluded_operation_ids": data["excluded_operation_ids"],
        "excluded_fields": data["excluded_fields"],
        "operations": [
            {
                k: v
                for k, v in op.items()
                if k
                not in {
                    "status",
                    "result_id",
                    "error",
                }
            }
            for op in data["operations"]
        ],
    }


class CRMExecutor:
    def __init__(
        self,
        store,
        crm: ScalekitCRM,
        *,
        can_execute: Callable[[], bool] | None = None,
    ):
        self.store, self.crm = store, crm
        self._can_execute = can_execute or (lambda: True)
        self._lock = asyncio.Lock()

    async def _save(self, proposal: Proposal, operation: CRMOperation | None = None):
        proposal.updated_at = timestamp()
        async with self.store.transaction() as tx:
            current = await tx.get("proposal", proposal.id)
            if current and current.get("version") == proposal.version:
                proposal.memory_status = current.get("memory_status", proposal.memory_status)
            await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            if operation:
                await tx.put(
                    "crm_operation",
                    operation.id,
                    {
                        "workspace_id": proposal.workspace_id,
                        "proposal_id": proposal.id,
                        "version": proposal.version,
                        "updated_at": timestamp(),
                        **operation.model_dump(mode="json"),
                    },
                )
                if operation.status == "succeeded" and operation.kind == "contact":
                    contact = next(
                        (c for c in proposal.contacts if c.id == operation.contact_id), None
                    )
                    if contact and contact.linkedin_url and operation.result_id:
                        key = linkedin_key(contact.linkedin_url)
                        await tx.put(
                            "identity",
                            key,
                            {
                                "workspace_id": proposal.workspace_id,
                                "id": key,
                                "linkedin_url": contact.linkedin_url,
                                "crm_record_id": operation.result_id,
                                "proposal_id": proposal.id,
                            },
                        )

    async def _claim(self, proposal_id: str) -> Proposal:
        async with self.store.transaction() as tx:
            raw = await tx.get("proposal", proposal_id)
            if raw is None:
                raise ApprovalRequired("CRM proposal does not exist.", code="approval")
            proposal = Proposal.model_validate(raw)
            if proposal.status != "approved" or not proposal.decided_by:
                raise ApprovalRequired("CRM changes require an approved proposal.", code="approval")
            try:
                await require_member(
                    tx,
                    {
                        "workspace_id": proposal.workspace_id,
                        "email": proposal.decided_by,
                    },
                )
            except PermissionError:
                raise ApprovalRequired(
                    "The approving user no longer has sales workspace access.",
                    code="approval",
                ) from None
            decisions = await tx.list("decision")
            matches = [
                decision
                for decision in decisions
                if (
                    decision.get("proposal_id") == proposal.id
                    and decision.get("version") == proposal.version
                    and decision.get("decision") == "approved"
                    and (decision.get("actor") or decision.get("decided_by")) == proposal.decided_by
                )
            ]
            if len(matches) != 1:
                raise ApprovalRequired(
                    "The exact proposal version has no approval audit.", code="approval"
                )
            snapshot = matches[0].get("proposal")
            if not snapshot or approved_content(
                Proposal.model_validate(snapshot)
            ) != approved_content(proposal):
                raise ApprovalRequired(
                    "Proposal content differs from its approval.", code="approval"
                )
            if proposal.execution in {"running", "succeeded"}:
                return proposal
            if proposal.execution != "queued":
                raise ApprovalRequired(
                    "Queue an approved retry before CRM execution.", code="approval"
                )
            # Maintenance can change while the caller waits for this transaction.
            # Drain reads use this same store lock, so queued work must remain unclaimed.
            if not self._can_execute():
                return proposal
            proposal.execution, proposal.error = "running", None
            proposal.updated_at = timestamp()
            await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            # Private marker; a second caller observing 'running' cannot execute the claimed work.
            self._claimed_id = proposal.id
            return proposal

    async def execute(self, proposal_id: str) -> Proposal:
        async with self._lock:
            self._claimed_id = None
            proposal = await self._claim(proposal_id)
            if self._claimed_id != proposal.id:
                return proposal
            operations = selected_operations(proposal)
            try:
                self._validate_graph(operations)
                # Reconcile uncertain creates before deciding whether unfinished work may continue.
                for operation in operations:
                    if operation.status in {"uncertain", "running"}:
                        await self._reconcile(proposal, operation)
                refreshed = await self._preflight(proposal)
                if refreshed is not None:
                    return refreshed
                for operation in operations:
                    if operation.status == "succeeded":
                        continue
                    if operation.status == "uncertain":
                        raise CRMError("An uncertain CRM create requires manual reconciliation.")
                    properties = self._resolve(operation, operations, proposal)
                    if not properties and operation.action == "update":
                        operation.status, operation.result_id = "succeeded", operation.record_id
                        await self._save(proposal, operation)
                        continue
                    operation.status, operation.error = "running", None
                    await self._save(proposal, operation)
                    try:
                        result_id = await self.crm.write(
                            operation.kind,
                            operation.action,
                            properties,
                            operation.record_id,
                        )
                    except CRMError as error:
                        operation.status = "uncertain" if error.uncertain else "failed"
                        operation.error = str(error)
                        await self._save(proposal, operation)
                        raise
                    except Exception:
                        # A provider/decoder bug may happen after a successful remote create.
                        # Do not leak arbitrary exception text or infer that nothing was written.
                        operation.status = "uncertain"
                        operation.error = (
                            "CRM returned an unexpected result; reconciliation required."
                        )
                        await self._save(proposal, operation)
                        raise CRMError(operation.error, uncertain=True) from None
                    operation.status, operation.result_id = "succeeded", result_id
                    await self._save(proposal, operation)
                proposal.execution = "succeeded"
                proposal.error = None
                await self._save(proposal)
            except Exception as error:
                # Cancellation is BaseException and deliberately preserves restart recovery.
                for operation in operations:
                    if operation.status == "running":
                        operation.status = "uncertain"
                        operation.error = "CRM execution stopped without a confirmed result."
                        await self._save(proposal, operation)
                proposal.execution = (
                    "partial"
                    if any(
                        op.status == "succeeded" and (op.properties or op.action == "create")
                        for op in operations
                    )
                    else "failed"
                )
                proposal.error = (
                    str(error)
                    if isinstance(error, CRMError)
                    else ("CRM execution could not complete. Check the operation journal.")
                )
                await self._save(proposal)
            return proposal

    @staticmethod
    def _validate_graph(operations):
        seen = set()
        for operation in operations:
            if operation.id in seen or not set(operation.depends_on) <= seen:
                raise CRMError("The approved CRM dependency graph is invalid.")
            for value in operation.properties.values():
                if isinstance(value, str) and value.startswith("op:"):
                    if value[3:] not in operation.depends_on:
                        raise CRMError("An association references an unapproved dependency.")
            seen.add(operation.id)

    @staticmethod
    def _resolve(operation, operations, proposal):
        by_id = {op.id: op for op in operations}
        for dependency in operation.depends_on:
            if by_id[dependency].status != "succeeded":
                raise CRMError("An approved dependency has not completed.")
        properties = {
            k: v
            for k, v in operation.properties.items()
            if k not in proposal.excluded_fields.get(operation.id, [])
        }
        # Only association identifier fields can be references. Research text stays literal.
        if operation.kind == "association":
            for key in ("from_object_id", "to_object_id"):
                value = properties.get(key, "")
                if not isinstance(value, str) or not value.startswith("op:"):
                    raise CRMError("An association is missing an approved record reference.")
                target = by_id.get(value[3:])
                if target is None or not target.result_id:
                    raise CRMError("An association target has no confirmed CRM ID.")
                properties[key] = target.result_id
        return properties

    async def _find_create(self, proposal, operation):
        if operation.kind == "company":
            return await self.crm.find_company(proposal.company.domain)
        if operation.kind == "contact":
            contact = next(c for c in proposal.contacts if c.id == operation.contact_id)
            company = next(op for op in proposal.operations if op.kind == "company")
            return await self.crm.find_contact(
                contact.name,
                operation.properties.get("email"),
                company.result_id or company.record_id,
            )
        return None

    async def _reconcile(self, proposal, operation):
        if operation.action in {"update", "associate"}:
            # Safe repeats still pass normal before-value revalidation.
            operation.status = "pending"
            await self._save(proposal, operation)
            return
        match = await self._find_create(proposal, operation)
        if match and all(
            same_value(match["properties"].get(key), value)
            for key, value in operation.properties.items()
            if key not in proposal.excluded_fields.get(operation.id, [])
        ):
            operation.status, operation.result_id, operation.error = (
                "succeeded",
                str(match["id"]),
                None,
            )
            await self._save(proposal, operation)
            return
        operation.status = "uncertain"
        operation.error = "Previous create has no exact confirmed match; do not repeat it."
        await self._save(proposal, operation)
        raise CRMError(operation.error)

    async def _preflight(self, original: Proposal) -> Proposal | None:
        # Failed reads must not persist half of an unapproved content revision.
        proposal = original.model_copy(deep=True)
        operations = selected_operations(proposal)
        stale = False
        for operation in operations:
            fields = {
                k: v
                for k, v in operation.properties.items()
                if k not in proposal.excluded_fields.get(operation.id, [])
            }
            if operation.kind not in {"company", "contact"}:
                continue
            if operation.status == "succeeded":
                # A zero-change anchor was observed during planning, before approval.
                if (
                    operation.action == "update"
                    and not operation.properties
                    and operation.record_id
                ):
                    row = await self.crm.get_record(operation.kind, operation.record_id)
                    if (
                        operation.kind == "company"
                        and canonical_domain(row["properties"].get("domain") or "")
                        != proposal.company.domain
                    ):
                        raise CRMError("The approved company identity changed; research it again.")
                continue
            if operation.action == "create":
                match = await self._find_create(proposal, operation)
                if match:
                    # A competing CRM write changed create into update. That needs a fresh decision.
                    operation.action, operation.record_id = "update", str(match["id"])
                    operation.before = {k: match["properties"].get(k) for k in fields}
                    stale = True
            else:
                row = await self.crm.get_record(operation.kind, operation.record_id)
                if (
                    operation.kind == "company"
                    and canonical_domain(row["properties"].get("domain") or "")
                    != proposal.company.domain
                ):
                    raise CRMError("The approved company identity changed; research it again.")
                current = {key: row["properties"].get(key) for key in fields}
                if any(not same_value(current[key], operation.before.get(key)) for key in current):
                    operation.before = current
                    stale = True
        if stale:
            async with self.store.transaction() as tx:
                # Old immutable decision/revision stays intact for the audit trail.
                proposal.version += 1
                proposal.status, proposal.execution = "pending", "needs_review"
                proposal.decided_by = proposal.decided_at = None
                proposal.error = "HubSpot changed after review. Approve the refreshed proposal."
                proposal.updated_at = timestamp()
                await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
                revision_id = f"{proposal.id}:{proposal.version}"
                if await tx.get("proposal_revision", revision_id) is None:
                    await tx.put(
                        "proposal_revision",
                        revision_id,
                        {
                            "workspace_id": proposal.workspace_id,
                            "proposal_id": proposal.id,
                            "version": proposal.version,
                            "proposal": proposal.model_dump(mode="json"),
                        },
                    )
            return proposal
        return None

    async def recover(self):
        """Recover once on startup; lost write responses never trigger automatic replay."""
        async with self.store.transaction() as tx:
            for raw in await tx.list("proposal"):
                proposal = Proposal.model_validate(raw)
                if proposal.execution != "running":
                    continue
                for operation in proposal.operations:
                    if operation.status == "running":
                        operation.status = "uncertain" if operation.action == "create" else "failed"
                        operation.error = (
                            "Coordinator restarted before the result was durably recorded."
                        )
                        await tx.put(
                            "crm_operation",
                            operation.id,
                            {
                                "workspace_id": proposal.workspace_id,
                                "proposal_id": proposal.id,
                                "version": proposal.version,
                                **operation.model_dump(mode="json"),
                            },
                        )
                proposal.execution = (
                    "partial"
                    if any(op.status == "succeeded" for op in selected_operations(proposal))
                    else "failed"
                )
                proposal.error = (
                    "Execution was interrupted. Review the operation journal before retrying."
                )
                proposal.updated_at = timestamp()
                await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
