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
from apps.orchestrator.sales.store import KINDS, SalesStore
from apps.orchestrator.storage.postgres import PostgresRunRepository

RUN_COLUMNS = ("id", "idempotency_key", "request_hash", "status", "created_at", "payload")
EVENT_COLUMNS = ("sequence", "run_id", "created_at", "stage", "message")
SALES_COLUMNS = ("workspace_id", "kind", "id", "payload")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def validate(bundle: dict) -> dict:
    if (
        bundle.get("format") not in {"toir-research-migration-v1", "toir-research-migration-v2"}
        or bundle.get("schema_version") != 1
    ):
        raise ValueError("Unsupported research migration format")
    records = {"runs": bundle.get("runs"), "events": bundle.get("events")}
    if bundle["format"] == "toir-research-migration-v2":
        records["sales_records"] = bundle.get("sales_records")
        if bundle.get("sales_schema_version") != 1:
            raise ValueError("Unsupported sales schema")
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
    sales_ids = set()
    for row in records.get("sales_records", []):
        if not isinstance(row, dict) or set(row) != set(SALES_COLUMNS):
            raise ValueError("Unexpected sales row shape")
        identity = (row["workspace_id"], row["kind"], row["id"])
        if (
            row["workspace_id"] != "toir"
            or row["kind"] not in KINDS
            or not isinstance(row["id"], str)
            or not row["id"]
        ):
            raise ValueError("Invalid sales record identity")
        if (
            identity in sales_ids
            or not isinstance(row["payload"], dict)
            or row["payload"].get("workspace_id") != "toir"
        ):
            raise ValueError("Duplicate or invalid sales payload")
        if row["kind"] in {"job", "crm_operation", "outbox"} and row["payload"].get("status") in {
            "running",
            "in_progress",
            "sending",
        }:
            raise ValueError("Drain active sales work before migration")
        sales_ids.add(identity)
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
        sales = []
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_records'"
        ).fetchone():
            if [r[0] for r in db.execute("SELECT version FROM sales_schema_version")] != [1]:
                raise ValueError("Unsupported source sales schema")
            for row in db.execute("SELECT * FROM sales_records ORDER BY workspace_id,kind,id"):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                sales.append(item)
    records = {"runs": runs, "events": events, "sales_records": sales}
    bundle = validate(
        {
            "format": "toir-research-migration-v2",
            "schema_version": 1,
            "sales_schema_version": 1,
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


async def import_postgres(bundle: dict, url: str, *, allow_identical: bool = False) -> dict:
    validate(bundle)
    repo = await PostgresRunRepository.open(url)
    include_sales = bundle["format"] == "toir-research-migration-v2"
    try:
        await SalesStore(repo).setup()
        async with repo.lock, repo.connection.transaction():
            await repo.connection.execute(
                "LOCK TABLE runs,run_events,sales_records IN ACCESS EXCLUSIVE MODE"
            )

            async def records():
                result = {
                    "runs": await (
                        await repo.connection.execute("SELECT * FROM runs ORDER BY created_at,id")
                    ).fetchall(),
                    "events": await (
                        await repo.connection.execute("SELECT * FROM run_events ORDER BY sequence")
                    ).fetchall(),
                }
                sales = await (
                    await repo.connection.execute(
                        "SELECT * FROM sales_records ORDER BY workspace_id,kind,id"
                    )
                ).fetchall()
                if include_sales:
                    result["sales_records"] = sales
                elif sales:
                    raise ValueError("Legacy migration cannot overwrite sales records")
                return result

            existing = await records()
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                if (
                    await (
                        await repo.connection.execute("SELECT to_regclass(%s) AS name", (table,))
                    ).fetchone()
                )["name"]:
                    # Identifiers come only from this fixed allowlist.
                    if (
                        await (
                            await repo.connection.execute(f"SELECT count(*) AS n FROM {table}")
                        ).fetchone()
                    )["n"]:
                        raise ValueError("Destination contains checkpoints; refusing migration")
            if allow_identical and digest(existing) == bundle["sha256"]:
                return {
                    **{k: len(v) for k, v in existing.items()},
                    "sha256": bundle["sha256"],
                    "already_imported": True,
                }
            if any(existing.values()):
                raise ValueError(
                    "Destination has research or sales records; refusing merge or overwrite"
                )
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
            for row in bundle.get("sales_records", []):
                await repo.connection.execute(
                    "INSERT INTO sales_records(workspace_id,kind,id,payload) VALUES (%s,%s,%s,%s)",
                    tuple(Jsonb(row[k]) if k == "payload" else row[k] for k in SALES_COLUMNS),
                )
            imported = await records()
            if digest(imported) != bundle["sha256"]:
                raise ValueError("Imported record hashes differ; transaction rolled back")
            largest = max((row["sequence"] for row in imported["events"]), default=0)
            await repo.connection.execute(
                "SELECT setval(pg_get_serial_sequence('run_events','sequence'), %s, %s)",
                (max(largest, 1), bool(largest)),
            )
        return {**{k: len(v) for k, v in imported.items()}, "sha256": bundle["sha256"]}
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
                    "sales_records": len(result.get("sales_records", [])),
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
