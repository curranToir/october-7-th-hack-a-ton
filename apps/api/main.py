"""Browser-facing application host. Business capabilities are intentionally absent."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Company Brain API", docs_url=None, redoc_url=None, openapi_url=None)


class Probe(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"


@app.get("/api/health", response_model=Probe)
async def health() -> Probe:
    return Probe()


@app.get("/api/ready", response_model=Probe)
async def ready() -> Probe:
    # No external dependencies exist in this scaffold.
    return Probe()
