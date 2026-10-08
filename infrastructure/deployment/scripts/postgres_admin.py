"""Credential-safe PG17 whole-database snapshots, restore verification and cutover.

Runs in the coordinator image. Credentials are read from runtime environment or one
JSON line on stdin; neither command arguments nor error output contain credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

PG_BIN = Path("/usr/lib/postgresql/17/bin")
ENV_KEYS = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "dbname": "PGDATABASE",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
}


def connection_environment(url: str) -> dict[str, str]:
    settings = conninfo_to_dict(url)
    if set(settings) - set(ENV_KEYS):
        raise ValueError("Unsupported connection settings")
    host = settings.get("host", "")
    if host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith("/"):
        if settings.get("sslmode") != "verify-full":
            raise ValueError("Remote database requires verified TLS")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k != "DATABASE_URL"}
    env.update({ENV_KEYS[k]: v for k, v in settings.items()})
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    return env


def run_client(name: str, args: list[str], url: str) -> str:
    result = subprocess.run(
        [str(PG_BIN / name), *args],
        env=connection_environment(url),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError(f"{name} failed; database output was withheld")
    return result.stdout


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(connection) -> list[dict]:
    """Hashes every user table, including all graph checkpoints, without logging rows."""
    tables = connection.execute(
        "SELECT schemaname,tablename FROM pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
        "AND schemaname NOT LIKE 'pg_toast%%' ORDER BY 1,2"
    ).fetchall()
    result = []
    for schema, table in tables:
        checksum, count = hashlib.sha256(), 0
        query = sql.SQL(
            'SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"'
        ).format(sql.Identifier(schema, table))
        with connection.cursor(name="backup_inventory") as cursor:
            cursor.execute(query)
            for (row,) in cursor:
                checksum.update(row.encode() + b"\n")
                count += 1
        result.append(
            {"schema": schema, "table": table, "rows": count, "sha256": checksum.hexdigest()}
        )
    return result


def snapshot(url: str, directory: Path) -> dict:
    version = run_client("pg_dump", ["--version"], url).strip()
    if not version.startswith("pg_dump (PostgreSQL) 17."):
        raise RuntimeError("PostgreSQL17 client is required")
    dump = directory / "database.dump"
    with psycopg.connect(url, connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        server = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if server // 10000 != 17:
            raise ValueError("This backup workflow is pinned to PostgreSQL17")
        snapshot_id = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        run_client(
            "pg_dump",
            [
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--lock-wait-timeout=30s",
                f"--snapshot={snapshot_id}",
                f"--file={dump}",
            ],
            url,
        )
        tables = inventory(connection)
    os.chmod(dump, 0o600)
    run_client("pg_restore", ["--list", str(dump)], url)
    return {
        "storage_backend": "postgres",
        "postgres_major": 17,
        "server_version_num": server,
        "client_version": version,
        "tables": tables,
        "checksums": {"database.dump": file_hash(dump)},
    }


def restore(url: str, directory: Path, metadata: dict, *, replace: bool = False):
    """Restore in one transaction. Only explicit operator restore permits replacement."""
    dump = directory / "database.dump"
    if metadata.get("postgres_major") != 17 or metadata.get("checksums") != {
        "database.dump": file_hash(dump)
    }:
        raise ValueError("Unverified PostgreSQL archive")
    with psycopg.connect(url, connect_timeout=10) as connection:
        current = inventory(connection)
        if not replace and current:
            raise ValueError("Scratch restore requires an empty database")
        if replace:
            expected_tables = {(row["schema"], row["table"]) for row in metadata["tables"]}
            if any((row["schema"], row["table"]) not in expected_tables for row in current):
                raise ValueError("Target has newer tables; use a schema-aware restore procedure")
    args = ["--single-transaction", "--exit-on-error", "--no-owner", "--no-acl", "--no-password"]
    if replace:
        args.extend(["--clean", "--if-exists"])
    # A DB name is necessary to select restore execution. It comes only from env
    # through a safe conninfo keyword; the URL/password is never an argument.
    args.extend(["--dbname", "connect_timeout=10", str(dump)])
    run_client("pg_restore", args, url)
    with psycopg.connect(url, connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if inventory(connection) != metadata["tables"]:
            raise ValueError("Restored table hashes differ")


def read_archive(stream, directory: Path) -> dict:
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        seen = set()
        for member in archive:
            if (
                member.name not in {"database.dump", "backup.json"}
                or member.name in seen
                or not member.isfile()
            ):
                raise ValueError("Unexpected PostgreSQL archive contents")
            seen.add(member.name)
            with (
                archive.extractfile(member) as source,
                (directory / member.name).open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
        if seen != {"database.dump", "backup.json"}:
            raise ValueError("Incomplete PostgreSQL archive")
    return json.loads((directory / "backup.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["snapshot", "restore", "inspect", "migrate"])
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    try:
        url = (
            json.loads(sys.stdin.buffer.readline())["DATABASE_URL"]
            if args.credential_stdin
            else os.environ["DATABASE_URL"]
        )
        connection_environment(url)
        with tempfile.TemporaryDirectory(prefix="toir-postgres-") as temp:
            directory = Path(temp)
            if args.operation == "snapshot":
                metadata = snapshot(url, directory)
                (directory / "backup.json").write_text(json.dumps(metadata))
                with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
                    for name in ("database.dump", "backup.json"):
                        archive.add(directory / name, arcname=name)
            elif args.operation == "restore":
                metadata = read_archive(sys.stdin.buffer, directory)
                restore(url, directory, metadata, replace=args.replace)
                print(json.dumps({"restored": True, "tables": len(metadata["tables"])}))
            elif args.operation == "migrate":
                import asyncio

                sys.path.insert(0, "/app")
                from tooling.migrate_research import export_sqlite, import_postgres

                bundle = export_sqlite(args.source, directory / "migration.json")
                print(json.dumps(asyncio.run(import_postgres(bundle, url, allow_identical=True))))
            else:
                with psycopg.connect(url, connect_timeout=10) as connection:
                    connection.execute("SET TRANSACTION READ ONLY")
                    print(json.dumps({"tables": inventory(connection)}))
    except Exception as error:
        print(
            f"PostgreSQL operation failed ({type(error).__name__}); details withheld.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
