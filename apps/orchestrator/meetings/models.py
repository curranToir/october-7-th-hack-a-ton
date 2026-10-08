"""Versioned meeting contracts shared with the internal extraction agent."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from apps.orchestrator.models.research import Contract


class Segment(Contract):
    id: str = Field(min_length=1, max_length=128)
    speaker: str = Field(min_length=1, max_length=200)
    start: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=12000)


class Evidence(Contract):
    segment_id: str
    quote: str = Field(min_length=5, max_length=2000)


class IssueDraft(Contract):
    title: str = Field(min_length=5, max_length=200)
    problem: str = Field(min_length=10, max_length=4000)
    impact: str = Field(max_length=2000)
    expected_behavior: str = Field(max_length=2000)
    reproduction_steps: list[str] = Field(max_length=12)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class Mention(Contract):
    kind: Literal["company", "person"]
    name: str = Field(min_length=2, max_length=200)
    context: str = Field(max_length=1000)
    evidence: Evidence


class MeetingNotes(Contract):
    summary: str = Field(min_length=1, max_length=3000)
    customer: str = Field(max_length=500)
    needs: list[str] = Field(max_length=15)
    next_steps: list[str] = Field(max_length=15)
    issues: list[IssueDraft] = Field(max_length=8)
    mentions: list[Mention] = Field(max_length=10)


class AnalysisRequest(Contract):
    title: str = Field(max_length=200)
    segments: list[Segment] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def bounded_transcript(self):
        if sum(len(s.text) for s in self.segments) > 120000:
            raise ValueError("Transcript exceeds the 120,000 character analysis limit")
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("Transcript segment IDs must be unique")
        return self


class MeetingCreate(Contract):
    title: str = Field(min_length=1, max_length=200)
    meeting_url: str = Field(max_length=2048)
    join_at: datetime | None = None
    consent_confirmed: Literal[True]

    @field_validator("meeting_url")
    @classmethod
    def zoom_url(cls, value):
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.fragment
            or not (host == "zoom.us" or host.endswith(".zoom.us"))
            or not parsed.path.startswith(("/j/", "/my/", "/s/"))
        ):
            raise ValueError("Use a valid HTTPS Zoom meeting invitation link")
        return value

    @field_validator("join_at")
    @classmethod
    def aware_time(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Join time must include a timezone")
        return value


class TranscriptImport(AnalysisRequest):
    consent_confirmed: Literal[True]


class TaskEdit(Contract):
    version: int = Field(ge=1)
    title: str = Field(min_length=5, max_length=200)
    body: str = Field(min_length=20, max_length=16000)


class TaskDecision(Contract):
    version: int = Field(ge=1)
    decision: Literal["approve", "reject"]


class MeetingView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    owner_email: str
    source: Literal["zoom", "import", "demo"]
    status: str
    created_at: str
    error: str | None = None
    bot_id: str | None = None
    transcript: list[Segment] = Field(default_factory=list)
    notes: MeetingNotes | None = None
    research: list[dict] = Field(default_factory=list)


class TaskView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: str
    meeting_id: str
    title: str
    body: str
    repository: str
    version: int
    status: str
    source: str
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    issue_url: str | None = None
    error: str | None = None


class Capabilities(Contract):
    owner_email: str
    repository: str
    zoom_ready: bool
    analysis_ready: bool
    github_ready: bool
    missing: list[str]


class MeetingWorkspace(Contract):
    meetings: list[MeetingView]
    tasks: list[TaskView]
    capabilities: Capabilities
