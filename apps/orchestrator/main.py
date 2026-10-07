"""Internal LangGraph coordinator host."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from apps.orchestrator.agents.research import ResearchAgentClient
from apps.orchestrator.coordinator import Coordinator
from apps.orchestrator.graph.workflow import build_graph
from apps.orchestrator.integrations.models import RespanModels, initialize_tracing
from apps.orchestrator.routes import router
from apps.orchestrator.storage.factory import data_directory, open_storage


@asynccontextmanager
async def lifespan(application: FastAPI):
    telemetry = initialize_tracing()
    agent = ResearchAgentClient()
    try:
        async with open_storage() as (repository, checkpointer):
            models = RespanModels()
            graph = build_graph(repository, models, agent, checkpointer)
            service = Coordinator(repository, graph, models, agent, data_directory())
            application.state.coordinator = service
            application.state.telemetry = telemetry
            await service.recover()
            try:
                yield
            finally:
                await service.shutdown()
    finally:
        await agent.close()


app = FastAPI(title="Toir Coordinator", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)


class Probe(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["orchestrator"] = "orchestrator"


@app.get("/health", response_model=Probe)
async def health() -> Probe:
    return Probe()


@app.get("/ready", response_model=Probe)
async def ready() -> Probe:
    return Probe()
