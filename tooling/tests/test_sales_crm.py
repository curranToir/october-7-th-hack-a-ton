"""CRM provider shape and approval/recovery guarantees. No live CRM writes."""

import asyncio
import copy
import json
from datetime import UTC, datetime

import httpx
import pytest

from apps.orchestrator.models.research import Citation, Source
from apps.orchestrator.sales.crm import AmbiguousMatch, CRMError, ScalekitCRM
from apps.orchestrator.sales.crm_executor import ApprovalRequired, CRMExecutor
from apps.orchestrator.sales.crm_planner import CRMPlanner
from apps.orchestrator.sales.models import (
    Company,
    Contact,
    ContactReport,
    DecisionRequest,
    ProposalEdit,
)
from apps.orchestrator.sales.proposals import ProposalService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.sqlite import SQLiteRunRepository

ACTOR = {"email": "curran@toirinc.com", "workspace_id": "toir"}


def report(email=False):
    text = (
        "Example Corporation operates example.com in the US. "
        "Alex Morgan is the current CTO of Example Corporation. "
        "Alex Morgan's LinkedIn is https://www.linkedin.com/in/alex-morgan. "
        "Alex Morgan can be contacted at alex@example.com."
    )
    source = Source(
        id="src_0123456789abcdef",
        url="https://example.com/team",
        title="Our leadership",
        retrieved_at=datetime.now(UTC),
        text=text,
    )
    quote = Citation(source_id=source.id, quote=text)
    return ContactReport(
        company=Company(
            name="Example Corporation",
            domain="example.com",
            citations=[quote],
            description="Public company overview",
            country="US",
        ),
        contacts=[
            Contact(
                name="Alex Morgan",
                title="CTO",
                company_domain="example.com",
                buying_relevance="Leads engineering decisions.",
                citations=[quote],
                linkedin_url="https://www.linkedin.com/in/alex-morgan",
                linkedin_citations=[quote],
                email="alex@example.com" if email else None,
                email_citations=[quote] if email else [],
            )
        ],
        sources=[source],
    )


class FakeCRM:
    def __init__(self):
        self.companies = {}
        self.contacts = {}
        self.notes = {}
        self.associations = set()
        self.writes = []
        self.failure = None
        self.ambiguous = False

    async def find_company(self, domain):
        matches = [
            row for row in self.companies.values() if row["properties"].get("domain") == domain
        ]
        if len(matches) > 1:
            raise AmbiguousMatch("Duplicate domains")
        return copy.deepcopy(matches[0]) if matches else None

    async def find_contact(self, name, email, company_id):
        if self.ambiguous:
            raise AmbiguousMatch("Ambiguous contact")
        for row in self.contacts.values():
            props = row["properties"]
            if (email and props.get("email") == email) or (
                " ".join([props.get("firstname", ""), props.get("lastname", "")]) == name
                and (row["id"], company_id) in self.associations
            ):
                return copy.deepcopy(row)
        return None

    async def get_record(self, kind, record_id):
        records = self.companies if kind == "company" else self.contacts
        if record_id not in records:
            raise CRMError("Record no longer exists")
        return copy.deepcopy(records[record_id])

    async def write(self, kind, action, properties, record_id=None):
        await asyncio.sleep(0)
        self.writes.append((kind, action, properties, record_id))
        failure, self.failure = self.failure, None
        if failure and not failure.uncertain:
            raise failure
        if kind == "association":
            self.associations.add((properties["from_object_id"], properties["to_object_id"]))
            result_id = "association"
        else:
            records = {"company": self.companies, "contact": self.contacts, "note": self.notes}[
                kind
            ]
            result_id = record_id or f"{kind}-{len(records) + 1}"
            records.setdefault(result_id, {"id": result_id, "properties": {}})["properties"].update(
                properties
            )
        if failure:
            raise failure
        return result_id


async def setup(tmp_path):
    repo = await SQLiteRunRepository.open(tmp_path / "crm.sqlite")
    store = SalesStore(repo)
    await store.setup()
    async with store.transaction() as tx:
        await tx.put("member", ACTOR["email"], {**ACTOR, "active": True, "role": "sales"})
    return repo, store


async def plan(store, crm, research=None):
    proposal = await CRMPlanner(crm, store).plan(
        research or report(),
        session_id="session",
        job_id="job",
        requested_by=ACTOR["email"],
    )
    async with store.transaction() as tx:
        await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
    return proposal


async def approve(store, proposal):
    return await ProposalService(store).decide(
        proposal.id,
        DecisionRequest(version=proposal.version, decision="approved"),
        ACTOR,
        f"approve-{proposal.id}-{proposal.version}",
    )


def test_no_mutation_before_approval_and_only_persisted_approved_values(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await plan(store, crm)
            executor = CRMExecutor(store, crm)
            with pytest.raises(ApprovalRequired):
                await executor.execute(proposal.id)
            assert crm.writes == []
            await approve(store, proposal)
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                saved["operations"][0]["properties"]["name"] = "Unapproved name"
                await tx.put("proposal", proposal.id, saved)
            with pytest.raises(ApprovalRequired):
                await executor.execute(proposal.id)
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_complete_no_email_proposal_and_concurrent_execution(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            first, second = CRMExecutor(store, crm), CRMExecutor(store, crm)
            await asyncio.gather(first.execute(proposal.id), second.execute(proposal.id))
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                journal = await tx.list("crm_operation")
                identities = await tx.list("identity")
            assert saved["execution"] == "succeeded"
            assert len(crm.companies) == len(crm.contacts) == 1
            assert len(crm.notes) == 2
            assert all(item["status"] == "succeeded" for item in journal)
            assert identities[0]["crm_record_id"] == "contact-1"
            assert "email" not in crm.contacts["contact-1"]["properties"]
            count = len(crm.writes)
            await first.execute(proposal.id)
            assert len(crm.writes) == count
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_new_duplicate_after_approval_requires_new_version(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            crm.companies["existing"] = {
                "id": "existing",
                "properties": {
                    "name": "Example",
                    "domain": "example.com",
                },
            }
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.status == "pending"
            assert result.execution == "needs_review"
            assert result.version == 2 and result.decided_by is None
            assert result.operations[0].record_id == "existing"
            assert crm.writes == []
            await approve(store, result)
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "succeeded"
            assert len(crm.companies) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_changed_before_value_requires_renewed_approval(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            crm.companies["old"] = {
                "id": "old",
                "properties": {
                    "name": "Former name",
                    "domain": "example.com",
                    "description": "old",
                },
            }
            proposal = await approve(store, await plan(store, crm))
            crm.companies["old"]["properties"]["description"] = "Edited by sales rep"
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "needs_review" and result.version == 2
            assert result.operations[0].before["description"] == "Edited by sales rep"
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_uncertain_create_reconciles_without_duplicate(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            crm.failure = CRMError("Response lost", uncertain=True)
            executor = CRMExecutor(store, crm)
            failed = await executor.execute(proposal.id)
            assert failed.operations[0].status == "uncertain"
            assert len(crm.companies) == 1
            await ProposalService(store).retry(proposal.id, ACTOR)
            result = await executor.execute(proposal.id)
            assert result.execution == "succeeded"
            assert len([write for write in crm.writes if write[:2] == ("company", "create")]) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_uncertain_create_without_match_never_repeats(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            crm.failure = CRMError("Response lost", uncertain=True)
            executor = CRMExecutor(store, crm)
            await executor.execute(proposal.id)
            crm.companies.clear()
            await ProposalService(store).retry(proposal.id, ACTOR)
            result = await executor.execute(proposal.id)
            assert result.execution == "failed"
            assert result.operations[0].status == "uncertain"
            assert len(crm.writes) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_partial_execution_retry_preserves_success(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            real_write = crm.write
            failed_note = False

            async def write(kind, action, properties, record_id=None):
                nonlocal failed_note
                if kind == "note" and not failed_note:
                    failed_note = True
                    raise CRMError("Rate limit", code="rate_limit")
                return await real_write(kind, action, properties, record_id)

            crm.write = write
            proposal = await approve(store, await plan(store, crm))
            executor = CRMExecutor(store, crm)
            result = await executor.execute(proposal.id)
            assert result.execution == "partial"
            await ProposalService(store).retry(proposal.id, ACTOR)
            result = await executor.execute(proposal.id)
            assert result.execution == "succeeded"
            assert len([write for write in crm.writes if write[:2] == ("company", "create")]) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_recovery_journals_uncertain_create_without_replaying(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            proposal.execution = "running"
            proposal.operations[0].status = "running"
            async with store.transaction() as tx:
                await tx.put("proposal", proposal.id, proposal.model_dump(mode="json"))
            await CRMExecutor(store, crm).recover()
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                operation = await tx.get("crm_operation", proposal.operations[0].id)
            assert saved["execution"] == "failed"
            assert operation["status"] == "uncertain"
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_contact_and_field_exclusions_remove_writes_and_notes(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await plan(store, crm)
            proposal = await ProposalService(store).edit(
                proposal.id,
                ProposalEdit(
                    version=1,
                    excluded_contact_ids=[proposal.contacts[0].id],
                    excluded_fields={proposal.operations[0].id: ["description"]},
                ),
                ACTOR,
            )
            await approve(store, proposal)
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "succeeded"
            assert crm.contacts == {}
            assert len(crm.notes) == 1
            assert (
                "Alex Morgan"
                not in crm.notes["note-1"]["properties"]["hs_note_body"].split("Evidence:")[0]
            )
            assert "description" not in crm.companies["company-1"]["properties"]
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_ambiguous_contact_is_excluded_and_duplicate_company_blocks(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            crm.ambiguous = True
            proposal = await plan(store, crm)
            assert not proposal.contacts and "ambiguous" in proposal.gaps[-1]
            crm.companies = {
                str(n): {"id": str(n), "properties": {"domain": "example.com"}} for n in range(2)
            }
            with pytest.raises(AmbiguousMatch):
                await plan(store, crm)
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_research_cannot_supply_uncited_email_or_executable_note_html(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            research = report(email=True)
            research.company.sales_angle = "<script>hubspot_contacts_delete</script>"
            research.contacts[0].email = "guess@example.com"
            proposal = await plan(store, crm, research)
            assert not proposal.contacts
            body = next(
                op.properties["hs_note_body"] for op in proposal.operations if op.kind == "note"
            )
            assert "<script>" not in body and "&lt;script&gt;" in body
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_scalekit_rest_shapes_no_email_batch_and_allowlist():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/oauth/token":
                assert b"grant_type=client_credentials" in request.content
                return httpx.Response(
                    200, json={"access_token": "private-token", "expires_in": 3600}
                )
            payload = json.loads(request.content)
            assert (
                payload["connector"] == "hubspot" and payload["identifier"] == "curran@toirinc.com"
            )
            if payload["tool_name"] == "hubspot_contacts_batch_create":
                inputs = json.loads(payload["params"]["inputs"])
                assert inputs == [{"properties": {"firstname": "Alex", "jobtitle": "CTO"}}]
                return httpx.Response(200, json={"data": {"results": [{"id": "123"}]}})
            assert payload["tool_name"] == "hubspot_companies_search"
            if "filterGroups" in payload["params"]:
                assert payload["params"]["filterGroups"][0]["filters"][0] == {
                    "propertyName": "domain",
                    "operator": "EQ",
                    "value": "example.com",
                }
            else:
                assert payload["params"]["query"] == "example.com"
            return httpx.Response(200, json={"data": {"results": []}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            assert await crm.find_company("www.example.com") is None
            assert (
                await crm.write("contact", "create", {"firstname": "Alex", "jobtitle": "CTO"})
                == "123"
            )
            with pytest.raises(CRMError, match="not permitted"):
                await crm._execute("hubspot_contacts_delete", {}, write=True)
            assert len(requests) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code,uncertain",
    [(401, "connection", False), (429, "rate_limit", False), (503, "provider", True)],
)
def test_provider_failures_are_sanitized_and_classified(status, code, uncertain):
    async def scenario():
        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "secret-token"})
            return httpx.Response(status, json={"error": "secret leaked provider body"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "client-secret", client=http)
            with pytest.raises(CRMError) as caught:
                await crm.write("company", "create", {"name": "Example", "domain": "example.com"})
            assert caught.value.code == code and caught.value.uncertain == uncertain
            assert "secret" not in str(caught.value)

    asyncio.run(scenario())


def test_capabilities_require_write_attestation_and_only_perform_reads():
    async def scenario():
        from apps.orchestrator.sales.crm import READ_TOOLS, WRITE_TOOLS

        tool_calls = []

        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "server-token"})
            if request.url.path == "/api/v1/tools/scoped":
                return httpx.Response(
                    200,
                    json={
                        "tools": [
                            {"tool": {"definition": {"name": name}}}
                            for name in READ_TOOLS | WRITE_TOOLS
                        ]
                    },
                )
            payload = json.loads(request.content)
            tool_calls.append(payload["tool_name"])
            return httpx.Response(200, json={"data": {"results": []}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            capability = await crm.capabilities()
            assert capability["read_verified"] and not capability["ready"]
            assert not capability["write_scopes_verified"]
            crm.write_scopes_verified = True
            assert (await crm.capabilities())["ready"]
            assert set(tool_calls) <= READ_TOOLS

    asyncio.run(scenario())


def test_preflight_read_failure_does_not_save_half_revised_approval(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            crm.companies["new"] = {"id": "new", "properties": {"domain": "example.com"}}

            async def unavailable(*args):
                raise CRMError("Contact lookup unavailable")

            original_find = crm.find_contact
            crm.find_contact = unavailable
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "failed" and result.version == 1
            assert result.operations[0].action == "create"
            assert result.operations[0].record_id is None
            crm.find_contact = original_find
            await ProposalService(store).retry(proposal.id, ACTOR)
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "needs_review" and result.version == 2
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_contact_search_requires_exact_name_and_company_association():
    async def scenario():
        reads = []

        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "server-token"})
            payload = json.loads(request.content)
            reads.append(payload)
            if payload["tool_name"] == "hubspot_contacts_search":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "results": [
                                {
                                    "id": "person",
                                    "properties": {"firstname": "Alex", "lastname": "Morgan"},
                                },
                                {
                                    "id": "other",
                                    "properties": {"firstname": "Alex", "lastname": "Different"},
                                },
                            ]
                        }
                    },
                )
            assert payload["tool_name"] == "hubspot_associations_batch_read"
            assert json.loads(payload["params"]["inputs"]) == [{"id": "person"}]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "results": [
                            {
                                "from": {"id": "person"},
                                "to": [{"toObjectId": "company"}],
                            }
                        ]
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            assert (await crm.find_contact("Alex Morgan", None, "company"))["id"] == "person"
            with pytest.raises(AmbiguousMatch):
                await crm.find_contact("Alex Morgan", None, "unrelated-company")
            assert len(reads) == 4

    asyncio.run(scenario())


def test_revoked_approver_cannot_trigger_queued_crm_execution(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            async with store.transaction() as tx:
                await tx.put("member", ACTOR["email"], {**ACTOR, "active": False, "role": "sales"})
            with pytest.raises(ApprovalRequired):
                await CRMExecutor(store, crm).execute(proposal.id)
            assert crm.writes == []
        finally:
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "stored_domain",
    [
        "https://example.com/",
        "https://www.example.com/about",
        "http://EXAMPLE.com/",
        "example.com/",
    ],
)
def test_company_matching_finds_canonical_url_forms(stored_domain):
    async def scenario():
        queries = []

        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "server-token"})
            params = json.loads(request.content)["params"]
            queries.append(params)
            rows = (
                []
                if "filterGroups" in params
                else [
                    {"id": "existing", "properties": {"domain": stored_domain}},
                    {"id": "unrelated", "properties": {"domain": "other-example.com"}},
                ]
            )
            return httpx.Response(200, json={"data": {"results": rows, "total": len(rows)}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            assert (await crm.find_company("example.com"))["id"] == "existing"
            assert len(queries) == 2 and queries[1]["query"] == "example.com"

    asyncio.run(scenario())


def test_broad_domain_search_collects_pages_and_refuses_duplicate_url_records():
    async def scenario():
        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "server-token"})
            params = json.loads(request.content)["params"]
            if "filterGroups" in params:
                result = {"results": [{"id": "first", "properties": {"domain": "example.com"}}]}
            elif not params.get("after"):
                result = {
                    "results": [{"id": "first", "properties": {"domain": "example.com"}}],
                    "paging": {"next": {"after": "1"}},
                    "total": 2,
                }
            else:
                result = {
                    "results": [
                        {
                            "id": "second",
                            "properties": {
                                "domain": "https://www.example.com/",
                            },
                        }
                    ],
                    "total": 2,
                }
            return httpx.Response(200, json={"data": result})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            with pytest.raises(AmbiguousMatch, match="Multiple"):
                await crm.find_company("example.com")

    asyncio.run(scenario())


def test_incomplete_domain_search_cannot_be_used_as_absent_company():
    async def scenario():
        def handler(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "server-token"})
            params = json.loads(request.content)["params"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "results": [],
                        "total": 0 if "filterGroups" in params else 600,
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            crm = ScalekitCRM("https://scalekit.example", "client", "secret", client=http)
            with pytest.raises(AmbiguousMatch, match="incomplete"):
                await crm.find_company("example.com")

    asyncio.run(scenario())


def test_unexpected_provider_exception_is_uncertain_sanitized_and_reconciled(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            write = crm.write
            failed = False

            async def unexpected(*args):
                nonlocal failed
                result = await write(*args)
                if not failed:
                    failed = True
                    raise ValueError("Decoder failed with token=must-not-leak")
                return result

            crm.write = unexpected
            executor = CRMExecutor(store, crm)
            result = await executor.execute(proposal.id)
            assert result.execution == "failed" and result.operations[0].status == "uncertain"
            assert "must-not-leak" not in result.model_dump_json()
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                journal = await tx.get("crm_operation", result.operations[0].id)
            assert saved["execution"] == "failed" and journal["status"] == "uncertain"
            await ProposalService(store).retry(proposal.id, ACTOR)
            result = await executor.execute(proposal.id)
            assert result.execution == "succeeded"
            assert len([w for w in crm.writes if w[:2] == ("company", "create")]) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_unexpected_preflight_exception_is_durably_failed(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))

            async def unavailable(*args):
                raise TypeError("Unexpected decoder error with sensitive details")

            crm.find_company = unavailable
            result = await CRMExecutor(store, crm).execute(proposal.id)
            assert result.execution == "failed" and "sensitive" not in result.error
            assert result.operations[0].status == "pending" and not crm.writes
            async with store.transaction() as tx:
                assert (await tx.get("proposal", proposal.id))["execution"] == "failed"
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_cancellation_preserves_running_journal_for_restart_recovery(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))

            async def cancelled(*args):
                raise asyncio.CancelledError()

            crm.write = cancelled
            executor = CRMExecutor(store, crm)
            with pytest.raises(asyncio.CancelledError):
                await executor.execute(proposal.id)
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                journal = await tx.get("crm_operation", proposal.operations[0].id)
            assert saved["execution"] == "running" and journal["status"] == "running"
            await executor.recover()
            async with store.transaction() as tx:
                saved = await tx.get("proposal", proposal.id)
                journal = await tx.get("crm_operation", proposal.operations[0].id)
            assert saved["execution"] == "failed" and journal["status"] == "uncertain"
            assert not crm.writes
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_maintenance_checked_inside_claim_keeps_work_queued_until_resume(tmp_path):
    async def scenario():
        repo, store = await setup(tmp_path)
        try:
            crm = FakeCRM()
            proposal = await approve(store, await plan(store, crm))
            paused = False
            executor = CRMExecutor(store, crm, can_execute=lambda: not paused)
            # Simulate the tick passing its outer maintenance check, then blocking on storage.
            async with store.transaction() as tx:
                execution = asyncio.create_task(executor.execute(proposal.id))
                await asyncio.sleep(0)
                paused = True
                assert (await tx.get("proposal", proposal.id))["execution"] == "queued"
            result = await execution
            assert result.execution == "queued" and crm.writes == []
            async with store.transaction() as tx:
                assert (await tx.get("proposal", proposal.id))["execution"] == "queued"
                assert await tx.list("crm_operation") == []
            paused = False
            result = await executor.execute(proposal.id)
            assert result.execution == "succeeded" and len(crm.companies) == 1
        finally:
            await repo.close()

    asyncio.run(scenario())
