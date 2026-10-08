"""Browser-facing application host; orchestration stays behind an internal service."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from apps.api.auth_routes import router as auth_router
from apps.api.research_client import ResearchClient
from apps.api.research_routes import router
from apps.api.sales_client import SalesClient
from apps.api.sales_routes import router as sales_router
from apps.auth_logging import protect_auth_logs


@asynccontextmanager
async def lifespan(application: FastAPI):
    protect_auth_logs()
    client = ResearchClient()
    application.state.research_client = client
    sales_client = SalesClient()
    application.state.sales_client = sales_client
    try:
        yield
    finally:
        await client.close()
        await sales_client.close()


app = FastAPI(title="Toir API", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)
app.include_router(sales_router)
app.include_router(auth_router)


class Probe(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"


@app.get("/api/health", response_model=Probe)
async def health() -> Probe:
    return Probe()


@app.get("/api/ready", response_model=Probe)
async def ready() -> Probe:
    # Process readiness is independent of provider configuration; see capabilities.
    return Probe()
