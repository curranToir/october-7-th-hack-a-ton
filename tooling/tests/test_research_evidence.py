"""Admission failures are deterministic; no LLM or external network is used here."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from apps.orchestrator.graph.evidence import qualify
from apps.orchestrator.models.research import (
    Brief,
    Citation,
    CompetitorFact,
    Lead,
    ResearchReport,
    Signal,
    Source,
)

TODAY = date(2026, 10, 7)
SOURCE_ID = "src_0123456789abcdef"
IDENTITY = "Acme is a US company with 100 employees."
APPOINTMENT = "Acme appointed Jordan as CTO on October 1, 2026."
FUNDING = "Acme announced a new funding round for its operating platform."
NEED = "Acme needs to automate manual warehouse scheduling."
COMPETITOR = "OtherCo publicly lists Acme as a customer of its AI integration service."


def citation(quote=APPOINTMENT, source_id=SOURCE_ID):
    return Citation(source_id=source_id, quote=quote)


def signal(kind="leadership", event_date=date(2026, 10, 1), quote=APPOINTMENT):
    return Signal(kind=kind, claim=quote, event_date=event_date, citations=[citation(quote)])


def lead(**changes):
    values = {
        "company": "Acme", "domain": "acme.example", "country": "US", "employee_count": 100,
        "identity_citations": [citation(IDENTITY)], "decision_maker": "Jordan, CTO",
        "signals": [signal()], "ai_use_case": "Automate warehouse scheduling with AI.",
        "rationale": "A new technology leader can sponsor an integration pilot.",
        "outreach_angle": "Offer a scoped scheduling pilot with measurable outcomes.",
        "fit_score": 85,
    }
    return Lead(**(values | changes))


def report(leads=None, competitors=None, text=None):
    return ResearchReport(
        leads=[lead()] if leads is None else leads,
        competitors=competitors or [],
        sources=[Source(
            id=SOURCE_ID, url="https://acme.example/news", title="Acme company announcement",
            retrieved_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
            published_at=date(2026, 10, 6),
            text=text or " ".join([IDENTITY, APPOINTMENT, FUNDING, NEED, COMPETITOR]),
        )],
    )


def qualify_report(value, **brief_overrides):
    brief = Brief(request="Find US companies with new decision-makers", **brief_overrides)
    return qualify(value, brief, today=TODAY)


def test_accepts_retrieved_identity_and_recent_appointment_without_mutating_input():
    candidate = report()
    before = candidate.model_dump(mode="json")
    result = qualify_report(candidate)
    assert len(result.leads) == 1
    assert result.leads[0].decision_maker == "Jordan, CTO"
    assert result.leads[0].signals[0].event_date == date(2026, 10, 1)
    assert candidate.model_dump(mode="json") == before


@pytest.mark.parametrize("location", ["identity", "signal"])
@pytest.mark.parametrize("citation_failure", ["unknown_source", "fabricated_quote"])
def test_rejects_unknown_source_ids_and_fabricated_quotes(location, citation_failure):
    bad = citation(
        quote="This quotation was never retrieved from the source." if citation_failure ==
        "fabricated_quote" else APPOINTMENT,
        source_id="src_ffffffffffffffff" if citation_failure == "unknown_source" else SOURCE_ID,
    )
    candidate = lead()
    if location == "identity":
        candidate.identity_citations = [bad]
    else:
        candidate.signals[0].citations = [bad]
    result = qualify_report(report(leads=[candidate]))
    assert result.leads == []
    assert result.gaps


def test_all_citations_on_a_claim_must_be_grounded():
    candidate = lead()
    candidate.signals[0].citations.append(citation(source_id="src_ffffffffffffffff"))
    assert qualify_report(report(leads=[candidate])).leads == []


def test_matches_quotes_across_case_and_whitespace_changes():
    result = qualify_report(report(text="\n\t".join([IDENTITY.upper(), APPOINTMENT.upper()])))
    assert len(result.leads) == 1


@pytest.mark.parametrize("quote", ["", "too short"])
def test_citation_contract_rejects_missing_or_unusable_quotation(quote):
    with pytest.raises(ValidationError):
        citation(quote=quote)


def test_signal_contract_requires_citation():
    with pytest.raises(ValidationError):
        Signal(kind="leadership", claim=APPOINTMENT, event_date=TODAY, citations=[])


@pytest.mark.parametrize("event_date", [
    None, TODAY - timedelta(days=181), TODAY + timedelta(days=1),
])
def test_rejects_missing_stale_and_future_appointment_dates(event_date):
    candidate = lead(signals=[signal(event_date=event_date)])
    result = qualify_report(report(leads=[candidate]))
    assert result.leads == []
    # A recent publication timestamp must never substitute for an event timestamp.
    assert result.sources[0].published_at == date(2026, 10, 6)


@pytest.mark.parametrize("kind", ["leadership", "funding", "partnership"])
def test_event_window_includes_exact_boundary_and_today(kind):
    days = 180 if kind == "leadership" else 90
    for event_date in [TODAY - timedelta(days=days), TODAY]:
        candidate = lead(signals=[signal(kind=kind, event_date=event_date)])
        assert len(qualify_report(report(leads=[candidate])).leads) == 1


@pytest.mark.parametrize("kind", ["funding", "partnership"])
def test_funding_and_partnership_use_shorter_window(kind):
    candidate = lead(signals=[signal(kind=kind, event_date=TODAY - timedelta(days=91))])
    assert qualify_report(report(leads=[candidate])).leads == []


def test_custom_freshness_window_is_applied():
    candidate = lead(signals=[signal(event_date=TODAY - timedelta(days=31))])
    assert qualify_report(report(leads=[candidate]), leadership_days=30).leads == []


def test_keeps_supported_business_need_without_inventing_appointment_or_date():
    candidate = lead(signals=[
        signal(event_date=TODAY - timedelta(days=181)),
        signal(kind="business_need", event_date=None, quote=NEED),
    ])
    result = qualify_report(report(leads=[candidate]))
    assert len(result.leads) == 1
    assert [entry.kind for entry in result.leads[0].signals] == ["business_need"]
    assert result.leads[0].signals[0].event_date is None
    assert result.leads[0].decision_maker is None
    assert candidate.decision_maker == "Jordan, CTO"


@pytest.mark.parametrize("employees", [19, 1001])
def test_excludes_companies_outside_requested_employee_range(employees):
    assert qualify_report(report(leads=[lead(employee_count=employees)])).leads == []


@pytest.mark.parametrize("employees", [20, 1000, None])
def test_retains_boundary_and_unknown_employee_counts(employees):
    result = qualify_report(report(leads=[lead(employee_count=employees)]))
    assert len(result.leads) == 1
    assert result.leads[0].employee_count == employees


def test_deduplicates_canonical_domains_and_keeps_best_supported_candidate():
    candidates = [
        lead(domain="https://WWW.ACME.EXAMPLE./about", fit_score=70),
        lead(domain="acme.example", fit_score=92, company="Acme Incorporated"),
        lead(domain="other.example", fit_score=80, company="Other"),
    ]
    result = qualify_report(report(leads=candidates))
    assert [(entry.domain, entry.fit_score) for entry in result.leads] == [
        ("acme.example", 92), ("other.example", 80),
    ]
    assert result.leads[0].company == "Acme Incorporated"


def test_limits_results_after_sorting_and_deduplication():
    result = qualify_report(report(leads=[
        lead(domain="z.example", fit_score=70),
        lead(domain="b.example", fit_score=90),
        lead(domain="a.example", fit_score=90),
    ]), target_count=2)
    assert [entry.domain for entry in result.leads] == ["a.example", "b.example"]


@pytest.mark.parametrize("kind", ["advertisement", "marketing", "pricing", "customer"])
def test_excludes_competitor_claims_with_missing_or_fabricated_evidence(kind):
    good = CompetitorFact(
        company="OtherCo", kind=kind, claim=COMPETITOR, citations=[citation(COMPETITOR)],
        positioning_hypothesis="Investigate whether a focused implementation scope would help.",
    )
    missing = good.model_copy(update={"citations": [citation(source_id="src_ffffffffffffffff")]})
    fabricated = good.model_copy(update={"citations": [citation("OtherCo offers a price of $1.")]})
    result = qualify_report(report(competitors=[missing, good, fabricated]))
    assert result.competitors == [good]
    assert any("competitor" in gap for gap in result.gaps)


def test_brief_contract_rejects_inverted_employee_range():
    with pytest.raises(ValidationError, match="Minimum company size"):
        Brief(request="Find recent company funding rounds", employee_min=1000, employee_max=20)
