"""Durable ingestion receipts, independent of Cognee's non-atomic stores.

Each method owns a short SQLite transaction and is called via to_thread. FULL
synchronous commits precede provider calls and acknowledgments. The ledger is
local to the single Spark writer and must be backed up with its Cognee stores.
"""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .research_contract import IngestionError, canonical


class ResearchLedger:
    def __init__(self, path: Path):
        self.path = path
        self._owner = None

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner = self.path.with_suffix(self.path.suffix + ".lock").open("a+")
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            legacy = self.path.parent / "research_ingestions.json"
            if legacy.exists() and json.loads(legacy.read_text()):
                # The alternate JSON implementation saved acknowledgments only,
                # without request hashes or evidence of interrupted provider work.
                # Never blindly replay or re-ingest those IDs in a fresh ledger.
                raise RuntimeError("legacy_research_receipts_require_reconciliation")
            with self.connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS research_ingestions (
                    ingestion_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    documents_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'prepared',
                    completed INTEGER NOT NULL DEFAULT 0,
                    active_document INTEGER,
                    completion_json TEXT NOT NULL DEFAULT '[]',
                    acknowledgment_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                # A previous process may have written to Cognee without committing
                # a receipt. Content hash deduplication cannot prove completion.
                db.execute("""UPDATE research_ingestions SET status='uncertain',
                    updated_at=CURRENT_TIMESTAMP WHERE active_document IS NOT NULL
                    AND status != 'forgotten'""")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._owner:
            self._owner.close()
            self._owner = None

    def reserve(self, ingestion_id, request_hash, payload, documents):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM research_ingestions WHERE ingestion_id=?", (ingestion_id,)
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise IngestionError(409, "ingestion_id_conflict")
                return dict(row)
            db.execute(
                """INSERT INTO research_ingestions
                (ingestion_id, request_hash, payload_json, documents_json) VALUES (?, ?, ?, ?)""",
                (ingestion_id, request_hash, payload, canonical(documents)),
            )
            return dict(
                db.execute(
                    "SELECT * FROM research_ingestions WHERE ingestion_id=?", (ingestion_id,)
                ).fetchone()
            )

    def start_document(self, ingestion_id, index):
        with self.connection() as db:
            changed = db.execute(
                """UPDATE research_ingestions SET active_document=?,
                updated_at=CURRENT_TIMESTAMP WHERE ingestion_id=? AND status='prepared'
                AND completed=? AND active_document IS NULL""",
                (index, ingestion_id, index),
            ).rowcount
            if changed != 1:
                raise IngestionError(503, "research_ingestion_uncertain")

    def finish_document(self, ingestion_id, index, completion):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM research_ingestions WHERE ingestion_id=?", (ingestion_id,)
            ).fetchone()
            if row is None or row["active_document"] != index or row["status"] != "prepared":
                raise IngestionError(503, "research_ingestion_uncertain")
            metadata = json.loads(row["completion_json"])
            metadata.append(completion)
            db.execute(
                """UPDATE research_ingestions SET completed=?, active_document=NULL,
                completion_json=?, updated_at=CURRENT_TIMESTAMP WHERE ingestion_id=?""",
                (index + 1, canonical(metadata), ingestion_id),
            )

    def uncertain(self, ingestion_id):
        with self.connection() as db:
            db.execute(
                """UPDATE research_ingestions SET status='uncertain',
                updated_at=CURRENT_TIMESTAMP WHERE ingestion_id=? AND status='prepared'""",
                (ingestion_id,),
            )

    def finish(self, ingestion_id, acknowledgment):
        with self.connection() as db:
            changed = db.execute(
                """UPDATE research_ingestions SET status='completed',
                acknowledgment_json=?, updated_at=CURRENT_TIMESTAMP WHERE ingestion_id=?
                AND status='prepared' AND active_document IS NULL AND completed=?""",
                (canonical(acknowledgment), ingestion_id, acknowledgment["documents"]),
            ).rowcount
            if changed != 1:
                raise IngestionError(503, "research_ingestion_uncertain")
        return acknowledgment

    def invalidate(self):
        """Invalidate before destructive dataset deletion, even if deletion fails."""
        with self.connection() as db:
            db.execute("""UPDATE research_ingestions SET status='forgotten',
                acknowledgment_json=NULL, updated_at=CURRENT_TIMESTAMP""")

    def status(self, ingestion_id):
        with self.connection() as db:
            row = db.execute(
                """SELECT ingestion_id,status,completed,active_document,
                completion_json FROM research_ingestions WHERE ingestion_id=?""",
                (ingestion_id,),
            ).fetchone()
        if not row:
            raise IngestionError(404, "research_ingestion_not_found")
        return {key: value for key, value in dict(row).items() if key != "completion_json"} | {
            "provider_completions": json.loads(row["completion_json"]),
        }
