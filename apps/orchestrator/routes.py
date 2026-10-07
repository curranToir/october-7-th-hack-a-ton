from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from apps.orchestrator.coordinator import Coordinator, NotConfigured
from apps.orchestrator.models.research import Brief, Run, RunDetail
from apps.orchestrator.sales.routes import sales_user
from apps.orchestrator.storage.ports import Conflict

router = APIRouter(prefix="/v1", tags=["research"])


def get_coordinator(request: Request) -> Coordinator:
    return request.app.state.coordinator


Service = Annotated[Coordinator, Depends(get_coordinator)]
RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


async def required_run(service: Coordinator, run_id: UUID) -> Run:
    run = await service.repository.get(str(run_id))
    if run is None:
        raise HTTPException(404, "Research run not found")
    return run


async def create(service: Coordinator, brief: Brief, key: UUID, parent_id=None):
    try:
        return await service.create(brief, str(key), parent_id)
    except Conflict as error:
        raise HTTPException(409, str(error)) from None
    except NotConfigured as error:
        raise HTTPException(503, str(error)) from None


@router.get("/capabilities", dependencies=[Depends(sales_user)])
async def capabilities(service: Service):
    return await service.capabilities()


@router.post(
    "/research-runs",
    response_model=Run,
    status_code=202,
    dependencies=[Depends(sales_user)],
)
async def create_run(brief: Brief, key: RequestKey, service: Service):
    return await create(service, brief, key)


@router.get("/research-runs", response_model=list[Run], dependencies=[Depends(sales_user)])
async def list_runs(service: Service):
    return await service.repository.list()


@router.get(
    "/research-runs/{run_id}",
    response_model=RunDetail,
    dependencies=[Depends(sales_user)],
)
async def get_run(run_id: UUID, service: Service):
    run = await required_run(service, run_id)
    return RunDetail(run=run, events=await service.repository.events(run.id))


@router.post(
    "/research-runs/{run_id}/cancellation",
    response_model=Run,
    dependencies=[Depends(sales_user)],
)
async def cancel_run(run_id: UUID, service: Service):
    return await service.cancel(await required_run(service, run_id))


@router.post(
    "/research-runs/{run_id}/retries",
    response_model=Run,
    status_code=202,
    dependencies=[Depends(sales_user)],
)
async def retry_run(run_id: UUID, key: RequestKey, service: Service):
    parent = await required_run(service, run_id)
    if parent.status in {"queued", "running"}:
        raise HTTPException(409, "Wait for the original run to finish or cancel it")
    return await create(service, parent.brief, key, parent.id)


class Maintenance(BaseModel):
    enabled: bool


@router.get("/maintenance")
async def maintenance(service: Service, request: Request):
    status = await service.maintenance_status()
    sales = getattr(request.app.state, "sales_service", None)
    if sales is not None:
        status.update(await sales.maintenance_status())
    return status


@router.post("/maintenance")
async def set_maintenance(body: Maintenance, service: Service, request: Request):
    await service.set_maintenance(body.enabled)
    return await maintenance(service, request)
