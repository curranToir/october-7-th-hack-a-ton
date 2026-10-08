"""Validate model evidence and render the exact GitHub proposal before approval."""

from apps.orchestrator.meetings.models import AnalysisRequest, MeetingNotes


def validate_evidence(notes: MeetingNotes, request: AnalysisRequest) -> MeetingNotes:
    segments = {s.id: s.text for s in request.segments}
    evidence = [e for issue in notes.issues for e in issue.evidence]
    evidence.extend(m.evidence for m in notes.mentions)
    if any(e.segment_id not in segments or e.quote not in segments[e.segment_id] for e in evidence):
        raise ValueError("The agent returned evidence that is not present in the transcript")
    for mention in notes.mentions:
        if mention.name.casefold() not in mention.evidence.quote.casefold():
            raise ValueError("A research subject must be explicitly named in its evidence")
    return notes


def issue_body(issue, meeting):
    segments = {s["id"]: s for s in meeting["transcript"]}
    lines = [
        "## Customer-reported problem",
        issue.problem,
        "\n## Impact",
        issue.impact or "Not established in the call.",
        "\n## Expected behavior",
        issue.expected_behavior or "Needs clarification.",
        "\n## Reproduction steps",
        *(f"{i + 1}. {step}" for i, step in enumerate(issue.reproduction_steps)),
    ]
    if not issue.reproduction_steps:
        lines.append("Not established; confirm with the customer.")
    lines.extend(["\n## Call evidence"])
    for e in issue.evidence:
        segment = segments[e.segment_id]
        timestamp = int(segment["start"])
        lines.append(f"\n{segment['speaker']} · {timestamp // 60:02d}:{timestamp % 60:02d}")
        lines.append("\n".join("> " + line for line in e.quote.splitlines()))
    lines.extend(
        [
            f"\nSource: {meeting['title']} ({meeting['source']}); meeting {meeting['id']}.",
            "Customer report; engineering has not independently reproduced it.",
        ]
    )
    if meeting["source"] == "demo":
        lines.append("\n**Demo scenario — fictional customer report.**")
    return "\n".join(lines)
