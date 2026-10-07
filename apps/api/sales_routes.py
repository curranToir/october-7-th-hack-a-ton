"""Explicit sales-resource routes; all policy and state live in the coordinator."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from apps.api.sales_client import SalesClient, get_sales_client

router = APIRouter(prefix="/api/sales", tags=["sales"])
Client = Annotated[SalesClient, Depends(get_sales_client)]
RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


@router.get("/workspace")
async def workspace(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/sales/workspace")


@router.get("/capabilities")
async def capabilities(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/sales/capabilities")


@router.post("/sessions", status_code=201)
async def create_session(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/sales/sessions")


@router.patch("/sessions/{session_id}")
async def edit_session(session_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/sessions/{session_id}")


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/sessions/{session_id}")


@router.post("/sessions/{session_id}/messages", status_code=202)
async def message(session_id: UUID, request: Request, key: RequestKey, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/sessions/{session_id}/messages", key=key)


@router.patch("/tasks/{task_id}")
async def edit_task(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/tasks/{task_id}")


@router.post("/tasks/{task_id}/decisions")
async def decision(task_id: UUID, request: Request, key: RequestKey, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/tasks/{task_id}/decisions", key=key)


@router.post("/tasks/{task_id}/retries")
async def retry_task(task_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/tasks/{task_id}/retries")


@router.patch("/automation")
async def edit_automation(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/sales/automation")


@router.post("/jobs/{job_id}/cancellation")
async def cancel_job(job_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/jobs/{job_id}/cancellation")


@router.post("/jobs/{job_id}/retries")
async def retry_job(job_id: UUID, request: Request, client: Client) -> Response:
    return await client.forward(request, f"/v1/sales/jobs/{job_id}/retries")
