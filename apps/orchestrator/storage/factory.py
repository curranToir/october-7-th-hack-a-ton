"""Database integration point. See coms/database-handoff.md before changing."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from apps.orchestrator.storage.postgres import PostgresRunRepository
from apps.orchestrator.storage.sqlite import SQLiteRunRepository


def data_directory() -> Path:
    return Path(os.environ.get("TOIR_DATA_DIR", ".deployment/data"))


@asynccontextmanager
async def open_storage():
    serde = JsonPlusSerializer(allowed_msgpack_modules=None, pickle_fallback=False)
    url = os.environ.get("DATABASE_URL")
    if url:
        repository = await PostgresRunRepository.open(url)
        try:
            async with await AsyncConnection.connect(
                url, autocommit=True, row_factory=dict_row, connect_timeout=10,
            ) as connection:
                saver = AsyncPostgresSaver(connection, serde=serde)
                await saver.setup()
                yield repository, saver
        finally:
            await repository.close()
        return
    directory = data_directory()
    repository = await SQLiteRunRepository.open(directory / "runs.sqlite")
    try:
        async with aiosqlite.connect(directory / "checkpoints.sqlite") as connection:
            saver = AsyncSqliteSaver(connection, serde=serde)
            await saver.setup()
            yield repository, saver
    finally:
        await repository.close()
