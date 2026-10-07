"""Internal orchestration host; no agents, providers, or task execution yet."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Company Brain Orchestrator", docs_url=None, redoc_url=None, openapi_url=None)


class Probe(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["orchestrator"] = "orchestrator"


@app.get("/health", response_model=Probe)
async def health() -> Probe:
    return Probe()


@app.get("/ready", response_model=Probe)
async def ready() -> Probe:
    return Probe()
