"""Browser meeting endpoints; coordinator owns policy and persistent state."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from apps.api.meeting_client import MeetingClient, get_meeting_client

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
Client = Annotated[MeetingClient, Depends(get_meeting_client)]


@router.get("/workspace")
async def workspace(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/meetings/workspace")


@router.post("", status_code=201)
async def create(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/meetings")


@router.post("/imports", status_code=201)
async def import_transcript(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/meetings/imports")


@router.post("/demos", status_code=201)
async def demo(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/meetings/demos")


@router.post("/{meeting_id}/retries")
async def retry_meeting(meeting_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/meetings/{meeting_id}/retries")


@router.patch("/tasks/{task_id}")
async def edit_task(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/meetings/tasks/{task_id}")


@router.post("/tasks/{task_id}/decisions")
async def decide(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/meetings/tasks/{task_id}/decisions")


@router.post("/tasks/{task_id}/retries")
async def retry_task(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/meetings/tasks/{task_id}/retries")


@router.post("/tasks/{task_id}/reconciliation")
async def reconcile(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/meetings/tasks/{task_id}/reconciliation")


@router.post("/webhooks/recall/{channel}", status_code=202)
async def webhook(
    channel: Literal["realtime", "dashboard"], request: Request, client: Client
) -> Response:
    return await client.forward(request, f"/v1/meetings/webhooks/recall/{channel}")
