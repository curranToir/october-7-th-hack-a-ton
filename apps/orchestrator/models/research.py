"""Contract v1: IDs and evidence travel unchanged across the Python/Bun boundary."""

from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brief(Contract):
    request: str = Field(min_length=10, max_length=4000)
    geography: Literal["US"] = "US"
    employee_min: int = Field(default=20, ge=1, le=100000)
    employee_max: int = Field(default=1000, ge=1, le=100000)
    target_count: int = Field(default=10, ge=1, le=10)
    leadership_days: int = Field(default=180, ge=1, le=365)
    signal_days: int = Field(default=90, ge=1, le=365)
    deadline_seconds: int = Field(default=600, ge=60, le=600)

    @field_validator("request")
    @classmethod
    def clean_request(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("Describe the companies or business needs to research")
        return value.strip()

    @model_validator(mode="after")
    def size_order(self):
        if self.employee_min > self.employee_max:
            raise ValueError("Minimum company size must not exceed maximum")
        return self


class Source(Contract):
    id: str = Field(pattern=r"^src_[a-f0-9]{16}$")
    url: HttpUrl
    title: str = Field(max_length=500)
    retrieved_at: datetime
    published_at: date | None = None
    text: str = Field(min_length=1, max_length=12000)


class Citation(Contract):
    source_id: str
    quote: str = Field(min_length=12, max_length=1000)


class Signal(Contract):
    kind: Literal["leadership", "funding", "partnership", "business_need"]
    claim: str = Field(min_length=10, max_length=1500)
    event_date: date | None
    citations: list[Citation] = Field(min_length=1, max_length=5)


class Lead(Contract):
    company: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253)
    country: Literal["US"]
    employee_count: int | None = Field(default=None, ge=1)
    identity_citations: list[Citation] = Field(min_length=1, max_length=5)
    decision_maker: str | None = Field(default=None, max_length=300)
    signals: list[Signal] = Field(min_length=1, max_length=5)
    ai_use_case: str = Field(min_length=10, max_length=1500)
    rationale: str = Field(min_length=10, max_length=1500)
    outreach_angle: str = Field(min_length=10, max_length=1500)
    fit_score: int = Field(ge=0, le=100)

    @field_validator("domain")
    @classmethod
    def canonical_domain(cls, value: str) -> str:
        host = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
        host = host.lower().removeprefix("www.").rstrip(".")
        if "." not in host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
            raise ValueError("Expected a company domain")
        return host


class CompetitorFact(Contract):
    company: str = Field(min_length=1, max_length=200)
    kind: Literal["advertisement", "marketing", "pricing", "customer"]
    claim: str = Field(min_length=10, max_length=1500)
    citations: list[Citation] = Field(min_length=1, max_length=5)
    positioning_hypothesis: str = Field(max_length=1500)


class ResearchReport(Contract):
    leads: list[Lead] = Field(default_factory=list, max_length=30)
    competitors: list[CompetitorFact] = Field(default_factory=list, max_length=20)
    sources: list[Source] = Field(default_factory=list, max_length=100)
    gaps: list[str] = Field(default_factory=list, max_length=40)
    summary: str = Field(default="", max_length=3000)


class ResearchPlan(Contract):
    queries: list[str] = Field(min_length=1, max_length=12)
    focus: str = Field(max_length=2000)


class Review(Contract):
    accepted_domains: list[str] = Field(max_length=30)
    accepted_competitor_indices: list[int] = Field(max_length=20)
    follow_up_queries: list[str] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list, max_length=20)


RunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]


class Run(Contract):
    id: str
    parent_id: str | None
    status: RunStatus
    stage: str
    brief: Brief
    created_at: str
    updated_at: str
    deadline_at: str
    task_id: str | None = None
    pass_number: int = 0
    report: ResearchReport = Field(default_factory=ResearchReport)
    plan: ResearchPlan | None = None
    error: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    trace_id: str | None = None


class RunEvent(Contract):
    sequence: int
    run_id: str
    created_at: str
    stage: str
    message: str


class RunDetail(Contract):
    run: Run
    events: list[RunEvent]
