"""Postgres run state; only the coordinator owns a connection and writes here."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg import AsyncConnection, IntegrityError
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.orchestrator.models.research import Brief, Run, RunEvent, now
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.sqlite import SQLiteRunRepository

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs((1))
 WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS run_events (
 sequence BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 created_at TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON run_events(run_id, sequence);
"""


class PostgresRunRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, url: str):
        settings = conninfo_to_dict(url)
        host = settings.get("host", "")
        if host and host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith("/"):
            if settings.get("sslmode") != "verify-full":
                raise ValueError("Remote DATABASE_URL requires sslmode=verify-full")
        connection = await AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row, connect_timeout=10,
        )
        try:
            async with connection.transaction():
                # Serialize startup DDL across independently opened repositories.
                await connection.execute("SELECT pg_advisory_xact_lock(724017)")
                await connection.execute(SCHEMA)
        except BaseException:
            await connection.close()
            raise
        return cls(connection)

    async def close(self):
        await self.connection.close()

    request_hash = staticmethod(SQLiteRunRepository.request_hash)

    async def replay(self, brief: Brief, key: str, parent_id: str | None = None) -> Run | None:
        async with self.lock:
            return await self._replay(brief, key, parent_id)

    async def _replay(self, brief: Brief, key: str, parent_id: str | None) -> Run | None:
        cursor = await self.connection.execute(
            "SELECT request_hash, payload FROM runs WHERE idempotency_key=%s", (key,),
        )
        existing = await cursor.fetchone()
        if existing:
            if existing["request_hash"] != self.request_hash(brief, parent_id):
                raise Conflict("This request key was already used for a different brief")
            return Run.model_validate(existing["payload"])
        return None

    async def create(self, brief: Brief, key: str, parent_id: str | None = None) -> Run:
        async with self.lock:
            try:
                async with self.connection.transaction():
                    existing = await self._replay(brief, key, parent_id)
                    if existing:
                        return existing
                    timestamp = now()
                    run = Run(
                        id=str(uuid4()), parent_id=parent_id, status="queued", stage="queued",
                        brief=brief, created_at=timestamp, updated_at=timestamp,
                        deadline_at=(
                            datetime.now(UTC) + timedelta(seconds=brief.deadline_seconds)
                        ).isoformat(),
                    )
                    if parent_id:
                        cursor = await self.connection.execute(
                            "SELECT payload FROM runs WHERE id=%s", (parent_id,),
                        )
                        parent = await cursor.fetchone()
                        if parent:
                            run.report = Run.model_validate(parent["payload"]).report
                    await self.connection.execute(
                        "INSERT INTO runs VALUES (%s, %s, %s, %s, %s, %s)",
                        (run.id, key, self.request_hash(brief, parent_id), run.status,
                         timestamp, Jsonb(run.model_dump(mode="json"))),
                    )
            except IntegrityError as error:
                # A competing same-key insert may have committed while ours waited.
                existing = await self._replay(brief, key, parent_id)
                if existing:
                    return existing
                raise Conflict(
                    "Another research run is active. Wait or cancel it first."
                ) from error
        await self.event(run.id, "queued", "Research request saved")
        return run

    async def get(self, run_id: str) -> Run | None:
        async with self.lock:
            cursor = await self.connection.execute("SELECT payload FROM runs WHERE id=%s", (run_id,))
            row = await cursor.fetchone()
        return Run.model_validate(row["payload"]) if row else None

    async def list(self, limit: int = 50) -> list[Run]:
        async with self.lock:
            cursor = await self.connection.execute(
                "SELECT payload FROM runs ORDER BY created_at DESC LIMIT %s",
                (None if limit < 0 else limit,),
            )
            rows = await cursor.fetchall()
        return [Run.model_validate(row["payload"]) for row in rows]

    async def active(self) -> Run | None:
        async with self.lock:
            cursor = await self.connection.execute(
                "SELECT payload FROM runs WHERE status IN ('queued', 'running')",
            )
            row = await cursor.fetchone()
        return Run.model_validate(row["payload"]) if row else None

    async def save(self, run: Run) -> None:
        run.updated_at = now()
        async with self.lock:
            await self.connection.execute(
                "UPDATE runs SET status=%s, payload=%s WHERE id=%s",
                (run.status, Jsonb(run.model_dump(mode="json")), run.id),
            )

    async def event(self, run_id: str, stage: str, message: str):
        async with self.lock:
            await self.connection.execute(
                "INSERT INTO run_events(run_id,created_at,stage,message) VALUES (%s,%s,%s,%s)",
                (run_id, now(), stage, message[:1000]),
            )

    async def events(self, run_id: str) -> list[RunEvent]:
        async with self.lock:
            cursor = await self.connection.execute(
                "SELECT * FROM run_events WHERE run_id=%s ORDER BY sequence DESC LIMIT 200",
                (run_id,),
            )
            rows = await cursor.fetchall()
        return [RunEvent(**row) for row in reversed(rows)]
