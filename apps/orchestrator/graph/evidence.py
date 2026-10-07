"""Deterministic evidence admission precedes model-assisted semantic review."""

import re
from datetime import date

from apps.orchestrator.models.research import Brief, Citation, ResearchReport


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def qualify(report: ResearchReport, brief: Brief, today: date | None = None) -> ResearchReport:
    today = today or date.today()
    sources = {s.id: s for s in report.sources}
    gaps = list(report.gaps)

    def supported(citations: list[Citation]) -> bool:
        return bool(citations) and all(
            citation.source_id in sources
            and normalized(citation.quote) in normalized(sources[citation.source_id].text)
            for citation in citations
        )

    leads = {}
    for candidate in report.leads:
        if not supported(candidate.identity_citations):
            gaps.append(f"Excluded {candidate.company}: company identity lacks retrieved evidence.")
            continue
        if candidate.employee_count is not None and not (
            brief.employee_min <= candidate.employee_count <= brief.employee_max
        ):
            continue
        signals = []
        for signal in candidate.signals:
            if not supported(signal.citations):
                continue
            if signal.kind != "business_need":
                days = brief.leadership_days if signal.kind == "leadership" else brief.signal_days
                if signal.event_date is None or not 0 <= (today - signal.event_date).days <= days:
                    continue
            signals.append(signal)
        if not signals:
            gaps.append(f"Excluded {candidate.company}: no supported, in-window buying signal.")
            continue
        lead = candidate.model_copy(update={"signals": signals})
        if not any(s.kind == "leadership" for s in signals):
            lead.decision_maker = None
        previous = leads.get(lead.domain)
        if previous is None or lead.fit_score > previous.fit_score:
            leads[lead.domain] = lead
    competitors = [fact for fact in report.competitors if supported(fact.citations)]
    if len(competitors) != len(report.competitors):
        gaps.append("Some competitor claims were excluded because their citations were unverified.")
    return ResearchReport(
        leads=sorted(leads.values(), key=lambda item: (-item.fit_score, item.domain))[
            : brief.target_count
        ],
        competitors=competitors,
        sources=list(sources.values()),
        gaps=list(dict.fromkeys(gaps))[:40],
    )
