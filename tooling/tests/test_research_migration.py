import asyncio
import copy

import pytest
from test_research_storage import postgres_url, saved_report  # noqa: F401

from apps.orchestrator.models.research import Brief
from apps.orchestrator.storage.sqlite import SQLiteRunRepository
from tooling.migrate_research import digest, export_sqlite, import_postgres, validate


@pytest.fixture
def migration_bundle(tmp_path):
    async def setup():
        repo = await SQLiteRunRepository.open(tmp_path / "runs.sqlite")
        try:
            run = await repo.create(Brief(request="Find companies with a new CTO"), "migration-key")
            run.report = saved_report()
            run.status = "completed"
            run.stage = "complete"
            await repo.save(run)
            await repo.event(run.id, "complete", "Evidence accepted")
        finally:
            await repo.close()

    asyncio.run(setup())
    return export_sqlite(tmp_path / "runs.sqlite", tmp_path / "export.json")


def test_export_preserves_complete_evidence_ids_hashes_and_order(migration_bundle, tmp_path):
    assert validate(migration_bundle) == migration_bundle
    run = migration_bundle["runs"][0]
    assert run["payload"]["report"]["sources"][0]["text"] == saved_report().sources[0].text
    assert [row["sequence"] for row in migration_bundle["events"]] == [1, 2]
    assert (tmp_path / "export.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="overwrite"):
        export_sqlite(tmp_path / "runs.sqlite", tmp_path / "export.json")


@pytest.mark.parametrize("change", ["checksum", "active", "request_hash", "orphan", "duplicate"])
def test_invalid_migration_never_reaches_import(migration_bundle, change):
    bundle = copy.deepcopy(migration_bundle)
    if change == "checksum":
        bundle["sha256"] = "0" * 64
    elif change == "active":
        bundle["runs"][0]["status"] = bundle["runs"][0]["payload"]["status"] = "running"
    elif change == "request_hash":
        bundle["runs"][0]["request_hash"] = "0" * 64
    elif change == "orphan":
        bundle["events"][0]["run_id"] = "missing"
    else:
        bundle["runs"].append(copy.deepcopy(bundle["runs"][0]))
    if change != "checksum":
        bundle["sha256"] = digest({"runs": bundle["runs"], "events": bundle["events"]})
    with pytest.raises(ValueError):
        validate(bundle)


def test_import_preserves_hashes_and_rejects_second_import(migration_bundle, request):
    import psycopg

    url = request.getfixturevalue("postgres_url")
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('runs')").fetchone()[0]:
            conn.execute("TRUNCATE runs,run_events RESTART IDENTITY")

    async def scenario():
        result = await import_postgres(migration_bundle, url)
        assert result == {"runs": 1, "events": 2, "sha256": migration_bundle["sha256"]}
        with pytest.raises(ValueError, match="Destination has research"):
            await import_postgres(migration_bundle, url)
        from apps.orchestrator.storage.postgres import PostgresRunRepository

        repo = await PostgresRunRepository.open(url)
        try:
            run = await repo.get(migration_bundle["runs"][0]["id"])
            assert run.report == saved_report()
            await repo.event(run.id, "verified", "Migration checked")
            assert (await repo.events(run.id))[-1].sequence == 3
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_live_backup_module_cannot_export_stale_sqlite_when_postgres_active(tmp_path, monkeypatch):
    from apps.orchestrator.storage.backup import export_rows, snapshot

    monkeypatch.setenv("DATABASE_URL", "postgresql://never-printed")
    for function in (export_rows, snapshot):
        with pytest.raises(RuntimeError, match="Postgres is active"):
            function(tmp_path / "should-not-exist")
    assert not (tmp_path / "should-not-exist").exists()
