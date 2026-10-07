import hashlib
from dataclasses import dataclass
from pathlib import Path

from pgvector import HalfVector
from psycopg.types.json import Jsonb
from pypdf import PdfReader

from rag.chunk import chunk_text
from rag.models import embed_passages

SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}


@dataclass
class IngestResult:
    source: str
    document_id: int
    chunks: int
    skipped: bool


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported file type: {suffix}")
    if suffix == ".pdf":
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def ingest_text(conn, text: str, source: str, title: str | None = None, metadata: dict | None = None) -> IngestResult:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with conn.transaction():
        existing = conn.execute(
            "SELECT id, (SELECT count(*) FROM chunks WHERE document_id = documents.id) AS chunks "
            "FROM documents WHERE content_sha256 = %s",
            (digest,),
        ).fetchone()
        if existing:
            return IngestResult(source, existing["id"], existing["chunks"], True)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("no text extracted")
        embeddings = embed_passages(chunks)
        if title is None:
            title = next(line.strip() for line in text.splitlines() if line.strip())[:200]
        metadata = dict(metadata or {})
        document = conn.execute(
            "INSERT INTO documents (source, title, metadata, content_sha256) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (content_sha256) DO NOTHING RETURNING id",
            (source, title, Jsonb(metadata), digest),
        ).fetchone()
        if document is None:
            existing = conn.execute(
                "SELECT id, (SELECT count(*) FROM chunks WHERE document_id = documents.id) AS chunks "
                "FROM documents WHERE content_sha256 = %s",
                (digest,),
            ).fetchone()
            return IngestResult(source, existing["id"], existing["chunks"], True)
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO chunks (document_id, ord, content, metadata, embedding) VALUES (%s, %s, %s, %s, %s)",
                (
                    (document["id"], ordinal, content, Jsonb(metadata), HalfVector(vector))
                    for ordinal, (content, vector) in enumerate(zip(chunks, embeddings, strict=True))
                ),
            )
        return IngestResult(source, document["id"], len(chunks), False)


def ingest_path(conn, path: Path, metadata: dict | None = None) -> list[IngestResult]:
    paths = (
        sorted(file for file in path.rglob("*") if file.is_file() and file.suffix.lower() in SUPPORTED_SUFFIXES)
        if path.is_dir()
        else [path]
    )
    return [
        ingest_text(
            conn,
            extract_text(file),
            source=str(file),
            metadata={"filename": file.name, **(metadata or {})},
        )
        for file in paths
    ]
