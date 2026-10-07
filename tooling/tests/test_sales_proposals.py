"""Approval boundaries and retries are transactional on each supported backend."""

import asyncio
from datetime import UTC, datetime

import pytest

from apps.orchestrator.models.research import Citation
from apps.orchestrator.sales.models import (
    Company,
    Contact,
    CRMOperation,
    DecisionRequest,
    Proposal,
    ProposalEdit,
)
from apps.orchestrator.sales.proposals import ProposalService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.ports import Conflict
from tooling.tests import test_sales_storage as storage_tests

open_sales_repository = storage_tests.open_sales_repository

ACTOR = {"email": "curran@toirinc.com", "workspace_id": "toir", "role": "sales"}
JARED = {"email": "jared@toirinc.com", "workspace_id": "toir", "role": "sales"}


def example_proposal():
    citation = Citation(source_id="src_0123456789abcdef", quote="Alex is the CTO of Example.")
    return Proposal(
        id="proposal-1",
        session_id="session-1",
        job_id="job-1",
        requested_by=ACTOR["email"],
        company=Company(name="Example", domain="www.example.com", citations=[citation]),
        contacts=[
            Contact(
                id="alex",
                name="Alex",
                title="CTO",
                company_domain="example.com",
                buying_relevance="Owns engineering decisions",
                citations=[citation],
            )
        ],
        operations=[
            CRMOperation(
                id="company", kind="company", action="create", properties={"name": "Example"}
            ),
            CRMOperation(id="contact", kind="contact", action="create", contact_id="alex"),
            CRMOperation(
                id="association",
                kind="association",
                action="associate",
                contact_id="alex",
                depends_on=["company", "contact"],
            ),
            CRMOperation(
                id="note",
                kind="note",
                action="create",
                contact_id="alex",
                depends_on=["association"],
            ),
        ],
    )


async def seed(repo):
    store = SalesStore(repo)
    await store.setup()
    proposal = example_proposal()
    async with store.transaction() as tx:
        for actor in (ACTOR, JARED):
            await tx.put("member", actor["email"], {**actor, "active": True})
        await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
    return store, ProposalService(store), proposal


def test_edit_cascades_and_never_reincludes_removed_contact(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            edited = await service.edit(
                original.id, ProposalEdit(version=1, excluded_contact_ids=["alex"]), ACTOR
            )
            assert edited.version == 2
            assert edited.excluded_contact_ids == ["alex"]
            assert set(edited.excluded_operation_ids) == {"contact", "association", "note"}
            assert len(edited.contacts) == 1  # Original evidence is preserved.
            unchanged = await service.edit(original.id, ProposalEdit(version=2), ACTOR)
            assert unchanged == edited
            async with store.transaction() as tx:
                revision = await tx.get("proposal_revision", f"{original.id}:1")
                assert revision["proposal"] == original.model_dump(mode="json")
                assert await tx.list("decision") == []
            with pytest.raises(Conflict, match="changed"):
                await service.edit(original.id, ProposalEdit(version=1), ACTOR)
            with pytest.raises(ValueError, match="Unknown contact"):
                await service.edit(
                    original.id, ProposalEdit(version=2, excluded_contact_ids=["stranger"]), ACTOR
                )
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_removing_company_cascades_dependent_operations(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            _, service, original = await seed(repo)
            edited = await service.edit(
                original.id, ProposalEdit(version=1, excluded_operation_ids=["company"]), ACTOR
            )
            assert set(edited.excluded_operation_ids) == {"company", "association", "note"}
            approved = await service.decide(
                original.id,
                DecisionRequest(version=2, decision="approved"),
                ACTOR,
                "approve-contact-only",
            )
            assert approved.execution == "queued"
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_decision_idempotency_is_exact_and_audit_immutable(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            request = DecisionRequest(version=1, decision="approved")
            result = await service.decide(original.id, request, ACTOR, "unique-key")
            assert result.status == "approved"
            assert result.execution == "queued"
            assert result.decided_by == ACTOR["email"]
            assert all(op.status == "pending" for op in result.operations)
            assert await service.decide(original.id, request, ACTOR, "unique-key") == result
            for altered, actor in (
                (DecisionRequest(version=1, decision="denied"), ACTOR),
                (DecisionRequest(version=2, decision="approved"), ACTOR),
                (request, JARED),
            ):
                with pytest.raises(Conflict, match="different request"):
                    await service.decide(original.id, altered, actor, "unique-key")
            with pytest.raises(Conflict, match="immutable decision"):
                await service.decide(original.id, request, JARED, "another-key")
            with pytest.raises(Conflict, match="pending"):
                await service.edit(original.id, ProposalEdit(version=1), ACTOR)
            async with store.transaction() as tx:
                audits = await tx.list("decision")
                assert len(audits) == 1
                assert audits[0]["actor"] == ACTOR["email"]
                assert audits[0]["proposal_id"] == original.id
                assert audits[0]["version"] == 1
                with pytest.raises(Conflict, match="immutable"):
                    await tx.put("decision", audits[0]["id"], {**audits[0], "decision": "denied"})
                assert len(await tx.list("proposal_revision")) == 1
                assert await tx.list("crm_operation") == []  # No CRM calls performed by approval.
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_concurrent_approvals_have_one_winner_across_connections(tmp_path, open_sales_repository):
    async def scenario():
        first = await open_sales_repository(tmp_path / "runs.sqlite")
        second = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(first)
            second_service = ProposalService(SalesStore(second))
            request = DecisionRequest(version=1, decision="approved")
            results = await asyncio.gather(
                service.decide(original.id, request, ACTOR, "first-key"),
                second_service.decide(original.id, request, JARED, "second-key"),
                return_exceptions=True,
            )
            assert sum(isinstance(item, Proposal) for item in results) == 1
            assert sum(isinstance(item, Conflict) for item in results) == 1
            async with store.transaction() as tx:
                assert len(await tx.list("decision")) == 1
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_concurrent_same_key_replays_one_decision(tmp_path, open_sales_repository):
    async def scenario():
        first = await open_sales_repository(tmp_path / "runs.sqlite")
        second = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(first)
            request = DecisionRequest(version=1, decision="approved")
            results = await asyncio.gather(
                service.decide(original.id, request, ACTOR, "same-key"),
                ProposalService(SalesStore(second)).decide(original.id, request, ACTOR, "same-key"),
            )
            assert results[0] == results[1]
            async with store.transaction() as tx:
                assert len(await tx.list("decision")) == 1
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_deny_suppresses_company_and_cannot_retry(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            denied = await service.decide(
                original.id, DecisionRequest(version=1, decision="denied"), JARED, "deny"
            )
            assert denied.execution == "not_started"
            with pytest.raises(Conflict, match="approved"):
                await service.retry(original.id, ACTOR)
            async with store.transaction() as tx:
                suppression = await tx.get("suppression", "example.com")
                remaining = datetime.fromisoformat(suppression["until"]) - datetime.now(UTC)
                assert 29 <= remaining.days <= 30
                assert suppression["proposal_id"] == original.id
        finally:
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "actor,member",
    [
        ({"email": "outsider@example.com", "workspace_id": "toir"}, None),
        ({**ACTOR, "workspace_id": "other"}, None),
        (ACTOR, {**ACTOR, "active": False}),
        (ACTOR, {**ACTOR, "active": True, "role": "viewer"}),
    ],
)
def test_membership_checked_inside_all_mutations(tmp_path, open_sales_repository, actor, member):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            if member:
                async with store.transaction() as tx:
                    await tx.put("member", ACTOR["email"], member)
            for mutation in (
                service.edit(original.id, ProposalEdit(version=1), actor),
                service.decide(
                    original.id,
                    DecisionRequest(version=1, decision="approved"),
                    actor,
                    "unauthorized",
                ),
                service.retry(original.id, actor),
            ):
                with pytest.raises(PermissionError):
                    await mutation
            async with store.transaction() as tx:
                assert (await tx.get("proposal", original.id))["status"] == "pending"
                assert await tx.list("decision") == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_retry_preserves_success_and_uncertain_creates(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            with pytest.raises(Conflict, match="approved"):
                await service.retry(original.id, ACTOR)
            approved = await service.decide(
                original.id, DecisionRequest(version=1, decision="approved"), ACTOR, "approve"
            )
            approved.execution = "partial"
            approved.operations[0].status = "succeeded"
            approved.operations[0].result_id = "hubspot-company-100"
            approved.operations[1].status = "uncertain"
            approved.operations[1].error = "Timed out after dispatch"
            approved.operations[2].status = "failed"
            approved.operations[2].error = "Temporary failure"
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, approved.model_dump(mode="json"))
            result = await service.retry(original.id, JARED)
            assert result.execution == "queued"
            assert result.operations[0] == approved.operations[0]
            assert result.operations[1] == approved.operations[1]
            assert result.operations[2].status == "pending"
            assert result.operations[2].error is None
            assert result.decided_by == ACTOR["email"]
            with pytest.raises(Conflict, match="failed or partial"):
                await service.retry(original.id, ACTOR)
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_stale_approval_and_empty_plan_do_not_create_audit(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            with pytest.raises(Conflict, match="changed"):
                await service.decide(
                    original.id, DecisionRequest(version=2, decision="approved"), ACTOR, "stale"
                )
            await service.edit(
                original.id,
                ProposalEdit(version=1, excluded_operation_ids=["company", "contact"]),
                ACTOR,
            )
            with pytest.raises(ValueError, match="no CRM operations"):
                await service.decide(
                    original.id, DecisionRequest(version=2, decision="approved"), ACTOR, "empty"
                )
            async with store.transaction() as tx:
                assert await tx.list("decision") == []
                assert (await tx.get("proposal", original.id))["status"] == "pending"
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_field_exclusions_persist_without_changing_original_properties(
    tmp_path, open_sales_repository
):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            original.operations[0].properties = {
                "name": "Example",
                "domain": "example.com",
                "description": "AI research target",
            }
            original.operations[1].properties = {"firstname": "Alex", "jobtitle": "CTO"}
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, original.model_dump(mode="json"))
            edited = await service.edit(
                original.id,
                ProposalEdit(
                    version=1,
                    excluded_fields={"company": ["description"], "contact": ["jobtitle"]},
                ),
                ACTOR,
            )
            assert edited.version == 2
            assert edited.operations[0].properties == original.operations[0].properties
            assert edited.excluded_fields == {"company": ["description"], "contact": ["jobtitle"]}
            assert (await service.edit(original.id, ProposalEdit(version=2), ACTOR)) == edited
            approved = await service.decide(
                original.id, DecisionRequest(version=2, decision="approved"), ACTOR, "fields"
            )
            assert approved.excluded_fields == edited.excluded_fields
            async with store.transaction() as tx:
                decision = (await tx.list("decision"))[0]
                assert decision["proposal"]["excluded_fields"] == edited.excluded_fields
        finally:
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation_id,fields,error",
    [
        ("company", ["domain"], "requires its approved name and domain"),
        ("contact", ["firstname"], "requires an approved first or last name"),
        ("association", ["from_object_id"], "Association references"),
        ("note", ["hs_note_body"], "body and timestamp"),
        ("company", ["not_a_field"], "Unknown"),
        ("unknown-operation", ["name"], "Unknown"),
    ],
)
def test_field_exclusions_protect_identity_and_dependencies(
    tmp_path,
    open_sales_repository,
    operation_id,
    fields,
    error,
):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            original.operations[0].properties = {"name": "Example", "domain": "example.com"}
            original.operations[1].properties = {"firstname": "Alex", "jobtitle": "CTO"}
            original.operations[2].properties = {"from_object_id": "op:contact"}
            original.operations[3].properties = {"hs_note_body": "Evidence", "hs_timestamp": 100}
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, original.model_dump(mode="json"))
            with pytest.raises(ValueError, match=error):
                await service.edit(
                    original.id,
                    ProposalEdit(
                        version=1,
                        excluded_fields={operation_id: fields},
                    ),
                    ACTOR,
                )
            async with store.transaction() as tx:
                assert (await tx.get("proposal", original.id))["version"] == 1
                assert await tx.list("proposal_revision") == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_existing_no_change_contact_can_be_removed_from_pending_proposal(
    tmp_path,
    open_sales_repository,
):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            original.operations[1].action = "update"
            original.operations[1].record_id = "existing-contact"
            original.operations[1].result_id = "existing-contact"
            original.operations[1].status = "succeeded"
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, original.model_dump(mode="json"))
            edited = await service.edit(
                original.id, ProposalEdit(version=1, excluded_contact_ids=["alex"]), ACTOR
            )
            assert "contact" in edited.excluded_operation_ids
        finally:
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "dependency,error", [("company", "cycle"), ("missing", "unknown dependency")]
)
def test_invalid_operation_dependencies_cannot_be_approved(
    tmp_path,
    open_sales_repository,
    dependency,
    error,
):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            original.operations[0].depends_on = [dependency]
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, original.model_dump(mode="json"))
            with pytest.raises(ValueError, match=error):
                await service.decide(
                    original.id,
                    DecisionRequest(version=1, decision="approved"),
                    ACTOR,
                    "invalid-dependency",
                )
            async with store.transaction() as tx:
                assert await tx.list("decision") == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_refresh_after_partial_execution_keeps_approved_field_exclusions(
    tmp_path,
    open_sales_repository,
):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store, service, original = await seed(repo)
            original.operations[0].properties = {
                "name": "Example",
                "domain": "example.com",
                "description": "Do not write",
            }
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, original.model_dump(mode="json"))
            await service.edit(
                original.id,
                ProposalEdit(
                    version=1,
                    excluded_fields={"company": ["description"]},
                ),
                ACTOR,
            )
            old_approval = await service.decide(
                original.id, DecisionRequest(version=2, decision="approved"), ACTOR, "old-approval"
            )
            refreshed = old_approval.model_copy(deep=True)
            refreshed.version = 3
            refreshed.status = "pending"
            refreshed.execution = "needs_review"
            refreshed.decided_by = refreshed.decided_at = None
            refreshed.operations[0].status = "succeeded"
            refreshed.operations[0].result_id = "created-company"
            async with store.transaction() as tx:
                await tx.put("proposal", original.id, refreshed.model_dump(mode="json"))
            with pytest.raises(Conflict, match="Completed CRM fields"):
                await service.edit(
                    original.id,
                    ProposalEdit(
                        version=3,
                        excluded_fields={"company": ["name"]},
                    ),
                    ACTOR,
                )
            result = await service.decide(
                original.id, DecisionRequest(version=3, decision="approved"), JARED, "new-approval"
            )
            assert result.excluded_fields == {"company": ["description"]}
            assert result.operations[0].result_id == "created-company"
            replay = await service.decide(
                original.id, DecisionRequest(version=2, decision="approved"), ACTOR, "old-approval"
            )
            assert replay == old_approval
            async with store.transaction() as tx:
                assert len(await tx.list("decision")) == 2
                assert (await tx.get("proposal", original.id))["version"] == 3
        finally:
            await repo.close()

    asyncio.run(scenario())
