"""Version-specific approvals with immutable audit records and no CRM tool access."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from apps.orchestrator.sales.models import DecisionRequest, Proposal, ProposalEdit, timestamp
from apps.orchestrator.sales.store import SalesStore, SalesTransaction
from apps.orchestrator.storage.ports import Conflict


async def require_member(tx: SalesTransaction, actor: dict) -> str:
    """Recheck durable membership inside the same transaction as the mutation."""
    email = str(actor.get("email", "")).strip().lower()
    if not email or actor.get("workspace_id") != "toir":
        raise PermissionError("Sales workspace membership is required")
    member = await tx.get("member", email)
    if (
        not member
        or member.get("active") is not True
        or member.get("role") not in {"sales", "admin"}
    ):
        raise PermissionError("Active sales workspace membership is required")
    return email


async def revision(tx: SalesTransaction, proposal: Proposal) -> None:
    """Capture a content version once; subsequent execution changes are not new content."""
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


def excluded_operations(proposal: Proposal) -> set[str]:
    """Remove selected operations and every operation depending on removed work."""
    contacts = set(proposal.excluded_contact_ids)
    excluded = set(proposal.excluded_operation_ids)
    excluded.update(op.id for op in proposal.operations if op.contact_id in contacts)
    while True:
        expanded = excluded | {
            op.id for op in proposal.operations if excluded.intersection(op.depends_on)
        }
        if expanded == excluded:
            return excluded
        excluded = expanded


def validate_field_exclusions(proposal: Proposal, exclusions: dict[str, list[str]]) -> None:
    operations = {operation.id: operation for operation in proposal.operations}
    for operation_id, fields in exclusions.items():
        operation = operations.get(operation_id)
        if operation is None or set(fields) - set(operation.properties):
            raise ValueError("Unknown CRM operation or field exclusion")
        if not fields:
            continue
        if operation.kind == "association" or any(
            str(operation.properties[field]).startswith("op:") for field in fields
        ):
            raise ValueError(
                "Association references cannot be removed; remove the operation instead"
            )
        if operation.action != "create":
            continue
        remaining = {key: value for key, value in operation.properties.items() if key not in fields}
        if operation.kind == "company" and not all(
            remaining.get(key) for key in ("name", "domain")
        ):
            raise ValueError("Company creation requires its approved name and domain")
        if operation.kind == "contact" and not any(
            remaining.get(key) for key in ("firstname", "lastname")
        ):
            raise ValueError("Contact creation requires an approved first or last name")
        if operation.kind == "note" and not all(
            remaining.get(key) is not None for key in ("hs_note_body", "hs_timestamp")
        ):
            raise ValueError("Research notes require their body and timestamp")


def validate_operations(proposal: Proposal) -> None:
    ids = {op.id for op in proposal.operations}
    if len(ids) != len(proposal.operations):
        raise ValueError("CRM operation IDs must be unique")
    for operation in proposal.operations:
        if set(operation.depends_on) - ids:
            raise ValueError("CRM operation has an unknown dependency")
    # Cycles cannot execute and must never be approved.
    pending = {op.id: set(op.depends_on) for op in proposal.operations}
    while pending:
        ready = {key for key, dependencies in pending.items() if not dependencies}
        if not ready:
            raise ValueError("CRM operation dependencies contain a cycle")
        pending = {key: value - ready for key, value in pending.items() if key not in ready}


class ProposalService:
    def __init__(self, store: SalesStore):
        self.store = store

    @staticmethod
    async def _load(tx: SalesTransaction, proposal_id: str) -> Proposal:
        data = await tx.get("proposal", proposal_id)
        if data is None:
            raise LookupError("Proposal not found")
        return Proposal.model_validate(data)

    async def edit(self, proposal_id: str, edit: ProposalEdit, actor: dict) -> Proposal:
        async with self.store.transaction() as tx:
            await require_member(tx, actor)
            proposal = await self._load(tx, proposal_id)
            if proposal.version != edit.version:
                raise Conflict("The proposal changed. Refresh before editing.")
            if proposal.status != "pending":
                raise Conflict("Only pending proposals can be edited")
            if set(edit.excluded_contact_ids) - {contact.id for contact in proposal.contacts}:
                raise ValueError("Unknown contact exclusion")
            if set(edit.excluded_operation_ids) - {op.id for op in proposal.operations}:
                raise ValueError("Unknown operation exclusion")
            previous = proposal.model_copy(deep=True)
            for operation_id, fields in edit.excluded_fields.items():
                added = set(fields) - set(proposal.excluded_fields.get(operation_id, []))
                if added and any(
                    operation.id == operation_id and operation.status == "succeeded"
                    for operation in proposal.operations
                ):
                    raise Conflict("Completed CRM fields cannot be removed")
                proposal.excluded_fields[operation_id] = sorted(
                    set(proposal.excluded_fields.get(operation_id, [])) | set(fields)
                )
            validate_field_exclusions(proposal, proposal.excluded_fields)
            # These edits remove work only. Missing arrays must never restore excluded work.
            proposal.excluded_contact_ids = sorted(
                set(proposal.excluded_contact_ids) | set(edit.excluded_contact_ids),
            )
            proposal.excluded_operation_ids = sorted(
                set(proposal.excluded_operation_ids) | set(edit.excluded_operation_ids),
            )
            proposal.excluded_operation_ids = sorted(excluded_operations(proposal))
            newly_excluded = set(proposal.excluded_operation_ids) - set(
                previous.excluded_operation_ids,
            )
            if any(
                op.id in newly_excluded and op.status == "succeeded" and bool(op.properties)
                for op in proposal.operations
            ):
                raise Conflict("Completed CRM operations cannot be removed")
            if proposal.model_dump() == previous.model_dump():
                return proposal
            await revision(tx, previous)
            proposal.version += 1
            proposal.updated_at = timestamp()
            await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            return proposal

    async def decide(
        self,
        proposal_id: str,
        request: DecisionRequest,
        actor: dict,
        idempotency_key: str,
    ) -> Proposal:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 256:
            raise ValueError("A decision Idempotency-Key of 1–256 characters is required")
        decision_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        async with self.store.transaction() as tx:
            email = await require_member(tx, actor)
            expected = {
                "proposal_id": proposal_id,
                "version": request.version,
                "decision": request.decision,
                "actor": email,
            }
            existing = await tx.get("decision", decision_id)
            if existing:
                if any(existing.get(key) != value for key, value in expected.items()):
                    raise Conflict("This decision key was already used for a different request")
                return Proposal.model_validate(existing["proposal"])
            proposal = await self._load(tx, proposal_id)
            if proposal.version != request.version:
                raise Conflict("The proposal changed. Refresh before deciding.")
            if proposal.status != "pending":
                raise Conflict("This proposal already has an immutable decision")
            if request.decision == "approved":
                validate_operations(proposal)
                validate_field_exclusions(proposal, proposal.excluded_fields)
                excluded = excluded_operations(proposal)
                if not any(op.id not in excluded for op in proposal.operations):
                    raise ValueError("There are no CRM operations to approve")
                proposal.excluded_operation_ids = sorted(excluded)
            await revision(tx, proposal)
            proposal.status = request.decision
            proposal.execution = "queued" if request.decision == "approved" else "not_started"
            proposal.decided_by = email
            proposal.decided_at = timestamp()
            proposal.updated_at = proposal.decided_at
            proposal.error = None
            result = proposal.model_dump(mode="json")
            await tx.put("proposal", proposal.id, result)
            await tx.put(
                "decision",
                decision_id,
                {
                    "id": decision_id,
                    "workspace_id": "toir",
                    **expected,
                    "decided_by": email,
                    "decided_at": proposal.decided_at,
                    "idempotency_key": idempotency_key,
                    "proposal": result,
                },
            )
            if request.decision == "denied":
                await tx.put(
                    "suppression",
                    proposal.company.domain,
                    {
                        "workspace_id": "toir",
                        "domain": proposal.company.domain,
                        "reason": "denied",
                        "proposal_id": proposal.id,
                        "denied_at": proposal.decided_at,
                        "until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                    },
                )
            return proposal

    async def retry(self, proposal_id: str, actor: dict) -> Proposal:
        async with self.store.transaction() as tx:
            await require_member(tx, actor)
            proposal = await self._load(tx, proposal_id)
            if proposal.status != "approved":
                raise Conflict("Only approved CRM execution can be retried")
            if proposal.execution not in {"failed", "partial"}:
                raise Conflict("Only failed or partial execution can be retried")
            decisions = await tx.list("decision")
            if not any(
                decision.get("proposal_id") == proposal.id
                and decision.get("version") == proposal.version
                and decision.get("decision") == "approved"
                and decision.get("actor") == proposal.decided_by
                for decision in decisions
            ):
                raise Conflict("The proposal has no matching approval audit")
            excluded = excluded_operations(proposal)
            unfinished = [
                op
                for op in proposal.operations
                if op.id not in excluded and op.status != "succeeded"
            ]
            if not unfinished:
                raise Conflict("No unfinished CRM operations remain")
            if any(op.status == "running" for op in unfinished):
                raise Conflict("Running operations must be recovered before retry")
            for operation in unfinished:
                if operation.status == "failed":
                    operation.status = "pending"
                    operation.error = None
                # Uncertain creates retain their status: the executor must reconcile first.
            proposal.execution = "queued"
            proposal.error = None
            proposal.updated_at = timestamp()
            await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            return proposal
