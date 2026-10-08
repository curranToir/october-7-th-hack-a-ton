"""Coordinator-owned sales records sharing the research repository connection.

A transaction holds both the repository's local lock and a database-level lock.
Do not call research repository methods inside it: those share the same lock.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from psycopg.types.json import Jsonb

from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.postgres import PostgresRunRepository

WORKSPACE = "toir"
KINDS = frozenset(
    {
        "member",
        "session",
        "message",
        "automation",
        "job",
        "proposal",
        "proposal_revision",
        "decision",
        "crm_operation",
        "outbox",
        "auth_session",
        "auth_flow",
        "identity",
        "suppression",
    }
)
IMMUTABLE_KINDS = frozenset({"decision", "proposal_revision"})
# Different from the research startup-DDL lock. Shared by all sales transactions.
SALES_LOCK = 724018


def schema(postgres: bool) -> str:
    payload_type = "JSONB" if postgres else "TEXT"
    kind_values = ", ".join(f"'{kind}'" for kind in sorted(KINDS))
    return f"""
CREATE TABLE IF NOT EXISTS sales_schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sales_records (
 workspace_id TEXT NOT NULL CHECK (workspace_id = 'toir'),
 kind TEXT NOT NULL CHECK (kind IN ({kind_values})),
 id TEXT NOT NULL,
 payload {payload_type} NOT NULL,
 PRIMARY KEY (workspace_id, kind, id)
);
INSERT INTO sales_schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
"""


class SalesStore:
    """Additive store; opening or closing the underlying connection is its owner's job."""

    def __init__(self, run_repository):
        self.connection = run_repository.connection
        self.lock = run_repository.lock
        self.is_postgres = isinstance(run_repository, PostgresRunRepository)

    async def setup(self) -> None:
        async with self.transaction():
            # executescript implicitly commits on SQLite; individual DDL preserves atomicity.
            for statement in schema(self.is_postgres).split(";"):
                if statement.strip():
                    await self.connection.execute(statement)

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            tx = SalesTransaction(self.connection, self.is_postgres)
            try:
                if self.is_postgres:
                    async with self.connection.transaction():
                        await self.connection.execute(
                            "SELECT pg_advisory_xact_lock(%s)", (SALES_LOCK,)
                        )
                        yield tx
                else:
                    await self.connection.execute("BEGIN IMMEDIATE")
                    try:
                        yield tx
                        await self.connection.commit()
                    except BaseException:
                        await self.connection.rollback()
                        raise
            finally:
                tx.active = False


class SalesTransaction:
    def __init__(self, connection, postgres: bool):
        self.connection = connection
        self.postgres = postgres
        self.active = True

    def _check(self, kind: str, record_id: str | None = None) -> None:
        if not self.active:
            raise RuntimeError("Sales transaction is no longer active")
        if kind not in KINDS:
            raise ValueError("Unsupported sales record kind")
        if record_id is not None and (not isinstance(record_id, str) or not record_id.strip()):
            raise ValueError("Sales record ID must be a non-empty string")

    async def _execute(self, query: str, parameters: tuple):
        if self.postgres:
            query = query.replace("?", "%s")
        return await self.connection.execute(query, parameters)

    def _payload(self, row) -> dict[str, Any]:
        value = row["payload"]
        payload = value if self.postgres else json.loads(value)
        if payload.get("workspace_id") != WORKSPACE:
            raise ValueError("Stored record has an invalid workspace")
        return payload

    async def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        self._check(kind, record_id)
        cursor = await self._execute(
            "SELECT payload FROM sales_records WHERE workspace_id=? AND kind=? AND id=?",
            (WORKSPACE, kind, record_id),
        )
        try:
            row = await cursor.fetchone()
            return self._payload(row) if row else None
        finally:
            await cursor.close()

    async def list(self, kind: str) -> list[dict[str, Any]]:
        return [payload for _, payload in await self.entries(kind)]

    async def entries(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        self._check(kind)
        cursor = await self._execute(
            "SELECT id,payload FROM sales_records WHERE workspace_id=? AND kind=? ORDER BY id",
            (WORKSPACE, kind),
        )
        try:
            return [(row["id"], self._payload(row)) for row in await cursor.fetchall()]
        finally:
            await cursor.close()

    async def put(self, kind: str, record_id: str, payload: dict[str, Any]) -> None:
        self._check(kind, record_id)
        data = dict(payload)
        data.setdefault("workspace_id", WORKSPACE)
        if data["workspace_id"] != WORKSPACE:
            raise ValueError("Sales records must belong to the Toir workspace")
        if kind in IMMUTABLE_KINDS:
            existing = await self.get(kind, record_id)
            if existing is not None:
                if existing != data:
                    raise Conflict(f"{kind} records are immutable")
                return
        encoded = Jsonb(data) if self.postgres else json.dumps(data, allow_nan=False)
        cursor = await self._execute(
            "INSERT INTO sales_records (workspace_id,kind,id,payload) VALUES (?,?,?,?) "
            "ON CONFLICT (workspace_id,kind,id) DO UPDATE SET payload=excluded.payload",
            (WORKSPACE, kind, record_id, encoded),
        )
        await cursor.close()

    async def delete(self, kind: str, record_id: str) -> None:
        self._check(kind, record_id)
        if kind in IMMUTABLE_KINDS:
            raise Conflict(f"{kind} records are immutable")
        cursor = await self._execute(
            "DELETE FROM sales_records WHERE workspace_id=? AND kind=? AND id=?",
            (WORKSPACE, kind, record_id),
        )
        await cursor.close()
