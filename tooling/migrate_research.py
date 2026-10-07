"""Validated application-only SQLite → Postgres cutover; never copies checkpoint blobs.

Run as a module from the repository root. Credentials are read only from DATABASE_URL.
Import requires an empty run store and a separately verified database backup.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from psycopg.types.json import Jsonb

from apps.orchestrator.models.research import Run, RunEvent, now
from apps.orchestrator.storage.postgres import PostgresRunRepository

RUN_COLUMNS = ("id", "idempotency_key", "request_hash", "status", "created_at", "payload")
EVENT_COLUMNS = ("sequence", "run_id", "created_at", "stage", "message")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def validate(bundle: dict) -> dict:
    if bundle.get("format") != "toir-research-migration-v1" or bundle.get("schema_version") != 1:
        raise ValueError("Unsupported research migration format")
    records = {"runs": bundle.get("runs"), "events": bundle.get("events")}
    if not all(isinstance(v, list) for v in records.values()) or digest(records) != bundle.get(
        "sha256"
    ):
        raise ValueError("Migration record checksum mismatch")
    ids, keys, sequences = set(), set(), set()
    for row in records["runs"]:
        if not isinstance(row, dict) or set(row) != set(RUN_COLUMNS):
            raise ValueError("Unexpected run row shape")
        run = Run.model_validate(row["payload"])
        if run.id in ids or row["idempotency_key"] in keys:
            raise ValueError("Duplicate run identity or idempotency key")
        if (run.id, run.status, run.created_at) != (row["id"], row["status"], row["created_at"]):
            raise ValueError("Run metadata does not match payload")
        if run.status in {"queued", "running"}:
            raise ValueError("Drain or interrupt active runs before migration")
        if not isinstance(row["idempotency_key"], str) or not row["idempotency_key"]:
            raise ValueError("Missing idempotency key")
        if PostgresRunRepository.request_hash(run.brief, run.parent_id) != row["request_hash"]:
            raise ValueError("Request hash does not match run brief")
        ids.add(run.id)
        keys.add(row["idempotency_key"])
    for row in records["runs"]:
        if row["payload"].get("parent_id") and row["payload"]["parent_id"] not in ids:
            raise ValueError("Historical parent run is missing")
    previous = 0
    for row in records["events"]:
        if not isinstance(row, dict) or set(row) != set(EVENT_COLUMNS):
            raise ValueError("Unexpected event row shape")
        event = RunEvent.model_validate(row)
        if event.run_id not in ids or event.sequence in sequences or event.sequence <= previous:
            raise ValueError("Invalid event identity or sequence ordering")
        sequences.add(event.sequence)
        previous = event.sequence
    return bundle


def export_sqlite(source: Path, destination: Path) -> dict:
    """Export a consistent read snapshot from a drained, online-backup-validated file."""
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing migration export")
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source SQLite failed integrity check")
        if [r[0] for r in db.execute("SELECT version FROM schema_version")] != [1]:
            raise ValueError("Unsupported source schema")
        runs = []
        for row in db.execute("SELECT * FROM runs ORDER BY created_at,id"):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            runs.append(item)
        events = [dict(row) for row in db.execute("SELECT * FROM run_events ORDER BY sequence")]
    records = {"runs": runs, "events": events}
    bundle = validate(
        {
            "format": "toir-research-migration-v1",
            "schema_version": 1,
            "created_at": now(),
            "sha256": digest(records),
            **records,
        }
    )
    # Source evidence can be sensitive. Exclusive creation prevents accidental replacement.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(bundle, output, indent=2)
        output.write("\n")
    return bundle


async def import_postgres(bundle: dict, url: str) -> dict:
    validate(bundle)
    repo = await PostgresRunRepository.open(url)
    try:
        async with repo.lock, repo.connection.transaction():
            await repo.connection.execute("LOCK TABLE runs,run_events IN ACCESS EXCLUSIVE MODE")
            cursor = await repo.connection.execute("SELECT count(*) AS n FROM runs")
            if (await cursor.fetchone())["n"]:
                raise ValueError("Destination has research records; refusing merge or overwrite")
            cursor = await repo.connection.execute("SELECT count(*) AS n FROM run_events")
            if (await cursor.fetchone())["n"]:
                raise ValueError("Destination has events; refusing merge or overwrite")
            for row in bundle["runs"]:
                await repo.connection.execute(
                    "INSERT INTO runs VALUES (%s,%s,%s,%s,%s,%s)",
                    tuple(Jsonb(row[k]) if k == "payload" else row[k] for k in RUN_COLUMNS),
                )
            for row in bundle["events"]:
                await repo.connection.execute(
                    "INSERT INTO run_events(sequence,run_id,created_at,stage,message) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    tuple(row[k] for k in EVENT_COLUMNS),
                )
            runs = await (
                await repo.connection.execute("SELECT * FROM runs ORDER BY created_at,id")
            ).fetchall()
            events = await (
                await repo.connection.execute("SELECT * FROM run_events ORDER BY sequence")
            ).fetchall()
            if digest({"runs": runs, "events": events}) != bundle["sha256"]:
                raise ValueError("Imported record hashes differ; transaction rolled back")
            # setval is not transactional, but on rollback advancing it is harmless.
            largest = max((row["sequence"] for row in events), default=0)
            await repo.connection.execute(
                "SELECT setval(pg_get_serial_sequence('run_events','sequence'), %s, %s)",
                (max(largest, 1), bool(largest)),
            )
        return {"runs": len(runs), "events": len(events), "sha256": bundle["sha256"]}
    finally:
        await repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    export = sub.add_parser("export")
    export.add_argument(
        "--source", type=Path, required=True, help="Verified SQLite runs.sqlite backup"
    )
    export.add_argument("--output", type=Path, required=True)
    inspect = sub.add_parser("validate")
    inspect.add_argument("--input", type=Path, required=True)
    importer = sub.add_parser("import")
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument(
        "--backup-reference",
        required=True,
        help="Verified, retained database backup ID recorded by the operator",
    )
    importer.add_argument("--writers-stopped", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "export":
            result = export_sqlite(args.source, args.output)
        else:
            result = validate(json.loads(args.input.read_text()))
            if args.operation == "import":
                if not args.backup_reference.strip():
                    raise ValueError("Record the verified pre-import backup reference")
                url = os.environ.get("DATABASE_URL")
                if not url:
                    raise ValueError(
                        "DATABASE_URL is required; never pass credentials as arguments"
                    )
                summary = asyncio.run(import_postgres(result, url))
                print(json.dumps({**summary, "backup_reference": args.backup_reference}))
                return
        print(
            json.dumps(
                {
                    "runs": len(result["runs"]),
                    "events": len(result["events"]),
                    "sha256": result["sha256"],
                }
            )
        )
    except Exception as error:
        # Database exceptions may contain credentials or row data. Log type only.
        parser.exit(
            1, f"Migration failed ({type(error).__name__}); no successful import was reported.\n"
        )


if __name__ == "__main__":
    main()
