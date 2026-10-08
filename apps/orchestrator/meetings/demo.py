"""Explicit fictional fixture: no claims of a real call, model run or research."""

from apps.orchestrator.meetings.models import AnalysisRequest, MeetingNotes, Segment


def demo_transcript():
    return AnalysisRequest(
        title="Demo · Customer export issue",
        segments=[
            Segment(
                id="demo-1",
                speaker="Curran",
                start=0,
                text="Thanks for joining. What is stopping your team today?",
            ),
            Segment(
                id="demo-2",
                speaker="Alex · Example customer",
                start=8,
                text="When I filter the customer report to last month and click Export CSV, "
                "the download is empty. It should contain the 248 rows I can see in the report.",
            ),
            Segment(
                id="demo-3",
                speaker="Alex · Example customer",
                start=24,
                text="Our operations team cannot finish its weekly customer review. "
                "We have been copying rows by hand.",
            ),
            Segment(
                id="demo-4",
                speaker="Alex · Example customer",
                start=39,
                text="We are also evaluating Linear. Could you look into Linear for us?",
            ),
            Segment(
                id="demo-5",
                speaker="Curran",
                start=51,
                text="I will send a clear report to our engineer after reviewing the notes.",
            ),
        ],
    )


def demo_notes():
    segments = demo_transcript().segments
    return MeetingNotes.model_validate(
        {
            "summary": "A fictional customer reports empty CSV exports with a date filter. "
            "Their operations team is manually copying rows and evaluating Linear.",
            "customer": "Alex · Example customer (fictional demo)",
            "needs": ["Export all visible rows in a filtered customer report."],
            "next_steps": ["Review the proposed issue before sending it to engineering."],
            "issues": [
                {
                    "title": "[Demo] Filtered customer report exports an empty CSV",
                    "problem": "The customer reports that Export CSV produces an empty file when "
                    "the customer report is filtered to last month, despite 248 visible rows.",
                    "impact": "Operations is copying rows manually and cannot finish its review.",
                    "expected_behavior": "The CSV should include all 248 visible rows.",
                    "reproduction_steps": [
                        "Open the customer report.",
                        "Filter to last month.",
                        "Click Export CSV and inspect the downloaded file.",
                    ],
                    "evidence": [
                        {"segment_id": "demo-2", "quote": segments[1].text},
                        {"segment_id": "demo-3", "quote": segments[2].text},
                    ],
                }
            ],
            "mentions": [
                {
                    "kind": "company",
                    "name": "Linear",
                    "context": "A product being evaluated.",
                    "evidence": {"segment_id": "demo-4", "quote": segments[3].text},
                }
            ],
        }
    )
