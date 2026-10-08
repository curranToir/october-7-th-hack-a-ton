"""Authenticated sales resources; business rules stay in domain services."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from apps.orchestrator.models.research import Contract
from apps.orchestrator.sales.auth import FLOW_COOKIE, FLOW_TTL_SECONDS, SESSION_COOKIE
from apps.orchestrator.sales.auth_sessions import AuthSessionView, UserView
from apps.orchestrator.sales.models import DecisionRequest, Message, Proposal, ProposalEdit, Session
from apps.orchestrator.sales.service import SalesService
from apps.orchestrator.storage.ports import Conflict

router = APIRouter(prefix="/v1/sales", tags=["sales"])
auth_router = APIRouter(prefix="/v1", tags=["authentication"])


def sales_service(request: Request) -> SalesService:
    service = getattr(request.app.state, "sales_service", None)
    if service is None:
        raise HTTPException(503, "Sales workspace is unavailable")
    return service


async def sales_user(request: Request):
    service = sales_service(request)
    actor = await service.auth.current_user(request.cookies.get(SESSION_COOKIE))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        service.auth.assert_same_origin(request.headers.get("origin"))
    return actor


Service = Annotated[SalesService, Depends(sales_service)]
Actor = Annotated[dict, Depends(sales_user)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]


async def action(work):
    try:
        return await work
    except Conflict as error:
        raise HTTPException(409, str(error)) from None
    except PermissionError:
        raise HTTPException(403, "Sales workspace access is required") from None
    except LookupError:
        raise HTTPException(404, "Record not found") from None
    except ValueError as error:
        raise HTTPException(422, str(error)[:500]) from None


@auth_router.get("/auth/login")
async def login(service: Service):
    url, flow = await service.auth.login()
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        FLOW_COOKIE,
        flow,
        max_age=FLOW_TTL_SECONDS,
        path="/api/auth",
        secure=service.auth.config.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def clear_session_cookie(response: Response, service: SalesService) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=service.auth.config.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@auth_router.get("/auth/callback")
async def callback(
    request: Request,
    service: Service,
    code: Annotated[str, Query(max_length=8192)] = "",
    state: Annotated[str, Query(max_length=256)] = "",
    error: Annotated[str, Query(max_length=256)] = "",
):
    try:
        token = await service.auth.callback(
            code,
            state,
            request.cookies.get(FLOW_COOKIE),
            error=error,
            previous_token=request.cookies.get(SESSION_COOKIE),
            user_agent=request.headers.get("user-agent", ""),
        )
        response = RedirectResponse(service.auth.config.public_url, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=service.auth.config.session_ttl_seconds,
            path="/",
            secure=service.auth.config.secure_cookies,
            httponly=True,
            samesite="lax",
        )
    except HTTPException as failure:
        # Keep provider codes, state, and error_description out of the response.
        response = JSONResponse({"detail": failure.detail}, status_code=failure.status_code)
    response.delete_cookie(
        FLOW_COOKIE,
        path="/api/auth",
        secure=service.auth.config.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@auth_router.post("/auth/logout", status_code=204)
async def logout(request: Request, service: Service):
    # Even an expired/revoked cookie must be clearable. Origin still prevents logout CSRF.
    service.auth.assert_same_origin(request.headers.get("origin"))
    await service.auth.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response, service)
    return response


@auth_router.get("/me", response_model=UserView)
async def me(actor: Actor, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return actor


@auth_router.get("/auth/sessions", response_model=list[AuthSessionView])
async def auth_sessions(request: Request, service: Service, response: Response, actor: Actor):
    response.headers["Cache-Control"] = "no-store"
    return await service.auth.sessions.list(request.cookies.get(SESSION_COOKIE))


@auth_router.delete("/auth/sessions", status_code=204)
async def revoke_all_sessions(request: Request, service: Service, actor: Actor):
    await service.auth.sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response, service)
    return response


@auth_router.delete("/auth/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: UUID, request: Request, service: Service, actor: Actor):
    current = await service.auth.sessions.revoke(
        request.cookies.get(SESSION_COOKIE),
        str(session_id),
    )
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    if current:
        clear_session_cookie(response, service)
    return response


@router.get("/workspace")
async def workspace(service: Service, actor: Actor):
    return await service.workspace(actor)


@router.get("/capabilities")
async def capabilities(service: Service, actor: Actor):
    return await service.capabilities()


class SessionRequest(Contract):
    title: str = Field(default="New chat", min_length=1, max_length=80)


@router.post("/sessions", response_model=Session, status_code=201)
async def create_session(body: SessionRequest, service: Service, actor: Actor):
    return await action(service.chat.create_session(actor, body.title))


@router.patch("/sessions/{session_id}", response_model=Session)
async def update_session(session_id: UUID, body: SessionRequest, service: Service, actor: Actor):
    return await action(service.chat.update_session(str(session_id), body.title))


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID, service: Service, actor: Actor):
    await action(service.chat.update_session(str(session_id), delete=True))
    return Response(status_code=204)


class MessageRequest(Contract):
    content: str = Field(min_length=1, max_length=10000)


@router.post("/sessions/{session_id}/messages", response_model=Message, status_code=202)
async def send_message(
    session_id: UUID, body: MessageRequest, key: Key, service: Service, actor: Actor
):
    return await action(service.chat.send(actor, str(session_id), body.content, str(key)))


@router.patch("/tasks/{proposal_id}", response_model=Proposal)
async def edit_proposal(proposal_id: UUID, body: ProposalEdit, service: Service, actor: Actor):
    return await action(service.proposals.edit(str(proposal_id), body, actor))


@router.post("/tasks/{proposal_id}/decisions", response_model=Proposal)
async def decide_proposal(
    proposal_id: UUID, body: DecisionRequest, key: Key, service: Service, actor: Actor
):
    return await action(service.proposals.decide(str(proposal_id), body, actor, str(key)))


@router.post("/tasks/{proposal_id}/retries", response_model=Proposal)
async def retry_proposal(proposal_id: UUID, service: Service, actor: Actor):
    return await action(service.proposals.retry(str(proposal_id), actor))


class AutomationPatch(Contract):
    enabled: bool | None = None
    request: str | None = Field(default=None, min_length=10, max_length=4000)
    employee_min: int | None = Field(default=None, ge=1, le=100000)
    employee_max: int | None = Field(default=None, ge=1, le=100000)
    fit_threshold: int | None = Field(default=None, ge=0, le=100)
    daily_enrichments: int | None = Field(default=None, ge=1, le=100)
    daily_discoveries: int | None = Field(default=None, ge=1, le=100)


@router.patch("/automation")
async def update_automation(body: AutomationPatch, service: Service, actor: Actor):
    return await action(service.update_automation(body.model_dump(exclude_none=True)))


@router.post("/jobs/{job_id}/cancellation")
async def cancel_job(job_id: UUID, service: Service, actor: Actor):
    return await action(service.scheduler.cancel(str(job_id)))


@router.post("/jobs/{job_id}/retries", status_code=202)
async def retry_job(job_id: UUID, service: Service, actor: Actor):
    return await action(service.scheduler.retry(str(job_id)))
