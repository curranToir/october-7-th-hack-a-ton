"""Internal LangGraph coordinator host."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from apps.auth_logging import protect_auth_logs
from apps.orchestrator.agents.research import ResearchAgentClient
from apps.orchestrator.coordinator import Coordinator
from apps.orchestrator.graph.workflow import build_graph
from apps.orchestrator.integrations.models import RespanModels, initialize_tracing
from apps.orchestrator.meetings.agent import MeetingAgentClient
from apps.orchestrator.meetings.github import GitHubIssues
from apps.orchestrator.meetings.providers import RecallClient
from apps.orchestrator.meetings.routes import router as meeting_router
from apps.orchestrator.meetings.service import MeetingService
from apps.orchestrator.meetings.store import MeetingStore
from apps.orchestrator.routes import router
from apps.orchestrator.sales.auth import AuthService
from apps.orchestrator.sales.brain import BrainClient
from apps.orchestrator.sales.contact_client import ContactAgentClient
from apps.orchestrator.sales.crm import ScalekitCRM
from apps.orchestrator.sales.crm_executor import CRMExecutor
from apps.orchestrator.sales.crm_planner import CRMPlanner
from apps.orchestrator.sales.routes import auth_router
from apps.orchestrator.sales.routes import router as sales_router
from apps.orchestrator.sales.service import SalesService
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.factory import data_directory, open_storage


@asynccontextmanager
async def lifespan(application: FastAPI):
    protect_auth_logs()
    telemetry = initialize_tracing()
    agent = ResearchAgentClient()
    try:
        async with open_storage() as (repository, checkpointer):
            models = RespanModels()
            graph = build_graph(repository, models, agent, checkpointer)
            service = Coordinator(repository, graph, models, agent, data_directory())
            application.state.coordinator = service
            application.state.telemetry = telemetry
            store = SalesStore(repository)
            auth, contacts, crm, brain = (
                AuthService(store),
                ContactAgentClient(),
                ScalekitCRM.from_env(),
                BrainClient(),
            )
            sales = SalesService(
                store,
                service,
                models,
                auth,
                contacts,
                crm,
                CRMPlanner(crm, store),
                CRMExecutor(store, crm, can_execute=lambda: not service.maintenance),
                brain,
            )
            application.state.sales_service = sales
            meetings = MeetingService(
                MeetingStore(repository),
                RecallClient(),
                MeetingAgentClient(),
                GitHubIssues(),
                service,
            )
            application.state.meeting_service = meetings
            service.meeting_service = meetings
            try:
                await sales.setup()
                await meetings.setup()
                await service.recover()
                sales.start()
                meetings.start()
                yield
            finally:
                await meetings.close()
                await sales.shutdown()
                await service.shutdown()
                await auth.close()
                await contacts.close()
                await crm.close()
                await brain.close()
    finally:
        await agent.close()


app = FastAPI(title="Toir Coordinator", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(router)
app.include_router(sales_router)
app.include_router(auth_router)
app.include_router(meeting_router)


class Probe(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["orchestrator"] = "orchestrator"


@app.get("/health", response_model=Probe)
async def health() -> Probe:
    return Probe()


@app.get("/ready", response_model=Probe)
async def ready() -> Probe:
    return Probe()
