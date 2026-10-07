"""Additive sales contracts. Research v1 remains unchanged."""

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from apps.orchestrator.models.research import Brief, Citation, Contract, Source


def uid() -> str:
    return str(uuid4())


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Record(Contract):
    id: str = Field(default_factory=uid)
    workspace_id: Literal["toir"] = "toir"
    created_at: str = Field(default_factory=timestamp)
    updated_at: str = Field(default_factory=timestamp)


class Company(Contract):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253)
    description: str = Field(default="", max_length=3000)
    country: str | None = None
    employee_count: int | None = Field(default=None, ge=1)
    citations: list[Citation] = Field(min_length=1, max_length=10)
    fit_score: int = Field(default=0, ge=0, le=100)
    sales_angle: str = Field(default="", max_length=3000)

    @field_validator("domain")
    @classmethod
    def domain_value(cls, value: str) -> str:
        host = urlsplit(value if "://" in value else f"https://{value}").hostname or ""
        host = host.lower().removeprefix("www.").rstrip(".")
        if "." not in host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
            raise ValueError("A company domain is required")
        return host


class Contact(Contract):
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    company_domain: str
    buying_relevance: str = Field(min_length=1, max_length=1500)
    linkedin_url: str | None = None
    email: str | None = None
    phone: str | None = None
    citations: list[Citation] = Field(min_length=1, max_length=10)
    linkedin_citations: list[Citation] = Field(default_factory=list, max_length=5)
    email_citations: list[Citation] = Field(default_factory=list, max_length=5)
    phone_citations: list[Citation] = Field(default_factory=list, max_length=5)

    @field_validator("linkedin_url")
    @classmethod
    def linkedin(cls, value):
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"linkedin.com", "www.linkedin.com"}
            or not parsed.path.startswith("/in/")
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Expected a public LinkedIn person profile")
        return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


class ContactReport(Contract):
    candidates: list[Company] = Field(default_factory=list, max_length=5)
    company: Company | None = None
    contacts: list[Contact] = Field(default_factory=list, max_length=5)
    sources: list[Source] = Field(default_factory=list, max_length=100)
    gaps: list[str] = Field(default_factory=list, max_length=40)
    summary: str = Field(default="", max_length=3000)


class ContactTask(Contract):
    version: Literal[1] = 1
    task_id: str
    run_id: str
    mode: Literal["resolve", "enrich"]
    query: str = Field(min_length=1, max_length=4000)
    company: Company | None = None
    deadline_at: str
    prior_sources: list[Source] = Field(default_factory=list, max_length=100)
    usage: dict[str, int] = Field(default_factory=dict)


class Session(Record):
    title: str = Field(default="New chat", max_length=80)
    owner: str
    automation: bool = False
    deleted: bool = False


class Message(Record):
    session_id: str
    role: Literal["user", "assistant"]
    content: str = Field(max_length=12000)
    task_ids: list[str] = Field(default_factory=list)
    job_id: str | None = None
    request_key: str | None = None


class Automation(Record):
    id: str = "default"
    enabled: bool = False
    owner: str = "curran@toirinc.com"
    request: str = (
        "Find US companies with practical AI integration needs and recent buying signals."
    )
    geography: Literal["US"] = "US"
    employee_min: int = Field(default=20, ge=1, le=100000)
    employee_max: int = Field(default=1000, ge=1, le=100000)
    fit_threshold: int = Field(default=70, ge=0, le=100)
    daily_enrichments: int = Field(default=25, ge=1, le=100)
    daily_discoveries: int = Field(default=10, ge=1, le=100)
    timezone: Literal["America/Los_Angeles"] = "America/Los_Angeles"

    @model_validator(mode="after")
    def ordered(self):
        if self.employee_min > self.employee_max:
            raise ValueError("Minimum size exceeds maximum")
        return self


class Job(Record):
    session_id: str
    requested_by: str
    kind: Literal["chat", "discovery", "resolve", "enrich"]
    origin: Literal["chat", "background"] = "chat"
    status: Literal[
        "queued", "running", "needs_input", "completed", "failed", "cancelled", "interrupted"
    ] = "queued"
    query: str
    propose_crm: bool = False
    company: Company | None = None
    candidates: list[Company] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    report: ContactReport | None = None
    task_id: str | None = None
    research_run_id: str | None = None
    research_brief: Brief | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    deadline_at: str | None = None
    progress: str = "Queued"
    error: str | None = None
    budget_day: str | None = None
    parent_id: str | None = None


class CRMOperation(Contract):
    id: str = Field(default_factory=uid)
    kind: Literal["company", "contact", "association", "note"]
    action: Literal["create", "update", "associate"]
    contact_id: str | None = None
    record_id: str | None = None
    properties: dict[str, str | int | float | None] = Field(default_factory=dict)
    before: dict[str, str | int | float | None] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "succeeded", "failed", "uncertain"] = "pending"
    result_id: str | None = None
    error: str | None = None


class Proposal(Record):
    session_id: str
    job_id: str
    requested_by: str
    company: Company
    contacts: list[Contact] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    version: int = 1
    status: Literal["pending", "approved", "denied"] = "pending"
    execution: Literal[
        "not_started", "queued", "running", "succeeded", "partial", "failed", "needs_review"
    ] = "not_started"
    operations: list[CRMOperation] = Field(default_factory=list)
    excluded_contact_ids: list[str] = Field(default_factory=list)
    excluded_operation_ids: list[str] = Field(default_factory=list)
    excluded_fields: dict[str, list[str]] = Field(default_factory=dict)
    decided_by: str | None = None
    decided_at: str | None = None
    error: str | None = None
    memory_status: Literal["pending", "synced", "blocked"] = "pending"


class DecisionRequest(Contract):
    version: int = Field(ge=1)
    decision: Literal["approved", "denied"]


class ProposalEdit(Contract):
    version: int = Field(ge=1)
    excluded_contact_ids: list[str] = Field(default_factory=list)
    excluded_operation_ids: list[str] = Field(default_factory=list)
    excluded_fields: dict[str, list[str]] = Field(default_factory=dict)
