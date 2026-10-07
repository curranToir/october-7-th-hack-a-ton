"""Authentication redirects/cookies are issued by the coordinator, unchanged here."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from apps.api.sales_client import SalesClient, get_sales_client

router = APIRouter(prefix="/api", tags=["identity"])
Client = Annotated[SalesClient, Depends(get_sales_client)]


@router.get("/auth/login")
async def login(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/auth/login")


@router.get("/auth/callback")
async def callback(
    request: Request,
    client: Client,
    code: Annotated[str | None, Query(max_length=8192)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    params = {
        key: value
        for key, value in {"code": code, "state": state, "error": error}.items()
        if value is not None
    }
    return await client.forward(request, "/v1/auth/callback", params=params)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/auth/logout")


@router.get("/me")
async def current_user(request: Request, client: Client) -> Response:
    return await client.forward(request, "/v1/me")
