from . import config as config
import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import version
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from respan import Respan
from . import memory
from .registry import user_name, CONNECTIONS
from .scalekit_pull import pull
from .auth import bearer
from .research_api import router as research_router, ingestion_failure
from .research_contract import IngestionError
from .research_ingestion import ResearchIngestion
from .research_ledger import ResearchLedger

jobs = {}
tasks = set()

@asynccontextmanager
async def lifespan(app):
    respan = Respan()
    ingestion = ResearchIngestion(
        ResearchLedger(Path(os.environ["BRAIN_RESEARCH_LEDGER"])),
        memory.remember_research_document, memory.research_access, memory.writer_lock,
    )
    app.state.research_ingestion = ingestion
    await ingestion.open()
    try:
        async with memory.writer_lock:
            await memory.initialize()
        yield
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await ingestion.close()
        respan.flush()

app = FastAPI(title="Toir Company Brain", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

private = APIRouter(dependencies=[Depends(bearer)])
app.add_exception_handler(IngestionError, ingestion_failure)

@app.exception_handler(ValueError)
async def invalid(request: Request, error: ValueError):
    if str(error) in {"unknown_user", "unknown_dataset", "unknown_source", "empty_report"}:
        return JSONResponse(status_code=400, content={"detail": str(error)})
    return JSONResponse(status_code=500, content={"detail": "internal_error"})

@app.exception_handler(PermissionError)
async def forbidden(request: Request, error: PermissionError):
    return JSONResponse(status_code=403, content={"detail": "not_dataset_owner"})

def llm_unavailable(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(error, "status_code", None) in {402, 429} or type(error).__name__ in {"RateLimitError", "BudgetExceededError", "BudgetExhaustedError"}:
            return True
        response = getattr(error, "response", None)
        if getattr(response, "status_code", None) in {402, 429}:
            return True
        error = error.__cause__ or error.__context__
    return False

@app.exception_handler(Exception)
async def failed(request: Request, error: Exception):
    return JSONResponse(status_code=503 if llm_unavailable(error) else 500, content={"detail": "llm_unavailable" if llm_unavailable(error) else "internal_error"})

class PullRequest(BaseModel):
    as_user: str
    sources: list[Literal["slack", "github", "hubspot"]] | None = None

class RecallRequest(BaseModel):
    as_user: str
    question: str = Field(min_length=1)
    mode: Literal["answer", "context"]
    session_id: str | None = None
    top_k: int = Field(default=10, ge=1)

class PermissionRequest(BaseModel):
    owner: str
    grantee: str
    dataset: str

class ForgetRequest(BaseModel):
    as_user: str
    dataset: str | None = None

@app.get("/health")
async def health():
    return {"status": "ok", "cognee": version("cognee")}

async def pull_job(job_id, body):
    job = jobs[job_id]
    async with memory.writer_lock:
        job["status"] = "running"
        try:
            result = await pull(body.as_user, body.sources)
            job.update(result, status="done")
        except Exception as error:
            # Provider exception strings can contain secrets. Authorization links are deliberately public to the authenticated caller.
            job.update(status="error", error=str(error) if isinstance(error, RuntimeError) else "llm_unavailable" if llm_unavailable(error) else type(error).__name__)

@private.post("/pull")
async def start_pull(body: PullRequest):
    user_name(body.as_user)
    job_id = str(uuid4())
    jobs[job_id] = {"status": "queued", "items_by_source": {src: 0 for src in body.sources or CONNECTIONS}, "datasets": [], "error": None}
    task = asyncio.create_task(pull_job(job_id, body))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"job_id": job_id}

@private.get("/pull/{job_id}")
async def pull_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "unknown_job")
    return jobs[job_id]

@private.post("/recall")
async def recall(body: RecallRequest):
    user_name(body.as_user)
    # Recall writes session state; serialize it with ingestion and ACL mutations too.
    async with memory.writer_lock:
        return await memory.recall(body.as_user, body.question, body.mode, body.session_id, body.top_k)

@private.get("/access/{user}")
async def access(user: str):
    return await memory.access(user)

@private.post("/grant")
async def grant(body: PermissionRequest):
    async with memory.writer_lock:
        return await memory.grant(body.owner, body.grantee, body.dataset)

@private.post("/revoke")
async def revoke(body: PermissionRequest):
    async with memory.writer_lock:
        return await memory.revoke(body.owner, body.grantee, body.dataset)

@private.post("/forget")
async def forget(body: ForgetRequest):
    async with memory.writer_lock:
        await app.state.research_ingestion.before_forget(body.as_user, body.dataset)
        return await memory.forget(body.as_user, body.dataset)

@app.get("/graph", response_class=HTMLResponse)
async def graph(dataset: str):
    async with memory.writer_lock:
        return await memory.graph(dataset)

app.include_router(private)
app.include_router(research_router, dependencies=[Depends(bearer)])
