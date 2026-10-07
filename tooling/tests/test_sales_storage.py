"""The sales contract runs unchanged against SQLite and isolated test Postgres."""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from apps.orchestrator.models.research import Brief
from apps.orchestrator.sales.store import SalesStore
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.postgres import PostgresRunRepository
from apps.orchestrator.storage.sqlite import SQLiteRunRepository


@pytest.fixture(params=["sqlite", "postgres"])
def open_sales_repository(request):
    if request.param == "sqlite":
        yield SQLiteRunRepository.open
        return
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for the sales Postgres contract")
    if conninfo_to_dict(url).get("dbname") != "toir_runs_test":
        pytest.fail("TEST_DATABASE_URL must use the isolated toir_runs_test database")
    schema = f"sales_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped_url = make_conninfo(url, options=f"-c search_path={schema}")
        yield lambda path: PostgresRunRepository.open(scoped_url)
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_sales_persists_and_does_not_replace_research(tmp_path, open_sales_repository):
    async def scenario():
        path = tmp_path / "runs.sqlite"
        repo = await open_sales_repository(path)
        try:
            run = await repo.create(Brief(request="Find companies investing in AI"), "research")
            store = SalesStore(repo)
            assert store.connection is repo.connection
            assert store.lock is repo.lock
            assert store.is_postgres == isinstance(repo, PostgresRunRepository)
            await store.setup()
            await store.setup()
            async with store.transaction() as tx:
                await tx.put("job", "job-1", {"id": "job-1", "status": "queued"})
                await tx.put("session", "session-1", {"title": "Research"})
                assert (await tx.get("job", "job-1"))["workspace_id"] == "toir"
                assert len(await tx.list("job")) == 1
                assert await tx.get("job", "absent") is None
            assert (await repo.get(run.id)).id == run.id
            with pytest.raises(Conflict, match="Another research run"):
                await repo.create(Brief(request="Find other companies investing in AI"), "second")
        finally:
            await repo.close()
        reopened = await open_sales_repository(path)
        try:
            async with SalesStore(reopened).transaction() as tx:
                assert (await tx.get("job", "job-1"))["status"] == "queued"
                await tx.delete("job", "job-1")
            async with SalesStore(reopened).transaction() as tx:
                assert await tx.list("job") == []
            assert (await reopened.active()).id == run.id
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_transactions_rollback_and_close_on_error_and_cancellation(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store = SalesStore(repo)
            await store.setup()
            for error in (ValueError("failure"), asyncio.CancelledError()):
                with pytest.raises(type(error)):
                    async with store.transaction() as tx:
                        await tx.put("job", "rolled-back", {"status": "queued"})
                        raise error
                with pytest.raises(RuntimeError, match="no longer active"):
                    await tx.get("job", "rolled-back")
                async with store.transaction() as tx:
                    assert await tx.get("job", "rolled-back") is None
            async with store.transaction() as tx:
                await tx.put("job", "committed", {"status": "queued"})
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_workspace_allowlist_and_immutable_audit(tmp_path, open_sales_repository):
    async def scenario():
        repo = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            store = SalesStore(repo)
            await store.setup()
            async with store.transaction() as tx:
                with pytest.raises(ValueError, match="Unsupported"):
                    await tx.get("proposal; DROP TABLE runs", "any")
                with pytest.raises(ValueError, match="non-empty"):
                    await tx.put("job", "", {})
                with pytest.raises(ValueError, match="workspace"):
                    await tx.put("job", "foreign", {"workspace_id": "other"})
                for kind in ("decision", "proposal_revision"):
                    await tx.put(kind, "immutable", {"decision": "approved"})
                    await tx.put(kind, "immutable", {"decision": "approved"})
                    with pytest.raises(Conflict, match="immutable"):
                        await tx.put(kind, "immutable", {"decision": "denied"})
                    with pytest.raises(Conflict, match="immutable"):
                        await tx.delete(kind, "immutable")
                    assert (await tx.get(kind, "immutable"))["decision"] == "approved"
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_transactions_serialize_across_connections(tmp_path, open_sales_repository):
    async def scenario():
        first = await open_sales_repository(tmp_path / "runs.sqlite")
        second = await open_sales_repository(tmp_path / "runs.sqlite")
        try:
            stores = [SalesStore(first), SalesStore(second)]
            await asyncio.gather(*(store.setup() for store in stores))

            async def increment(store):
                async with store.transaction() as tx:
                    record = await tx.get("automation", "counter") or {"count": 0}
                    await asyncio.sleep(0.002)
                    record["count"] += 1
                    await tx.put("automation", "counter", record)

            await asyncio.gather(*(increment(stores[i % 2]) for i in range(12)))
            async with stores[0].transaction() as tx:
                assert (await tx.get("automation", "counter"))["count"] == 12
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())
