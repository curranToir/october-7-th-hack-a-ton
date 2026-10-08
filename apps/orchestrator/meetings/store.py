"""Additive meeting storage on the coordinator's existing SQLite/Postgres database."""

import json
from contextlib import asynccontextmanager

from psycopg.types.json import Jsonb

from apps.orchestrator.sales.store import SalesStore

KINDS = {"meeting", "task", "event", "decision"}


class MeetingStore:
    def __init__(self, repository):
        # Reuse the database transaction/advisory lock, not the sales record schema.
        self.database = SalesStore(repository)

    async def setup(self):
        datatype = "JSONB" if self.database.is_postgres else "TEXT"
        async with self.database.transaction() as tx:
            await tx._execute(
                f"CREATE TABLE IF NOT EXISTS meeting_records ("
                f"kind TEXT NOT NULL, id TEXT NOT NULL, payload {datatype} NOT NULL, "
                "PRIMARY KEY(kind,id))",
                (),
            )

    @asynccontextmanager
    async def transaction(self):
        async with self.database.transaction() as tx:
            yield MeetingTransaction(tx)

    async def get(self, kind, identifier):
        async with self.transaction() as tx:
            return await tx.get(kind, identifier)

    async def list(self, kind):
        async with self.transaction() as tx:
            return await tx.list(kind)


class MeetingTransaction:
    def __init__(self, transaction):
        self.tx = transaction

    def check(self, kind):
        if kind not in KINDS or not self.tx.active:
            raise ValueError("Invalid meeting record transaction")

    def decode(self, row):
        return row["payload"] if self.tx.postgres else json.loads(row["payload"])

    async def get(self, kind, identifier):
        self.check(kind)
        cursor = await self.tx._execute(
            "SELECT payload FROM meeting_records WHERE kind=? AND id=?",
            (kind, identifier),
        )
        try:
            row = await cursor.fetchone()
            return self.decode(row) if row else None
        finally:
            await cursor.close()

    async def list(self, kind):
        self.check(kind)
        cursor = await self.tx._execute(
            "SELECT payload FROM meeting_records WHERE kind=? ORDER BY id",
            (kind,),
        )
        try:
            return [self.decode(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()

    async def put(self, kind, identifier, payload):
        self.check(kind)
        if kind == "decision" and await self.get(kind, identifier):
            raise ValueError("Meeting decisions are immutable")
        encoded = Jsonb(payload) if self.tx.postgres else json.dumps(payload, allow_nan=False)
        cursor = await self.tx._execute(
            "INSERT INTO meeting_records (kind,id,payload) VALUES (?,?,?) "
            "ON CONFLICT (kind,id) DO UPDATE SET payload=excluded.payload",
            (kind, identifier, encoded),
        )
        await cursor.close()
