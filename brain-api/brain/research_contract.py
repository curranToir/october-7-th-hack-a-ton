"""Research ingestion contracts and lossless, deterministic document projection."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .registry import document

DATASET = "toir-pipeline"
MAX_REQUEST_BYTES = 2_000_000


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_user: str = Field(min_length=1, max_length=254)
    run_id: str = Field(min_length=1, max_length=200)
    report: dict[str, JsonValue]


class ResearchAcknowledgment(BaseModel):
    dataset: Literal["toir-pipeline"] = DATASET
    ingestion_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents: int = Field(ge=1)


class ResearchCapabilities(BaseModel):
    research_idempotency: bool
    research_writers: list[str]


class ResearchIngestionStatus(BaseModel):
    ingestion_id: str
    status: Literal["prepared", "completed", "uncertain", "forgotten", "legacy"]
    completed: int
    active_document: int | None
    provider_completions: list[dict[str, str]]


class IngestionError(Exception):
    """Only fixed public codes belong in this exception, never provider errors."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def prepare(body: ResearchRequest, ingestion_id: str):
    """Preserve original report and nested evidence while validating citation links."""
    if body.report.get("ingestion_id") != ingestion_id:
        raise IngestionError(422, "ingestion_id_mismatch")
    try:
        payload = canonical(body.model_dump(mode="json"))
    except (ValueError, TypeError, RecursionError):
        raise IngestionError(422, "invalid_research_report") from None
    if len(payload.encode()) > MAX_REQUEST_BYTES:
        raise IngestionError(413, "research_report_too_large")
    leads = body.report.get("leads")
    if (
        not isinstance(leads, list)
        or not 1 <= len(leads) <= 100
        or not all(isinstance(lead, dict) and lead for lead in leads)
    ):
        raise IngestionError(422, "research_leads_required")
    raw_sources = body.report.get("sources", [])
    if not isinstance(raw_sources, list):
        raise IngestionError(422, "invalid_research_sources")
    sources = {}
    for source in raw_sources:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("id"), str)
            or not source["id"]
        ):
            raise IngestionError(422, "invalid_research_sources")
        if source["id"] in sources:
            raise IngestionError(422, "duplicate_research_source")
        sources[source["id"]] = source

    docs = []
    for index, lead in enumerate(leads):
        cited_ids = set()
        pending = [lead]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                source_id = value.get("source_id")
                if source_id is not None:
                    if not isinstance(source_id, str) or source_id not in sources:
                        raise IngestionError(422, "unresolved_research_citation")
                    cited_ids.add(source_id)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        cited = [sources[source_id] for source_id in sorted(cited_ids)]
        title = str(lead.get("company", lead.get("name", lead.get("title", f"Lead {index + 1}"))))
        content = canonical(
            {
                "ingestion_id": ingestion_id,
                "lead": lead,
                "sources": raw_sources,
                "cited_sources": cited,
            }
        )
        docs.append(
            document(
                "research",
                f"{body.run_id}:{ingestion_id}:{index}",
                DATASET,
                title,
                cited[0].get("url", "") if cited else "",
                content,
            )
        )
    return hashlib.sha256(payload.encode()).hexdigest(), payload, docs
