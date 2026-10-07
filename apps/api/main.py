"""Browser-facing application host; orchestration stays behind an internal service."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from apps.api.research_client import ResearchClient
from apps.api.research_routes import router


@asynccontextmanager
async def lifespan(application: FastAPI):
    client = ResearchClient()
    application.state.research_client = client
    try:
        yield
    finally:
        await client.close()


app = FastAPI(title="Toir API", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)


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
