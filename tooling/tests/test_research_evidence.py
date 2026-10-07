"""Admission failures are deterministic; no LLM or external network is used here."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from apps.orchestrator.graph.evidence import finalize_report, qualify
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
    size_quote = f"Acme is a US company with {employees} employees."
    result = qualify_report(report(
        leads=[lead(employee_count=employees, identity_citations=[citation(size_quote)])],
        text=f"{size_quote} {APPOINTMENT}",
    ))
    assert result.leads == []
    assert any("outside the requested 20–1000 range" in gap for gap in result.gaps)


@pytest.mark.parametrize("employees", [20, 1000])
def test_retains_inclusive_employee_count_boundaries(employees):
    size_quote = f"Acme is a US company with {employees} employees."
    result = qualify_report(report(
        leads=[lead(employee_count=employees, identity_citations=[citation(size_quote)])],
        text=f"{size_quote} {APPOINTMENT}",
    ))
    assert len(result.leads) == 1
    assert result.leads[0].employee_count == employees


def test_unknown_size_is_not_qualified_despite_strong_recent_signal():
    identity = "Acme is a US company based in Austin, Texas."
    candidate = lead(employee_count=None, identity_citations=[citation(identity)], fit_score=100)
    candidates = report(leads=[candidate], text=f"{identity} {APPOINTMENT}")
    result = qualify_report(candidates)
    assert result.leads == []
    assert any("employee count is unknown" in gap for gap in result.gaps)
    assert result.sources == candidates.sources
    # Nullable estimates remain valid candidate data; qualification does not invent a count.
    assert candidates.leads[0].employee_count is None


def test_employee_count_must_fit_the_actual_brief_not_only_default_bounds():
    result = qualify_report(report(), employee_min=150, employee_max=250)
    assert result.leads == []
    assert any("outside the requested 150–250 range" in gap for gap in result.gaps)


def test_unknown_size_and_stale_signal_do_not_displace_a_qualified_company():
    candidates = [
        lead(company="Unknown size", domain="unknown.example", employee_count=None, fit_score=100),
        lead(company="Stale signal", domain="stale.example", fit_score=99, signals=[
            signal(event_date=TODAY - timedelta(days=181)),
        ]),
        lead(company="Qualified", domain="qualified.example", fit_score=80),
    ]
    result = qualify_report(report(leads=candidates), target_count=1)
    assert [candidate.domain for candidate in result.leads] == ["qualified.example"]
    assert any("Unknown size: employee count is unknown" in gap for gap in result.gaps)
    assert any("Stale signal: no supported, in-window buying signal" in gap for gap in result.gaps)


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


def test_finalization_replaces_stale_funding_rationale_and_pre_filter_coverage_claims():
    brief = Brief(request="Find qualified US companies for Toir", target_count=2)
    accepted = lead(
        company="Trase", domain="trase.example",
        signals=[
            signal(),
            signal(kind="funding", event_date=TODAY - timedelta(days=91), quote=FUNDING),
        ],
        rationale="Fresh funding inside the last 90 days makes Trase ready to buy immediately.",
    )
    unknown_size = lead(company="Unknown size", domain="unknown.example", employee_count=None)
    raw = report(leads=[accepted, unknown_size])
    raw.gaps = [
        "Only 9 leads were found.",
        "Companies with null employee counts were retained because they seemed promising.",
        "Trase has fresh funding inside the 90-day window.",
    ]
    raw.summary = "Nine leads with fresh funding were qualified."
    admitted = qualify(raw, brief, today=TODAY)
    assert [entry.kind for entry in admitted.leads[0].signals] == ["leadership"]
    assert raw.gaps[0] in admitted.gaps, "Raw gaps remain usable during internal refinement"
    before_finalization = admitted.model_dump(mode="json")

    final = finalize_report(admitted, brief)
    assert len(final.leads) == 1
    assert "leadership" in final.leads[0].rationale.casefold()
    assert "funding" not in final.leads[0].rationale.casefold()
    assert "buy immediately" not in final.leads[0].rationale.casefold()
    assert "Only 1 of 2 requested companies qualified." in final.gaps
    assert "No verified funding signals." in final.gaps
    published_prose = " ".join([final.summary, *final.gaps, final.leads[0].rationale]).casefold()
    assert "9 leads" not in published_prose
    assert "fresh funding" not in published_prose
    assert "null employee counts were retained" not in published_prose
    assert final.summary.startswith("1 companies qualified against the supplied brief.")
    assert final.leads[0].signals == admitted.leads[0].signals
    assert final.leads[0].identity_citations == admitted.leads[0].identity_citations
    assert final.leads[0].ai_use_case == admitted.leads[0].ai_use_case
    assert final.leads[0].outreach_angle == admitted.leads[0].outreach_angle
    assert final.sources == admitted.sources
    assert admitted.model_dump(mode="json") == before_finalization


@pytest.mark.parametrize("retained_kind", ["leadership", "funding", "partnership"])
def test_final_signal_coverage_uses_only_the_signals_in_accepted_companies(retained_kind):
    brief = Brief(request="Find qualified US companies for Toir", target_count=1)
    accepted = report(leads=[lead(signals=[signal(kind=retained_kind)])])
    # These claims may have described earlier passes; they cannot dictate final coverage.
    accepted.gaps = ["No leadership was found.", "All funding and partnerships were verified."]
    final = finalize_report(accepted, brief)
    for kind in ["leadership", "funding", "partnership"]:
        assert (f"No verified {kind} signals." in final.gaps) is (kind != retained_kind)
    assert not any(gap.startswith("Only ") for gap in final.gaps)
    assert final.gaps[-1] == (
        "Public-web coverage is incomplete; missing evidence does not prove absence."
    )


@pytest.mark.parametrize("fact_kind,covered_label", [
    ("marketing", None),
    ("advertisement", "advertisements"),
    ("pricing", "pricing"),
    ("customer", "customer relationships"),
])
def test_final_competitor_coverage_distinguishes_marketing_from_actual_evidence(
    fact_kind, covered_label,
):
    claims = {
        "marketing": "OtherCo markets an AI integration service.",
        "advertisement": "OtherCo published a paid advertisement for its AI integration service.",
        "pricing": "OtherCo publicly prices its integration starter package at $12,000.",
        "customer": COMPETITOR,
    }
    fact = CompetitorFact(
        company="OtherCo", kind=fact_kind, claim=claims[fact_kind],
        citations=[citation(claims[fact_kind])],
        positioning_hypothesis="A narrower initial implementation scope may suit some buyers.",
    )
    accepted = report(
        competitors=[fact], text=" ".join([IDENTITY, APPOINTMENT, claims[fact_kind]]),
    )
    accepted.gaps = ["All ad libraries were searched and every competitor price is known."]
    brief = Brief(request="Find qualified US companies for Toir", target_count=1)
    final = finalize_report(accepted, brief)
    for label in ["advertisements", "pricing", "customer relationships"]:
        assert (f"No verified competitor {label}." in final.gaps) is (label != covered_label)
    assert final.competitors == accepted.competitors
    assert final.competitors[0].positioning_hypothesis == fact.positioning_hypothesis
    assert not any("all ad libraries" in gap.casefold() for gap in final.gaps)


def test_finalization_with_complete_coverage_keeps_only_conservative_web_limitation():
    brief = Brief(request="Find qualified US companies for Toir", target_count=1)
    candidate = lead(signals=[signal(kind=kind) for kind in [
        "leadership", "funding", "partnership", "business_need",
    ]])
    facts = [CompetitorFact(
        company="OtherCo", kind=kind, claim=COMPETITOR, citations=[citation(COMPETITOR)],
        positioning_hypothesis="Investigate a narrower implementation scope.",
    ) for kind in ["advertisement", "pricing", "customer"]]
    # Input here is the post-semantic-review aggregate; finalization never rejudges facts.
    accepted = report(leads=[candidate], competitors=facts)
    accepted.gaps = ["An earlier pass had no companies or competitor evidence."]
    final = finalize_report(accepted, brief)
    assert final.gaps == [
        "Public-web coverage is incomplete; missing evidence does not prove absence.",
    ]
    assert final.summary == (
        "1 companies qualified against the supplied brief. "
        "AI use cases and outreach angles are proposals, not verified buying intent."
    )
    assert final.leads[0].signals == candidate.signals
    assert finalize_report(final, brief) == final


def test_finalization_counts_post_semantic_rejections_and_preserves_source_history():
    brief = Brief(request="Find qualified US companies for Toir", target_count=3)
    accepted = report(leads=[])
    accepted.summary = "Previously found three companies."
    accepted.gaps = ["Only two companies passed deterministic checks."]
    final = finalize_report(accepted, brief)
    assert final.leads == []
    assert final.sources == accepted.sources
    assert "Only 0 of 3 requested companies qualified." in final.gaps
    assert final.summary.startswith("0 companies qualified")
    assert not any("two companies" in gap for gap in final.gaps)
    for kind in ["leadership", "funding", "partnership"]:
        assert f"No verified {kind} signals." in final.gaps


def test_final_rationale_is_bounded_even_with_maximum_length_verified_claims():
    brief = Brief(request="Find qualified US companies for Toir", target_count=1)
    accepted = report()
    accepted.leads[0].signals = [
        signal(kind=kind).model_copy(update={"claim": "Verified source statement. " * 55})
        for kind in ["leadership", "funding", "partnership", "business_need", "business_need"]
    ]
    final = finalize_report(accepted, brief)
    assert len(final.leads[0].rationale) <= 1500
    assert "Verified source statement." not in final.leads[0].rationale
    assert final.leads[0].signals == accepted.leads[0].signals
    assert ResearchReport.model_validate(final.model_dump()) == final
