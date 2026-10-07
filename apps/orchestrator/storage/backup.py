"""Consistent local snapshot/export tools, invoked inside the coordinator container."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from apps.orchestrator.models.research import Run, now
from apps.orchestrator.storage.factory import data_directory


def snapshot(directory: Path, source: Path | None = None):
    source = source or data_directory()
    maintenance = source / "maintenance.json"
    if not maintenance.exists() or not json.loads(maintenance.read_text()).get("enabled"):
        raise RuntimeError("Enable maintenance and drain active runs before snapshotting")
    directory.mkdir(parents=True, exist_ok=False)
    checksums = {}
    with sqlite3.connect(f"file:{source / 'runs.sqlite'}?mode=ro", uri=True) as connection:
        if connection.execute(
            "SELECT count(*) FROM runs WHERE status IN ('queued','running')",
        ).fetchone()[0]:
            raise RuntimeError("An active run must finish or be cancelled before backup")
    for name in ("runs.sqlite", "checkpoints.sqlite"):
        with sqlite3.connect(f"file:{source / name}?mode=ro", uri=True) as connection:
            with sqlite3.connect(directory / name) as backup:
                connection.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite snapshot failed integrity check")
        checksums[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": 1,
                "created_at": now(),
                "checksums": checksums,
            },
            indent=2,
        )
        + "\n"
    )


def export_rows(destination: Path, source: Path | None = None):
    with sqlite3.connect(
        f"file:{(source or data_directory()) / 'runs.sqlite'}?mode=ro", uri=True
    ) as db:
        db.row_factory = sqlite3.Row
        with db:  # A read transaction yields one application-state snapshot.
            db.execute("BEGIN")
            runs = []
            for row in db.execute("SELECT * FROM runs ORDER BY created_at"):
                item = dict(row)
                item["payload"] = Run.model_validate_json(item["payload"]).model_dump(mode="json")
                runs.append(item)
            events = [dict(row) for row in db.execute("SELECT * FROM run_events ORDER BY sequence")]
    destination.write_text(
        json.dumps({"schema_version": 1, "runs": runs, "events": events}, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["snapshot", "export"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.operation == "snapshot":
        if args.directory is None:
            parser.error("--directory is required")
        snapshot(args.directory)
    else:
        if args.output is None:
            parser.error("--output is required")
        export_rows(args.output)
