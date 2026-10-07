from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from apps.api.research_client import ResearchClient
from apps.orchestrator.models.research import Brief, Run, RunDetail

router = APIRouter(prefix="/api", tags=["research"])


def get_client(request: Request) -> ResearchClient:
    return request.app.state.research_client


Client = Annotated[ResearchClient, Depends(get_client)]
RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


@router.get("/research-capabilities")
async def capabilities(client: Client):
    return await client.request("GET", "/v1/capabilities")


@router.post("/research-runs", response_model=Run, status_code=202)
async def create_run(body: Brief, key: RequestKey, client: Client):
    return await client.request(
        "POST",
        "/v1/research-runs",
        body=body.model_dump(),
        key=str(key),
    )


@router.get("/research-runs", response_model=list[Run])
async def list_runs(client: Client):
    return await client.request("GET", "/v1/research-runs")


@router.get("/research-runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: UUID, client: Client):
    return await client.request("GET", f"/v1/research-runs/{run_id}")


@router.post("/research-runs/{run_id}/cancellation", response_model=Run)
async def cancel_run(run_id: UUID, client: Client):
    return await client.request("POST", f"/v1/research-runs/{run_id}/cancellation")


@router.post("/research-runs/{run_id}/retries", response_model=Run, status_code=202)
async def retry_run(run_id: UUID, key: RequestKey, client: Client):
    return await client.request("POST", f"/v1/research-runs/{run_id}/retries", key=str(key))
