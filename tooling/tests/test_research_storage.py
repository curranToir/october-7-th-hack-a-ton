"""Durable repository contract; run these against any replacement storage adapter."""

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import aiosqlite
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from apps.orchestrator.models.research import (
    Brief,
    Citation,
    Lead,
    ResearchPlan,
    ResearchReport,
    Signal,
    Source,
)
from apps.orchestrator.storage.ports import Conflict
from apps.orchestrator.storage.postgres import PostgresRunRepository
from apps.orchestrator.storage.sqlite import SQLiteRunRepository


@pytest.fixture(scope="session")
def postgres_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the Postgres storage contract")
    if conninfo_to_dict(url).get("dbname") != "toir_runs_test":
        pytest.fail("TEST_DATABASE_URL must point to the dedicated toir_runs_test database")
    # The scoped app role cannot CREATE DATABASE; isolate this session in a schema.
    schema = f"storage_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(url, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture(params=["sqlite", "postgres"])
def storage_backend(request):
    return request.param


@pytest.fixture
def open_repository(storage_backend, request):
    if storage_backend == "sqlite":
        return SQLiteRunRepository.open
    url = request.getfixturevalue("postgres_url")
    with psycopg.connect(url, autocommit=True) as connection:
        if connection.execute("SELECT to_regclass('runs')").fetchone()[0]:
            connection.execute("TRUNCATE runs, run_events RESTART IDENTITY")
    return lambda path: PostgresRunRepository.open(url)



def brief(request="Find US manufacturers investing in AI integration"):
    return Brief(request=request)


def saved_report():
    source = Source(
        id="src_0123456789abcdef",
        url="https://example.com/news/new-cto",
        title="Example appoints a CTO",
        retrieved_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
        published_at=date(2026, 10, 1),
        text="Example has 100 US employees. Example appointed Alex as CTO on October 1, 2026.",
    )
    identity = Citation(source_id=source.id, quote="Example has 100 US employees.")
    signal = Signal(
        kind="leadership",
        claim="Example appointed Alex as CTO on October 1, 2026.",
        event_date=date(2026, 10, 1),
        citations=[Citation(source_id=source.id, quote=source.text.split(". ")[1])],
    )
    return ResearchReport(
        leads=[Lead(
            company="Example", domain="example.com", country="US", employee_count=100,
            identity_citations=[identity], decision_maker="Alex, CTO", signals=[signal],
            ai_use_case="Integrate AI into warehouse scheduling.",
            rationale="The new CTO has a mandate to modernize operations.",
            outreach_angle="Offer a scoped warehouse scheduling pilot.", fit_score=80,
        )],
        sources=[source], gaps=["No confirmed budget."], summary="One qualified company.",
    )


def test_idempotency_replays_normalized_brief_even_after_completion(tmp_path, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            original = await repository.create(brief(), "stable-key")
            normalized = brief("  Find US manufacturers investing in AI integration  ")
            assert (await repository.replay(normalized, "stable-key")).id == original.id
            assert await repository.replay(brief(), "missing-key") is None
            replay = await repository.create(normalized, "stable-key")
            assert replay.id == original.id
            assert len(await repository.events(original.id)) == 1

            original.status = "completed"
            original.report = saved_report()
            await repository.save(original)
            terminal_replay = await repository.create(brief(), "stable-key")
            assert terminal_replay.id == original.id
            assert terminal_replay.status == "completed"
            assert terminal_replay.report == original.report
            assert (await repository.replay(brief(), "stable-key")).report == original.report
            assert len(await repository.list()) == 1
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_idempotency_rejects_different_brief_or_parent(tmp_path, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            original = await repository.create(brief(), "stable-key")
            original.status = "failed"
            await repository.save(original)
            with pytest.raises(Conflict, match="different brief"):
                await repository.create(
                    brief("Find US software firms with newly hired CTOs"), "stable-key",
                )
            with pytest.raises(Conflict, match="different brief"):
                await repository.create(brief(), "stable-key", parent_id=original.id)
            with pytest.raises(Conflict, match="different brief"):
                await repository.replay(
                    brief("Find US software firms with newly hired CTOs"), "stable-key",
                )
            with pytest.raises(Conflict, match="different brief"):
                await repository.replay(brief(), "stable-key", parent_id=original.id)
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_creates_across_connections_allow_one_active_run(tmp_path, open_repository):
    """Separate connections ensure the database constraint, not only a Python lock, protects us."""
    async def scenario():
        path = tmp_path / "runs.sqlite"
        first = await open_repository(path)
        second = await open_repository(path)
        try:
            outcomes = await asyncio.gather(
                first.create(brief(), "first"), second.create(brief(), "second"),
                return_exceptions=True,
            )
            failures = [result for result in outcomes if isinstance(result, BaseException)]
            assert len(failures) == 1
            assert isinstance(failures[0], Conflict)
            rows = await first.list()
            assert len(rows) == 1
            assert (await first.active()).id == rows[0].id
            assert (await second.active()).id == rows[0].id
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("storage_backend", ["postgres"], indirect=True)
def test_postgres_concurrent_same_key_replays_committed_run(tmp_path, open_repository):
    async def scenario():
        first = await open_repository(tmp_path / "runs.sqlite")
        second = await open_repository(tmp_path / "runs.sqlite")
        try:
            original, replay = await asyncio.gather(
                first.create(brief(), "same-key"), second.create(brief(), "same-key"),
            )
            assert original.id == replay.id
            assert len(await first.list()) == 1
            assert len(await first.events(original.id)) == 1
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "interrupted"])
def test_each_terminal_state_releases_active_run_slot(tmp_path, terminal, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            original = await repository.create(brief(), "first")
            original.status = "running"
            await repository.save(original)
            with pytest.raises(Conflict, match="Another research run is active"):
                await repository.create(brief(), "second")
            original.status = terminal
            await repository.save(original)
            assert await repository.active() is None
            next_run = await repository.create(brief(), "second")
            assert next_run.id != original.id
            assert next_run.status == "queued"
            assert (await repository.active()).id == next_run.id
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_retry_gets_new_uuid_and_keeps_original_evidence_unchanged(tmp_path, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            original = await repository.create(brief(), "first")
            original.status = "interrupted"
            original.report = saved_report()
            await repository.save(original)
            before_retry = original.model_dump(mode="json")

            retry = await repository.create(original.brief, "retry", parent_id=original.id)
            assert UUID(retry.id).version == 4
            assert retry.id != original.id
            assert retry.parent_id == original.id
            assert retry.report == original.report
            assert retry.task_id is None
            assert retry.status == "queued"
            retry.report.gaps.append("New follow-up gap.")
            await repository.save(retry)
            assert (await repository.get(original.id)).model_dump(mode="json") == before_retry
            assert (await repository.create(original.brief, "retry", original.id)).id == retry.id
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_payload_events_and_list_order_survive_close_and_reopen(tmp_path, open_repository):
    async def scenario():
        path = tmp_path / "nested" / "runs.sqlite"
        repository = await open_repository(path)
        original = await repository.create(brief(), "first")
        original.status = "failed"
        original.stage = "reviewing"
        original.task_id = f"{original.id}-1"
        original.pass_number = 1
        original.plan = ResearchPlan(
            queries=["new manufacturing CTO October 2026"], focus="New CTOs",
        )
        original.report = saved_report()
        original.usage = {"input_tokens": 231, "output_tokens": 67, "tool_calls": 2}
        original.error = "Research session was interrupted."
        original.trace_id = "0123456789abcdef0123456789abcdef"
        await repository.save(original)
        await repository.event(original.id, "researching", "Found company evidence")
        await repository.event(original.id, "reviewing", "Validated the company signal")
        expected = original.model_dump(mode="json")
        second = await repository.create(brief(), "second")
        await repository.close()

        reopened = await open_repository(path)
        try:
            assert (await reopened.get(original.id)).model_dump(mode="json") == expected
            assert await reopened.get("does-not-exist") is None
            assert [run.id for run in await reopened.list()] == [second.id, original.id]
            assert [run.id for run in await reopened.list(limit=1)] == [second.id]
            assert await reopened.list(limit=0) == []
            assert len(await reopened.list(limit=-1)) == 2
            assert await reopened.events("does-not-exist") == []
            events = await reopened.events(original.id)
            assert [event.message for event in events] == [
                "Research request saved", "Found company evidence", "Validated the company signal",
            ]
            assert [event.sequence for event in events] == sorted({e.sequence for e in events})
            assert all(event.run_id == original.id for event in events)
            assert all(datetime.fromisoformat(event.created_at).utcoffset().total_seconds() == 0
                       for event in events)
            assert (await reopened.active()).id == second.id
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_event_history_keeps_latest_200_in_chronological_order(tmp_path, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            run = await repository.create(brief(), "first")
            for number in range(205):
                await repository.event(run.id, "researching", f"Progress {number}")
            events = await repository.events(run.id)
            assert len(events) == 200
            assert [event.message for event in events] == [f"Progress {n}" for n in range(5, 205)]
            assert [event.sequence for event in events] == sorted({e.sequence for e in events})
            await repository.event(run.id, "researching", "x" * 1005)
            latest = (await repository.events(run.id))[-1]
            assert latest.message == "x" * 1000
            assert latest.sequence > events[-1].sequence
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_event_cannot_reference_missing_run(tmp_path, open_repository):
    async def scenario():
        repository = await open_repository(tmp_path / "runs.sqlite")
        try:
            with pytest.raises((aiosqlite.IntegrityError, psycopg.IntegrityError)):
                await repository.event("missing-run", "researching", "Orphaned evidence")
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_backup_and_export_preserve_evidence_events_and_idempotency(tmp_path):
    from apps.orchestrator.storage.backup import export_rows, snapshot

    async def scenario():
        source = tmp_path / "source"
        backup = tmp_path / "backup"
        repository = await SQLiteRunRepository.open(source / "runs.sqlite")
        try:
            run = await repository.create(brief(), "survives-restore")
            run.status = "completed"
            run.report = saved_report()
            await repository.save(run)
            await repository.event(run.id, "completed", "Evidence report saved")
            expected_events = await repository.events(run.id)
            (source / "maintenance.json").write_text('{"enabled": true}')
            with sqlite3.connect(source / "checkpoints.sqlite") as checkpoint:
                checkpoint.execute("CREATE TABLE checkpoint_marker (run_id TEXT)")
                checkpoint.execute("INSERT INTO checkpoint_marker VALUES (?)", (run.id,))
            # Keep the writer open: snapshot must include committed WAL records.
            await asyncio.to_thread(snapshot, backup, source)
            manifest = json.loads((backup / "manifest.json").read_text())
            assert manifest["schema_version"] == 1
            assert set(manifest["checksums"]) == {"runs.sqlite", "checkpoints.sqlite"}
            for filename, digest in manifest["checksums"].items():
                assert hashlib.sha256((backup / filename).read_bytes()).hexdigest() == digest
            with sqlite3.connect(backup / "checkpoints.sqlite") as restored_checkpoint:
                assert restored_checkpoint.execute(
                    "SELECT run_id FROM checkpoint_marker",
                ).fetchone() == (run.id,)

            restored = await SQLiteRunRepository.open(backup / "runs.sqlite")
            try:
                assert (await restored.get(run.id)).model_dump() == run.model_dump()
                assert await restored.events(run.id) == expected_events
                assert (await restored.create(brief(), "survives-restore")).id == run.id
            finally:
                await restored.close()

            output = tmp_path / "export.json"
            await asyncio.to_thread(export_rows, output, source)
            exported = json.loads(output.read_text())
            assert exported["schema_version"] == 1
            assert len(exported["runs"]) == 1
            row = exported["runs"][0]
            assert row["payload"] == run.model_dump(mode="json")
            assert row["idempotency_key"] == "survives-restore"
            assert len(row["request_hash"]) == 64
            assert exported["events"] == [event.model_dump() for event in expected_events]
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_backup_refuses_maintenance_disabled_or_active_research(tmp_path):
    from apps.orchestrator.storage.backup import snapshot

    async def scenario():
        source = tmp_path / "source"
        repository = await SQLiteRunRepository.open(source / "runs.sqlite")
        try:
            await repository.create(brief(), "active")
            with pytest.raises(RuntimeError, match="Enable maintenance"):
                await asyncio.to_thread(snapshot, tmp_path / "disabled", source)
            (source / "maintenance.json").write_text('{"enabled": true}')
            with pytest.raises(RuntimeError, match="active run"):
                await asyncio.to_thread(snapshot, tmp_path / "active", source)
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_factory_checkpoint_serializer_stays_strict_when_global_default_is_permissive(
    tmp_path, monkeypatch, storage_backend, request, open_repository,
):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.serde import _msgpack
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from apps.orchestrator.storage.factory import open_storage

    monkeypatch.setenv("TOIR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if storage_backend == "postgres":
        monkeypatch.setenv("DATABASE_URL", request.getfixturevalue("postgres_url"))
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "false")
    # Simulate LangGraph having been imported before any application env setup.
    monkeypatch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", False)

    async def scenario():
        async with open_storage() as (repository, checkpointer):
            if storage_backend == "sqlite":
                assert isinstance(repository, SQLiteRunRepository)
            else:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                assert isinstance(repository, PostgresRunRepository)
                assert isinstance(checkpointer, AsyncPostgresSaver)
            assert isinstance(checkpointer.serde, JsonPlusSerializer)
            state = {"run": {"brief": brief().model_dump()}, "follow_up_queries": []}
            encoded = checkpointer.serde.dumps_typed(state)
            assert checkpointer.serde.loads_typed(encoded) == state
            # An unregistered Pydantic class must remain plain data, not be instantiated.
            custom_type = checkpointer.serde.dumps_typed(brief())
            decoded = checkpointer.serde.loads_typed(custom_type)
            assert type(decoded) is dict
            assert decoded == brief().model_dump()
            assert checkpointer.serde.pickle_fallback is False
            with pytest.raises(NotImplementedError, match="Unknown serialization type: pickle"):
                checkpointer.serde.loads_typed(("pickle", b"unused"))
            run = await repository.create(brief(), "checkpoint-run")
            config = {"configurable": {"thread_id": run.id, "checkpoint_ns": ""}}
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = state
            checkpoint["channel_versions"] = {key: "1" for key in state}
            saved_config = await checkpointer.aput(
                config, checkpoint, {"source": "update", "step": 0, "parents": {}},
                checkpoint["channel_versions"],
            )
            assert (await checkpointer.aget(saved_config))["channel_values"] == state
        async with open_storage() as (repository, checkpointer):
            assert (await repository.get(run.id)).id == run.id
            assert (await checkpointer.aget(saved_config))["channel_values"] == state

    asyncio.run(scenario())
