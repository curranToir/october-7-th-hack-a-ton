"""Authenticated meeting resources; signed provider webhooks have a separate trust boundary."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from apps.orchestrator.meetings.models import (
    MeetingCreate,
    MeetingView,
    MeetingWorkspace,
    TaskDecision,
    TaskEdit,
    TaskView,
    TranscriptImport,
)
from apps.orchestrator.meetings.providers import ProviderFailure
from apps.orchestrator.meetings.service import MeetingService
from apps.orchestrator.sales.routes import sales_user
from apps.orchestrator.storage.ports import Conflict

router = APIRouter(prefix="/v1/meetings", tags=["meetings"])


def meeting_service(request: Request) -> MeetingService:
    service = getattr(request.app.state, "meeting_service", None)
    if service is None:
        raise HTTPException(503, "Meeting workspace is unavailable")
    return service


async def meeting_user(request: Request):
    actor = await sales_user(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if meeting_service(request).coordinator.maintenance:
            raise HTTPException(409, "Meeting actions are paused for maintenance")
    return actor


Service = Annotated[MeetingService, Depends(meeting_service)]
Actor = Annotated[dict, Depends(meeting_user)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]


async def action(work):
    try:
        return await work
    except Conflict as error:
        raise HTTPException(409, str(error)) from None
    except LookupError:
        raise HTTPException(404, "Meeting record not found") from None
    except PermissionError:
        raise HTTPException(403, "Invalid or expired meeting webhook signature") from None
    except ValueError:
        raise HTTPException(422, "Meeting data could not be validated") from None
    except ProviderFailure as error:
        raise HTTPException(503, str(error)) from None


@router.get("/workspace", response_model=MeetingWorkspace)
async def workspace(service: Service, actor: Actor, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await service.workspace()


@router.post("", status_code=201, response_model=MeetingView)
async def create(body: MeetingCreate, service: Service, actor: Actor, key: Key):
    return await action(service.capture.create("zoom", body, str(key), actor))


@router.post("/imports", status_code=201, response_model=MeetingView)
async def import_transcript(body: TranscriptImport, service: Service, actor: Actor, key: Key):
    return await action(service.capture.create("import", body, str(key), actor))


@router.post("/demos", status_code=201, response_model=MeetingView)
async def demo(service: Service, actor: Actor, key: Key):
    return await action(service.capture.create("demo", None, str(key), actor))


@router.post("/{meeting_id}/retries", response_model=MeetingView)
async def retry_meeting(meeting_id: UUID, service: Service, actor: Actor):
    return await action(service.processing.retry(str(meeting_id)))


@router.patch("/tasks/{task_id}", response_model=TaskView)
async def edit_task(task_id: UUID, body: TaskEdit, service: Service, actor: Actor):
    return await action(service.tasks.edit(str(task_id), body))


@router.post("/tasks/{task_id}/decisions", response_model=TaskView)
async def decide(task_id: UUID, body: TaskDecision, service: Service, actor: Actor):
    return await action(service.tasks.decide(str(task_id), body, actor))


@router.post("/tasks/{task_id}/retries", response_model=TaskView)
async def retry_task(task_id: UUID, service: Service, actor: Actor):
    return await action(service.tasks.retry(str(task_id)))


@router.post("/tasks/{task_id}/reconciliation", response_model=TaskView)
async def reconcile(task_id: UUID, service: Service, actor: Actor):
    return await action(service.tasks.reconcile(str(task_id)))


@router.post("/webhooks/recall/{channel}", status_code=202)
async def webhook(channel: Literal["realtime", "dashboard"], request: Request, service: Service):
    if service.coordinator.maintenance:
        raise HTTPException(503, "Meeting ingestion is paused", headers={"Retry-After": "30"})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_000_000:
            raise HTTPException(413, "Meeting webhook is too large")
    await action(service.capture.webhook(bytes(body), request.headers, channel))
    return {"accepted": True}
