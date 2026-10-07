from dataclasses import asdict
import json
from pathlib import Path
import shutil
from typing import Annotated

import httpx
import psycopg
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag.answer import AnswerNotConfigured, answer
from rag.config import settings, PROJECT_ROOT
from rag.db import connect
from rag.ingest import SUPPORTED_SUFFIXES, ingest_path, ingest_text
from rag.models import ModelServiceError
from rag.search import search

app = FastAPI(title="oct7 RAG")


class TextRequest(BaseModel):
    text: str
    source: str
    title: str | None = None
    metadata: dict | None = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, gt=0)
    candidates: int = Field(default=50, gt=0)
    rerank: bool = True


class AnswerRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, gt=0)


@app.exception_handler(ModelServiceError)
def model_service_error(request: Request, error: ModelServiceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(error)})


@app.exception_handler(AnswerNotConfigured)
def answer_not_configured(request: Request, error: AnswerNotConfigured) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.exception_handler(ValueError)
def invalid_input(request: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.get("/health")
def health() -> dict[str, bool]:
    status = {"db": False, "embed": False, "rerank": False}
    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=3,
            options="-c statement_timeout=3000",
        ) as conn:
            conn.execute("SELECT 1")
        status["db"] = True
    except (psycopg.Error, ValueError):
        pass
    urls = {
        "embed": f"{settings.embed_base_url.rstrip('/')}/models",
        "rerank": f"{settings.rerank_base_url.rstrip('/')}/v1/models",
    }
    for service, url in urls.items():
        try:
            response = httpx.get(url, timeout=3)
            response.raise_for_status()
            status[service] = True
        except (httpx.HTTPError, httpx.InvalidURL):
            pass
    return status


@app.post("/ingest/text")
def ingest_text_route(body: TextRequest) -> dict:
    with connect() as conn:
        result = ingest_text(conn, body.text, body.source, body.title, body.metadata)
    return asdict(result)


@app.post("/ingest/file")
def ingest_file_route(
    file: Annotated[UploadFile, File()],
    metadata: Annotated[str | None, Form()] = None,
) -> dict:
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail="unsupported file type")
    parsed_metadata = None
    if metadata is not None:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail="metadata must be valid JSON") from error
        if parsed_metadata is not None and not isinstance(parsed_metadata, dict):
            raise HTTPException(status_code=400, detail="metadata must be a JSON object or null")
    path = PROJECT_ROOT / "data" / "uploads" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as destination:
        shutil.copyfileobj(file.file, destination)
    with connect() as conn:
        result = ingest_path(conn, path, parsed_metadata)[0]
    return asdict(result)


@app.post("/search")
def search_route(body: SearchRequest) -> dict:
    with connect() as conn:
        hits = search(conn, body.query, body.top_k, body.candidates, body.rerank)
    return {"hits": [asdict(hit) for hit in hits]}


@app.post("/answer")
def answer_route(body: AnswerRequest) -> dict:
    if not settings.answer_base_url or not settings.answer_model:
        raise AnswerNotConfigured(
            "answer endpoint not configured: set ANSWER_BASE_URL/ANSWER_MODEL"
        )
    with connect() as conn:
        hits = search(conn, body.query, top_k=body.top_k)
    return {"answer": answer(body.query, hits), "hits": [asdict(hit) for hit in hits]}


@app.get("/documents")
def documents() -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT d.id, d.source, d.title, d.metadata, d.created_at, "
            "count(c.id) AS chunks FROM documents d "
            "LEFT JOIN chunks c ON c.document_id = d.id "
            "GROUP BY d.id ORDER BY d.id"
        ).fetchall()


@app.delete("/documents/{id}", status_code=204)
def delete_document(id: int) -> Response:
    with connect() as conn:
        deleted = conn.execute(
            "DELETE FROM documents WHERE id = %s RETURNING id", (id,)
        ).fetchone()
        if deleted is None:
            raise HTTPException(status_code=404, detail="document not found")
    return Response(status_code=204)
