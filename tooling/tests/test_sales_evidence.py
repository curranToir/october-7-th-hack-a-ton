"""Literal and semantic admission tests never call a model or public service."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import HttpUrl

from apps.orchestrator.models.research import Citation, Source
from apps.orchestrator.sales import evidence
from apps.orchestrator.sales.evidence import EvidenceReview, ground, review_report
from apps.orchestrator.sales.models import Company, Contact, ContactReport, Job


def fixture():
    quote = (
        "Example is a US software company with 100 employees. "
        "Jane Smith is Chief Technology Officer at Example. "
        "Jane Smith: jane@example.com, +1 212 555 1234; "
        "https://www.linkedin.com/in/janesmith"
    )
    source = Source(
        id="src_1234567890abcdef",
        url="https://example.com/team",
        title="Example team",
        retrieved_at=datetime.now(UTC),
        text=quote,
    )
    citation = Citation(source_id=source.id, quote=quote)
    company = Company(
        name="Example",
        domain="example.com",
        description="Software company",
        country="US",
        employee_count=100,
        citations=[citation],
        sales_angle="Explore workflow automation",
    )
    contact = Contact(
        id="jane",
        name="Jane Smith",
        title="Chief Technology Officer",
        company_domain="example.com",
        buying_relevance="Technical buyer",
        linkedin_url="https://www.linkedin.com/in/janesmith",
        email="jane@example.com",
        phone="+1 212 555 1234",
        citations=[citation],
        linkedin_citations=[citation],
        email_citations=[citation],
        phone_citations=[citation],
    )
    report = ContactReport(
        company=company,
        contacts=[contact],
        sources=[source],
        summary="Unreviewed prose must never reach the UI",
    )
    job = Job(
        session_id="session",
        requested_by="curran@toirinc.com",
        kind="enrich",
        query="Research Example",
        company=company,
        deadline_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    )
    return job, report


class Models:
    def __init__(self, **kwargs):
        self.data = None
        self.verdict = EvidenceReview(
            accepted_company_domains=["example.com"],
            accepted_contact_ids=["jane"],
            **kwargs,
        )

    async def structured(self, schema, prompt, data):
        assert schema is EvidenceReview
        assert "untrusted" in prompt
        self.data = data
        return self.verdict


def test_literal_fields_reject_missing_person_prefix_matches_and_forged_quotes():
    job, report = fixture()
    report.contacts[0].email = "jane@example.co"
    report.contacts[0].linkedin_url = "https://www.linkedin.com/in/jane"
    report.contacts[0].phone = "+1 212 555 9999"
    result = ground(report, job)
    person = result.contacts[0]
    assert person.email is person.linkedin_url is person.phone is None
    assert (
        not person.email_citations and not person.phone_citations and not person.linkedin_citations
    )
    assert report.contacts[0].email == "jane@example.co"  # Original evidence is retained.
    report.contacts[0].citations = [Citation(source_id=report.sources[0].id, quote=" " * 12)]
    assert ground(report, job).contacts == []


def test_employer_and_current_role_require_support_and_deduplicate_identity():
    job, report = fixture()
    report.contacts.append(report.contacts[0].model_copy(update={"id": "duplicate", "email": None}))
    assert len(ground(report, job).contacts) == 1
    report.contacts[0].title = "Chief Executive Officer"
    report.contacts = report.contacts[:1]
    assert not ground(report, job).contacts
    job, report = fixture()
    report.contacts[0].company_domain = "different.com"
    assert not ground(report, job).contacts
    job, report = fixture()
    report.sources[0].text = report.sources[0].text.replace("is Chief", "is former Chief")
    report.contacts[0].citations[0].quote = report.sources[0].text
    assert not ground(report, job).contacts


def test_company_identity_private_urls_and_conflicting_sources_fail_closed():
    job, report = fixture()
    report.company.domain = "different.com"
    assert ground(report, job).company is None
    job, report = fixture()
    report.sources[0].url = "http://169.254.169.254/team"
    assert ground(report, job).company is None
    job, report = fixture()
    report.sources.append(report.sources[0].model_copy(update={"text": "different evidence"}))
    assert ground(report, job).company is None


def test_resolve_ambiguity_cannot_be_removed_by_semantic_reviewer_guess():
    async def scenario():
        job, report = fixture()
        job.kind, job.company = "resolve", None
        other_source = report.sources[0].model_copy(
            update={"id": "src_aaaaaaaaaaaaaaaa", "url": HttpUrl("https://example.net/team")}
        )
        report.sources.append(other_source)
        report.candidates = [
            report.company.model_copy(
                update={
                    "domain": "example.net",
                    "citations": [Citation(source_id=other_source.id, quote=other_source.text)],
                }
            )
        ]
        result = await review_report(Models(), job, report)
        assert result.company is None
        assert result.contacts == []
        assert result.candidates[0].domain == "example.com"

    asyncio.run(scenario())


def test_semantics_require_affirmative_company_facts_and_remove_rejected_field_citations():
    async def scenario():
        job, report = fixture()
        models = Models(
            accepted_company_fields={"example.com": ["country"]},
            rejected_fields={"jane": ["email", "nonexistent"]},
        )
        result = await review_report(models, job, report)
        assert result.company.country == "US"
        assert result.company.employee_count is None
        assert result.company.description == ""
        assert result.contacts[0].email is None and not result.contacts[0].email_citations
        assert result.contacts[0].linkedin_url == "https://www.linkedin.com/in/janesmith"
        assert "unverified" in result.contacts[0].buying_relevance
        assert "unverified" in result.company.sales_angle
        assert "Unreviewed prose" not in result.summary
        assert "summary" not in models.data["report"]
        assert result.sources == report.sources

    asyncio.run(scenario())


def test_source_context_is_cited_bounded_and_preserves_complete_persisted_text():
    async def scenario():
        job, report = fixture()
        report.sources[0].text = "padding " * 1300 + report.sources[0].text
        for index in range(99):
            report.sources.append(
                Source(
                    id=f"src_{index:016x}",
                    url=f"https://example.com/{index}",
                    title="Uncited",
                    text="private uncited text " * 500,
                    retrieved_at=datetime.now(UTC),
                )
            )
        models = Models()
        result = await review_report(models, job, report)
        assert len(json.dumps(models.data)) <= evidence.MAX_REVIEW_CHARS
        assert len(models.data["report"]["sources"]) == 1
        assert models.data["report"]["sources"][0]["truncated"] is True
        assert "private uncited text" not in json.dumps(models.data)
        assert len(result.sources) == 100
        assert result.sources[0].text == report.sources[0].text

    asyncio.run(scenario())


def test_timeout_invalid_verdict_and_past_deadlines_never_admit_unreviewed_contacts(monkeypatch):
    class FailingModels:
        async def structured(self, *_):
            raise ValueError("private-secret-response")

    class SlowModels:
        async def structured(self, *_):
            await asyncio.sleep(1)

    async def scenario():
        job, report = fixture()
        with pytest.raises(RuntimeError, match="could not be completed") as error:
            await review_report(FailingModels(), job, report)
        assert "private-secret" not in str(error.value)
        monkeypatch.setattr(evidence, "REVIEW_TIMEOUT_SECONDS", 0.005)
        with pytest.raises(RuntimeError, match="deadline"):
            await review_report(SlowModels(), job, report)
        job.deadline_at = "2020-01-01T00:00:00+00:00"
        with pytest.raises(RuntimeError, match="deadline"):
            await review_report(Models(), job, report)
        job.deadline_at = "invalid"
        with pytest.raises(RuntimeError, match="invalid deadline"):
            await review_report(Models(), job, report)

    asyncio.run(scenario())
