# Toir research persistence — database engineer handoff

This document is being published ahead of the research implementation so database
work can start immediately. The interfaces below are the integration contract for
the implementation in progress; they are not a claim that the full feature is
already deployed. The existing TypeScript demo adapters in this directory are a
separate browser demo and are not the research system of record.

## Ownership and data flow

Browser → API → Python/LangGraph coordinator → Bun/Oh My Pi research agent.

The coordinator is the only durable-state writer. It owns research briefs, run
status, events, accepted company findings, source evidence and graph checkpoints.
The API proxies validated requests; the research agent owns temporary sessions
and returns versioned task results. Neither opens the coordinator's database.

V1 has one Toir workspace and one active run. Its two local SQLite databases live
on the coordinator's persistent volume, under `TOIR_DATA_DIR` (default `/data` in
Kubernetes): `runs.sqlite` and `checkpoints.sqlite`. Local developer runs may set
this directory to an ignored working directory. SQLite WAL mode is enabled.
Local-path volumes survive pod replacement and reboot, but are tied to the EC2
disk. Backup/restore is required for loss or replacement of that disk.

The existing `rag-db-api` on Spark remains independent. A vector index or document
search API cannot replace transactional run state or the LangGraph checkpointer.

## Application repository contract

Implement `RunRepository` from `apps/orchestrator/storage/ports.py`. The initial
adapter is `apps/orchestrator/storage/sqlite.py`; select the replacement in the
storage factory used by the coordinator lifespan, not in routers or graph nodes.

```python
async def create(brief: Brief, key: str, parent_id: str | None = None) -> Run: ...
async def get(run_id: str) -> Run | None: ...
async def list(limit: int = 50) -> list[Run]: ...
async def active() -> Run | None: ...
async def save(run: Run) -> None: ...
async def event(run_id: str, stage: str, message: str) -> None: ...
async def events(run_id: str) -> list[RunEvent]: ...
```

`create` atomically enforces two independent constraints:

1. An idempotency key with the same normalized brief and parent ID returns the
   original run, including after completion. A different request using that key
   raises `Conflict` (HTTP 409). Never dispatch paid work before the row commits.
2. Only one run may have status `queued` or `running`. A competing creation raises
   `Conflict`. Enforce this in the database, not just a process-local lock.

Retry creates a new UUID run with `parent_id` pointing to the original. It carries
saved research evidence forward but never overwrites the original report.
Terminal states are `completed`, `failed`, `cancelled`, and `interrupted`.
Lists return newest runs first; events return chronological order, capped at the
latest 200. Missing IDs return `None`; event sequence numbers are monotonic.
Preserve UTC timestamps, original IDs, idempotency keys and request hashes.

## SQLite schema and report data

Schema version 1:

```sql
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE NOT NULL,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_run ON runs((1))
  WHERE status IN ('queued', 'running');
CREATE TABLE run_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  created_at TEXT NOT NULL,
  stage TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX events_by_run ON run_events(run_id, sequence);
```

`payload` is the validated `Run` JSON from
`apps/orchestrator/models/research.py`. It includes the brief, timestamps, status,
stage, internal task ID, follow-up pass, plan, report, usage, error and trace ID.
There is no separate company/evidence table in v1: the report is an aggregate,
saved with its run. The database adapter may use JSONB first or normalize it
later while preserving the same returned model.

The report contains:

- **Leads:** company, canonical domain, country, employee estimate when known,
  identity citations, decision-maker when verified, dated buying signals,
  proposed AI use case, rationale, outreach angle and fit score.
- **Signals:** leadership, funding, partnership or business need; claim,
  event date and citations. Event date is distinct from source publication date.
- **Sources:** stable `src_` ID, URL, title, retrieval timestamp, optional
  publication date and retrieved text (bounded to 12,000 characters).
- **Citations:** source ID and a supporting quotation. Source IDs must resolve
  within the report; quotes must match stored retrieved text.
- **Competitor facts:** company, advertisement/marketing/pricing/customer type,
  sourced claim, citations and a separately labelled positioning hypothesis.
- **Gaps and summary:** limitations and conclusions from completed review.

Preserve source text and citations during migration. A URL alone is not a
sufficient evidence record: pages change. Canonical domains identify company
candidates; they do not make two historical research runs the same record.

## LangGraph checkpoint replacement

Checkpoint persistence is a separate interface from `RunRepository`. V1 uses
`langgraph-checkpoint-sqlite`'s `AsyncSqliteSaver`, with the run UUID as LangGraph
`thread_id`. All graph access goes through the checkpointer selected at startup.

For PostgreSQL, use a pinned compatible `langgraph-checkpoint-postgres`
`AsyncPostgresSaver`; run its setup/migrations once under operator control before
starting application writers. Store only typed, validated graph state and keep
strict checkpoint deserialization enabled. Do not hand-copy undocumented
checkpoint blobs or assume SQLite and PostgreSQL checkpoint tables are identical.

For the initial cutover, drain all active runs, preserve their historical
application records, and start with an empty PostgreSQL checkpoint store for new
runs. Keep the SQLite checkpoint backup for rollback. Do not resume an old graph
from a manually translated blob. Retrying an interrupted historical run creates
a new run using its saved report/evidence.

## Configuration and infrastructure changes

1. Add a database adapter and switch the storage factory using `DATABASE_URL`;
   missing `DATABASE_URL` continues to select local SQLite.
2. Store `DATABASE_URL` in this project's AWS Secrets Manager runtime secret,
   inject it only into the coordinator, and redact it from diagnostics/traces.
   The API, web and research agent do not need database credentials.
3. Require verified TLS for a remote database, scoped database permissions,
   bounded connections and private connectivity. Choose the concrete connection
   settings with the database deployment; do not expose the Spark service or add
   a public inbound EC2 rule as a shortcut.
4. Keep one coordinator replica and one active run for the first cutover.
   Multiple replicas require database-backed ownership leases, worker fencing,
   cancellation coordination and idempotent task dispatch in addition to the
   database change. A connection string alone does not make execution scalable.
5. Keep the old persistent volume and backups through the rollback window.

## Backup, migration and cutover

- Back up with SQLite's online backup API, including both databases. Copying a
  live `.sqlite` file without its WAL is not a valid backup. For a coordinated
  pre-deployment/cutover snapshot, drain research first and refuse new runs.
- Upload snapshots and a manifest (schema version, timestamps, checksums and
  source release) to the project's encrypted S3 `backups/` prefix. The EC2 role
  needs only scoped backup write/read access in addition to release reads.
- Export application rows as JSON with the complete `Run` payload, idempotency
  metadata and ordered events. Validate every payload before importing it inside
  a transaction. Preserve IDs and reset any PostgreSQL event sequence above the
  largest imported sequence. Compare counts and canonical payload hashes.
- Switch the runtime database secret, synchronize it to Kubernetes and restart
  the coordinator. Test history, a new run, retry and cancellation before
  admitting normal traffic.
- If cutover fails before new writes, restore the old configuration and verified
  SQLite snapshot. After new writes, drain and reconcile/export those records
  before reverting; never silently discard post-cutover runs.

## Required adapter and integration checks

Run the repository contract suite in `tooling/tests/test_research_storage.py`
against the replacement adapter. Extend it for the chosen database driver.

- Same-key replay returns the same ID; same-key/different-brief rejects.
- Two concurrent creates allow exactly one active run.
- Save/load preserves report evidence, dates, usage, errors and task IDs.
- Event ordering, foreign-key relationships and list limits remain correct.
- Retry preserves the parent and uses a new idempotency boundary.
- Pod restart retains history and reconciles an existing agent task; a lost
  agent session becomes interrupted instead of silently repeating paid calls.
- Backup restoration and migration round-trips preserve record counts/hashes.
- Secrets and connection strings never appear in API responses, logs or traces.

The browser-facing `/api/research-runs` contract and the internal research-agent
task contract must remain unchanged during the database swap.
