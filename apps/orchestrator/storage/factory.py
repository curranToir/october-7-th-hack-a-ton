"""Database integration point. See coms/database-handoff.md before changing."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from apps.orchestrator.storage.sqlite import SQLiteRunRepository


def data_directory() -> Path:
    return Path(os.environ.get("TOIR_DATA_DIR", ".deployment/data"))


@asynccontextmanager
async def open_storage():
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is set but its database adapter has not been installed")
    directory = data_directory()
    repository = await SQLiteRunRepository.open(directory / "runs.sqlite")
    try:
        async with aiosqlite.connect(directory / "checkpoints.sqlite") as connection:
            saver = AsyncSqliteSaver(
                connection,
                serde=JsonPlusSerializer(
                    allowed_msgpack_modules=None,
                    pickle_fallback=False,
                ),
            )
            await saver.setup()
            yield repository, saver
    finally:
        await repository.close()
