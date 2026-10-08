"""Authenticated routes installed by the Brain API host."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request
from fastapi.responses import JSONResponse

from .research_contract import (
    IngestionError,
    ResearchAcknowledgment,
    ResearchCapabilities,
    ResearchIngestionStatus,
    ResearchRequest,
)
from .research_ingestion import ResearchIngestion

router = APIRouter(tags=["research memory"])
Identity = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[a-f0-9]{64}$")]


def get_research_service(request: Request) -> ResearchIngestion:
    return request.app.state.research_ingestion


Service = Annotated[ResearchIngestion, Depends(get_research_service)]


async def ingestion_failure(request: Request, error: IngestionError):
    headers = {"Retry-After": "60"} if error.status == 503 else None
    return JSONResponse(status_code=error.status, content={"detail": error.code}, headers=headers)


@router.get("/capabilities", response_model=ResearchCapabilities)
async def capabilities(service: Service):
    return await service.capabilities()


@router.post("/remember/research", response_model=ResearchAcknowledgment)
async def remember(body: ResearchRequest, idempotency_key: IdempotencyKey, service: Service):
    return await service.submit(body, idempotency_key)


@router.get("/remember/research/{ingestion_id}", response_model=ResearchIngestionStatus)
async def status(ingestion_id: Identity, service: Service):
    return await service.status(ingestion_id)
