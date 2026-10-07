"""One coordinator owns this database. Never mount it into an agent pod."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiosqlite

from apps.orchestrator.models.research import Brief, Run, RunEvent, now
from apps.orchestrator.storage.ports import Conflict

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs((1))
 WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS run_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 created_at TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON run_events(run_id, sequence);
"""


class SQLiteRunRepository:
    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.executescript(SCHEMA)
        await connection.commit()
        return cls(connection)

    async def close(self):
        await self.connection.close()

    @staticmethod
    def request_hash(brief: Brief, parent_id: str | None) -> str:
        return hashlib.sha256(
            json.dumps(
                {"brief": brief.model_dump(), "parent_id": parent_id},
                sort_keys=True,
            ).encode()
        ).hexdigest()

    async def replay(self, brief: Brief, key: str, parent_id: str | None = None) -> Run | None:
        async with self.connection.execute(
            "SELECT request_hash, payload FROM runs WHERE idempotency_key=?",
            (key,),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing:
            if existing["request_hash"] != self.request_hash(brief, parent_id):
                raise Conflict("This request key was already used for a different brief")
            return Run.model_validate_json(existing["payload"])
        return None

    async def create(self, brief: Brief, key: str, parent_id: str | None = None) -> Run:
        digest = hashlib.sha256(
            json.dumps(
                {"brief": brief.model_dump(), "parent_id": parent_id},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with self.lock:
            async with self.connection.execute(
                "SELECT request_hash, payload FROM runs WHERE idempotency_key=?",
                (key,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise Conflict("This request key was already used for a different brief")
                return Run.model_validate_json(existing["payload"])
            timestamp = now()
            run = Run(
                id=str(uuid4()),
                parent_id=parent_id,
                status="queued",
                stage="queued",
                brief=brief,
                created_at=timestamp,
                updated_at=timestamp,
                deadline_at=(
                    datetime.now(UTC)
                    + timedelta(
                        seconds=brief.deadline_seconds,
                    )
                ).isoformat(),
            )
            if parent_id:
                parent = await self.get(parent_id)
                if parent:
                    run.report = parent.report
            try:
                await self.connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                    (run.id, key, digest, run.status, timestamp, run.model_dump_json()),
                )
                await self.connection.commit()
            except aiosqlite.IntegrityError as error:
                await self.connection.rollback()
                raise Conflict(
                    "Another research run is active. Wait or cancel it first."
                ) from error
        await self.event(run.id, "queued", "Research request saved")
        return run

    async def get(self, run_id: str) -> Run | None:
        async with self.connection.execute(
            "SELECT payload FROM runs WHERE id=?",
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return Run.model_validate_json(row[0]) if row else None

    async def list(self, limit: int = 50) -> list[Run]:
        async with self.connection.execute(
            "SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return [Run.model_validate_json(row[0]) for row in await cursor.fetchall()]

    async def active(self) -> Run | None:
        async with self.connection.execute(
            "SELECT payload FROM runs WHERE status IN ('queued', 'running')",
        ) as cursor:
            row = await cursor.fetchone()
        return Run.model_validate_json(row[0]) if row else None

    async def save(self, run: Run) -> None:
        run.updated_at = now()
        async with self.lock:
            await self.connection.execute(
                "UPDATE runs SET status=?, payload=? WHERE id=?",
                (run.status, run.model_dump_json(), run.id),
            )
            await self.connection.commit()

    async def event(self, run_id: str, stage: str, message: str):
        async with self.lock:
            await self.connection.execute(
                "INSERT INTO run_events(run_id,created_at,stage,message) VALUES (?,?,?,?)",
                (run_id, now(), stage, message[:1000]),
            )
            await self.connection.commit()

    async def events(self, run_id: str) -> list[RunEvent]:
        async with self.connection.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY sequence DESC LIMIT 200",
            (run_id,),
        ) as cursor:
            return [RunEvent(**dict(row)) for row in reversed(await cursor.fetchall())]
