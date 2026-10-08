"""PG backup/cutover guards plus an opt-in real isolated PostgreSQL17 round trip."""

import importlib.util
import io
import json
import os
import subprocess
import tarfile
from contextlib import nullcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "infrastructure/deployment/scripts"
spec = importlib.util.spec_from_file_location("postgres_admin", SCRIPTS / "postgres_admin.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)

import remote_apply  # noqa: E402


def test_credentials_are_environment_only_and_errors_are_redacted(monkeypatch):
    url = "postgresql://user:sensitive@localhost/db"
    calls = []

    def invoke(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, "", url)

    monkeypatch.setattr(pg.subprocess, "run", invoke)
    with pytest.raises(RuntimeError, match="output was withheld") as error:
        pg.run_client("pg_dump", ["--format=custom"], url)
    assert "sensitive" not in str(error.value)
    args, kwargs = calls[0]
    assert not any("sensitive" in str(arg) or url in str(arg) for arg in args)
    assert kwargs["env"]["PGPASSWORD"] == "sensitive"
    assert "DATABASE_URL" not in kwargs["env"]


def test_remote_postgres_requires_verified_tls():
    with pytest.raises(ValueError, match="verified TLS"):
        pg.connection_environment("postgresql://u:p@spark/db?sslmode=require")
    env = pg.connection_environment("postgresql://u:p@spark/db?sslmode=verify-full&sslrootcert=/ca")
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGSSLROOTCERT"] == "/ca"


def test_database_archive_rejects_links(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("database.dump")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    stream.seek(0)
    with pytest.raises(ValueError, match="Unexpected"):
        pg.read_archive(stream, tmp_path)


def test_configured_postgres_never_falls_back_to_offline_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_apply, "data_directory", lambda: tmp_path)
    monkeypatch.setattr(remote_apply, "coordinator_writes_database", lambda: False)
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "postgres")
    with pytest.raises(remote_apply.PostgresBackupRequired, match="stale SQLite"):
        remote_apply.backup("bucket")


def test_pg_recovery_does_not_bypass_unavailable_coordinator(monkeypatch):
    monkeypatch.setattr(remote_apply, "runtime_config", lambda: {"bucket": "b"})
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "postgres")

    def unavailable(*args, **kwargs):
        raise RuntimeError("no ready coordinator")

    monkeypatch.setattr(remote_apply, "backup", unavailable)
    with pytest.raises(remote_apply.PostgresBackupRequired):
        remote_apply.prepare_backup("b", recovery=True)


def test_pg_failed_snapshot_never_uploads_or_touches_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    changes = []
    monkeypatch.setattr(
        remote_apply, "maintenance", lambda state=None: changes.append(state) or {"enabled": False}
    )
    monkeypatch.setattr(remote_apply, "drain", lambda: True)
    monkeypatch.setattr(
        remote_apply.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1)
    )
    monkeypatch.setattr(
        remote_apply, "execute", lambda *args, **kwargs: pytest.fail("No S3 upload allowed")
    )
    monkeypatch.setattr(
        remote_apply, "snapshot_databases", lambda *args: pytest.fail("No SQLite fallback")
    )
    with pytest.raises(remote_apply.PostgresBackupRequired):
        remote_apply.backup_postgres("bucket")
    assert changes == [None, False]
    assert not (tmp_path / "latest-backup.json").exists()


def test_cutover_failure_does_not_activate_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "sqlite")
    monkeypatch.setattr(remote_apply, "data_directory", lambda: tmp_path)
    monkeypatch.setattr(remote_apply, "coordinator_writes_database", lambda: True)
    monkeypatch.setattr(remote_apply, "drain", lambda: True)
    monkeypatch.setattr(
        remote_apply, "publish_sqlite_backup", lambda *args, **kwargs: "sqlite-backup"
    )
    monkeypatch.setattr(
        remote_apply, "database_operator", lambda **kwargs: nullcontext("pod/operator")
    )
    monkeypatch.setattr(remote_apply, "execute", lambda *args, **kwargs: "")
    monkeypatch.setattr(remote_apply, "backup_postgres", lambda *args, **kwargs: "pg-backup")
    monkeypatch.setattr(remote_apply, "postgres_secret", lambda: {"DATABASE_URL": "sensitive"})
    monkeypatch.setattr(
        remote_apply.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""),
    )
    monkeypatch.setattr(
        remote_apply, "sync_secrets", lambda: pytest.fail("No activation after failed import")
    )
    with pytest.raises(RuntimeError, match="Migration failed"):
        remote_apply.storage_cutover("b")
    assert not (tmp_path / "storage.json").exists()


def test_pg_rollback_rejects_image_without_backup_support(tmp_path, monkeypatch):
    manifest = {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": "orchestrator"},
                "spec": {"template": {"metadata": {}}},
            }
        ]
    }
    (tmp_path / "manifests.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "postgres")
    monkeypatch.setattr(
        remote_apply, "execute", lambda *args, **kwargs: pytest.fail("No release mutation allowed")
    )
    with pytest.raises(remote_apply.PostgresBackupRequired, match="old release"):
        remote_apply.apply_release(tmp_path)


@pytest.mark.skipif(
    not os.environ.get("TOIR_PG17_TEST_CONTAINER"),
    reason="Requires explicitly isolated local PostgreSQL17 container",
)
def test_pg17_whole_database_roundtrip_in_container():
    """Real dump/restore covers graph binary checkpoints, row hashes and rollback."""
    script = r"""
import importlib.util, tempfile, json
from pathlib import Path
import psycopg
spec=importlib.util.spec_from_file_location('pg','/opt/toir/postgres_admin.py')
pg=importlib.util.module_from_spec(spec);spec.loader.exec_module(pg)
source='postgresql://postgres@127.0.0.1/toir_runs_test'
target='postgresql://postgres@127.0.0.1/toir_backup_scratch'
with psycopg.connect(source,autocommit=True) as c:
 c.execute('CREATE TABLE IF NOT EXISTS runs (id text primary key, payload jsonb)')
 c.execute('CREATE TABLE IF NOT EXISTS checkpoint_blobs (id text primary key, blob bytea)')
 c.execute("INSERT INTO runs VALUES ('run-1','{\"evidence\":[\"source\"]}') ON CONFLICT DO NOTHING")
 c.execute("INSERT INTO checkpoint_blobs VALUES ('thread-1',%s) ON CONFLICT DO NOTHING",
           (b'\x00\x01checkpoint',))
 c.execute('DROP DATABASE IF EXISTS toir_backup_scratch')
 c.execute('CREATE DATABASE toir_backup_scratch')
with tempfile.TemporaryDirectory() as d:
 folder=Path(d);meta=pg.snapshot(source,folder)
 assert {r['table'] for r in meta['tables']}=={'runs','checkpoint_blobs'}
 pg.restore(target,folder,meta)
 with psycopg.connect(target) as c:
  c.execute("DELETE FROM runs")
 pg.restore(target,folder,meta,replace=True)
 with psycopg.connect(target) as c:
  assert c.execute('SELECT count(*) FROM runs').fetchone()[0]==1
  c.execute('CREATE TABLE newer_table(id integer)')
 try: pg.restore(target,folder,meta,replace=True)
 except ValueError: pass
 else: raise AssertionError('newer schema must fail before replacement')
 print(json.dumps({'roundtrip':'ok','tables':len(meta['tables']),'client':meta['client_version']}))
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "container:" + os.environ["TOIR_PG17_TEST_CONTAINER"],
            "-v",
            str(SCRIPTS / "postgres_admin.py") + ":/opt/toir/postgres_admin.py:ro",
            os.environ.get("TOIR_PG17_TEST_IMAGE", "toir-pg-coordinator-check"),
            "python",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["roundtrip"] == "ok"


def test_selected_postgres_missing_credential_preserves_runtime_secret(monkeypatch):
    monkeypatch.setattr(remote_apply, "configured_backend", lambda: "postgres")
    monkeypatch.setattr(
        remote_apply,
        "runtime_config",
        lambda: {"respan_secret": "r", "scalekit_secret": "s", "database_secret": "d"},
    )
    monkeypatch.setattr(remote_apply, "read_secret", lambda *args: {})
    monkeypatch.setattr(
        remote_apply.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must preserve secret")
    )
    with pytest.raises(remote_apply.PostgresBackupRequired, match="credential is missing"):
        remote_apply.sync_secrets()


def test_recovery_pg_backup_uses_last_verified_image(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    release = "a" * 12 + "-" + "b" * 12
    folder = tmp_path / "releases" / release
    folder.mkdir(parents=True)
    (tmp_path / "state.json").write_text(json.dumps({"current": release}))
    manifest = {
        "items": [
            {
                "kind": "Deployment",
                "metadata": {"name": "orchestrator"},
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"company-brain/postgres-backup": "v1"}},
                        "spec": {"containers": [{"image": "last-good"}]},
                    }
                },
            }
        ]
    }
    (folder / "manifests.json").write_text(json.dumps(manifest))
    (folder / "checksums.json").write_text(
        json.dumps({"manifests.json": remote_apply.sha256(folder / "manifests.json")})
    )
    calls = []
    monkeypatch.setattr(
        remote_apply,
        "database_operator",
        lambda **kwargs: calls.append(kwargs) or nullcontext("pod/operator"),
    )
    monkeypatch.setattr(
        remote_apply,
        "backup_postgres",
        lambda bucket, **kwargs: calls.append(kwargs) or "fresh-pg-backup",
    )
    assert remote_apply.recovery_postgres_backup("bucket") == "fresh-pg-backup"
    assert calls == [
        {"image": "last-good"},
        {"leave_maintenance": True, "staged": True, "operator": "pod/operator"},
    ]


def test_cutover_records_receipt_only_after_stopped_writer_verified_import(tmp_path, monkeypatch):
    from contextlib import contextmanager

    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    monkeypatch.setattr(remote_apply, "data_directory", lambda: tmp_path)
    monkeypatch.setattr(remote_apply, "coordinator_writes_database", lambda: True)
    monkeypatch.setattr(remote_apply, "drain", lambda: True)
    state = {"stopped": False}

    @contextmanager
    def operator(**kwargs):
        assert kwargs == {"source_readonly": True}
        state["stopped"] = True
        yield "pod/admin"
        state["stopped"] = False

    monkeypatch.setattr(remote_apply, "database_operator", operator)

    def publish(source, bucket):
        assert state["stopped"]
        return "sqlite-safe"

    monkeypatch.setattr(remote_apply, "publish_sqlite_backup", publish)
    monkeypatch.setattr(remote_apply, "backup_postgres", lambda *args, **kwargs: "pg-safe")
    monkeypatch.setattr(remote_apply, "postgres_secret", lambda: {"DATABASE_URL": "never-logged"})

    def invoke(args, **kwargs):
        assert state["stopped"]
        assert "never-logged" not in repr(args)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"runs": 1, "events": 2, "sales_records": 3, "sha256": "a" * 64}),
            "",
        )

    monkeypatch.setattr(remote_apply.subprocess, "run", invoke)

    def sync():
        assert remote_apply.configured_backend() == "postgres"
        assert not state["stopped"]

    monkeypatch.setattr(remote_apply, "sync_secrets", sync)
    monkeypatch.setattr(remote_apply, "execute", lambda *args, **kwargs: "")
    monkeypatch.setattr(remote_apply, "live_backend", lambda: "postgres")
    monkeypatch.setattr(remote_apply, "maintenance", lambda *_: {})
    remote_apply.storage_cutover("bucket")
    receipt = json.loads((tmp_path / "storage.json").read_text())
    assert receipt["migration"]["sales_records"] == 3
    assert receipt["sqlite_backup"] == "sqlite-safe"


def test_restore_takes_safeguard_after_writer_stops(tmp_path, monkeypatch):
    from contextlib import contextmanager

    monkeypatch.setattr(remote_apply, "STATE", tmp_path)
    monkeypatch.setattr(remote_apply, "unpack_postgres_backup", lambda *args: {})
    monkeypatch.setattr(remote_apply, "drain", lambda: True)
    events = []

    def execute(*args, **kwargs):
        if "s3" in args and "cp" in args:
            Path(args[args.index("cp") + 2]).write_bytes(b"archive")
        if "--replicas=1" in args:
            events.append("restart")
        return ""

    monkeypatch.setattr(remote_apply, "execute", execute)

    @contextmanager
    def operator(**kwargs):
        events.append("stop")
        yield "pod/admin"
        events.append("remove-admin")

    monkeypatch.setattr(remote_apply, "database_operator", operator)
    monkeypatch.setattr(
        remote_apply, "backup_postgres", lambda *args, **kwargs: events.append("backup")
    )
    monkeypatch.setattr(
        remote_apply.subprocess,
        "run",
        lambda args, **kwargs: events.append("restore") or subprocess.CompletedProcess(args, 0),
    )
    monkeypatch.setattr(remote_apply, "maintenance", lambda *_: events.append("resume"))
    remote_apply.restore_postgres("bucket", "20261007T000000Z-" + "a" * 12)
    assert events == ["stop", "backup", "restore", "remove-admin", "restart", "resume"]
