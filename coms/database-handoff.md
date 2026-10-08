# Toir research persistence — database engineer handoff

This document was published first so database work could start immediately. The SQLite and Postgres adapters, repository interfaces, validated models and
contract tests are implemented. The Scalekit Exa connected account was verified
active in the historical October 7 handoff. Live readiness still requires a
current runtime check. Server-backed sales workflows are documented in
[prospecting-contract.md](prospecting-contract.md); historical browser demo
fixtures are never application records or executable approvals.

## Ownership and data flow

Browser → API → Python/LangGraph coordinator → Bun/Oh My Pi research agent.

The coordinator is the only durable-state writer. It owns research briefs, run
status, events, accepted company findings, source evidence and graph checkpoints.
The API proxies validated requests; the research agent owns temporary sessions
and returns versioned task results. Neither opens the coordinator's database.

Research v1 has one Toir workspace and one active research run. The new durable
sales queue preserves that database constraint while adding a separate contact
worker slot. When `DATABASE_URL` is absent, two local SQLite databases live
on the coordinator's persistent volume, under `TOIR_DATA_DIR` (default `/data` in
Kubernetes): `runs.sqlite` and `checkpoints.sqlite`. Local developer runs may set
this directory to an ignored working directory. SQLite WAL mode is enabled.
Local-path volumes survive pod replacement and reboot, but are tied to the EC2
disk. Backup/restore is required for loss or replacement of that disk.

The existing `rag-db-api` on Spark remains independent. A vector index or document
search API cannot replace transactional run state or the LangGraph checkpointer.

## Application repository contract

Preserve the implemented `RunRepository` from `apps/orchestrator/storage/ports.py`. The adapters are `apps/orchestrator/storage/sqlite.py` and `postgres.py`; selection
already lives in the coordinator storage factory, not routers or graph nodes.

```python
async def replay(brief: Brief, key: str, parent_id: str | None = None) -> Run | None: ...
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

`replay` performs the same key/hash check without creating or dispatching work.
The coordinator checks it before maintenance/provider readiness, so an accepted
request remains readable even when new paid work is temporarily blocked.

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

## Postgres option (implemented, Jared)

- **Selection.** Set `DATABASE_URL` on the coordinator to select
  `PostgresRunRepository` (`apps/orchestrator/storage/postgres.py`) and
  `AsyncPostgresSaver`. Without `DATABASE_URL`, local SQLite is unchanged.
  - Startup applies the idempotent run schema v1 and runs the Postgres
    checkpointer `setup()` once per lifespan, before storage is handed to writers.
  - Both checkpointers restrict MsgPack deserialization and disable the pickle
    fallback.
  - One coordinator uses two Postgres connections. Keep one coordinator replica
    and the one-active-run database constraint.
- **Connection.**
  - Non-loopback `DATABASE_URL` connections require `sslmode=verify-full`
    (enforced by `factory.py`).
  - Host: `waffle-spark.taild4c940.ts.net:5432`. Its Tailscale-issued (public CA)
    certificate matches that name.
  - Use `sslrootcert=/etc/ssl/certs/ca-certificates.crt`; psycopg's bundled libpq
    does not resolve `sslrootcert=system`. Never connect by IP with checks
    disabled.
  - The ready-made URL is in AWS Secrets Manager `/company-brain-hackathon/database`
    (`DATABASE_URL`). Inject it into the coordinator only.
- **Role.** `toir_runs`: LOGIN, CONNECTION LIMIT 10, no CREATEDB, CREATEROLE or
  superuser. pg_hba allows it only via `hostssl` to `toir_runs` /
  `toir_runs_test`; every other database rejects it, and plaintext is rejected
  even on loopback.
- **State.** The production run and checkpoint schemas are initialized with no runs.
- **Cutover.** Drain active work, then export and validate historical records and
  evidence. Keep the SQLite checkpoint backup for rollback. Start new runs on the
  empty Postgres checkpoint store; never translate or resume old SQLite checkpoint
  blobs. Retry interrupted historical runs under a new run ID from their preserved
  report/evidence.
- **Tests.**
  - `TEST_DATABASE_URL` points at `toir_runs_test` (127.0.0.1, `sslmode=require`).
    Each session creates a throwaway schema with `search_path` isolation, resets
    tables between tests and drops the schema at teardown.
  - `tooling/tests/test_research_storage.py`: 27 passed on both backends.
  - SQLite snapshot/export tests remain SQLite-only.
- **Known differences from SQLite.**
  - Concurrent same-key creates across connections resolve as a replay, not a
    `Conflict`.
  - Integrity errors surface as `psycopg.IntegrityError`.
  - `BIGSERIAL` can leave gaps after failed inserts; sequences stay monotonic.

## Configuration and infrastructure changes

1. Reuse the implemented adapter and storage factory. `DATABASE_URL` selects
   Postgres; missing `DATABASE_URL` continues to select local SQLite.
2. Consume the existing `/company-brain-hackathon/database` secret via its exact
   ARN; CloudFormation must not create a duplicate named secret. The deployment
   synchronizer injects it only into the coordinator and omits it from logs/traces.
   The API, web and both research workers have no database credentials.
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


## Prospecting workflow extension (October 7 implementation)

The additive `sales_schema_version` / `sales_records` schema preserves research
v1's tables and one-active-run index. `SalesStore` reuses the repository connection
and transaction lock; Postgres transactions take a sales advisory lock, SQLite
uses `BEGIN IMMEDIATE`. Versioned JSON records include workspace membership,
sessions/messages, settings, jobs/leases, contact evidence/proposals/revisions,
approval decisions, CRM-operation journals, identities/auth sessions and memory
outbox jobs. Current models and API contracts are in
[prospecting-contract.md](prospecting-contract.md).

`tooling/migrate_research.py` exports a validated SQLite application snapshot and
imports into an empty Postgres run store in one transaction. It preserves all
payload data, source text, IDs, request hashes and ordered events, verifies
canonical hashes, then advances the PG event sequence. Active runs, duplicates,
missing parents/event references and a nonempty destination abort import. This
is historical research migration only; it does not import browser demo fixtures,
copy checkpoint blobs, or replace a full workflow backup.

The operator must record a verified full Postgres backup reference and stop
writers before import. Once sales data exists, backups must include its tables,
CRM journals and outbox. The SQLite operator backup/restore now checks the live
backend and stops when Postgres is active, even if retained SQLite files exist.
Spark's owner supplies PG17 dump/restore verification and a release handoff; see
[operations](../docs/operations.md) and the
[implementation status](prospecting-implementation.md).


## Google identity and browser sessions

The [auth handoff](auth-handoff.md) specifies additive member fields, issuer/subject
bindings, user UUIDs and revocable browser sign-ins in the existing sales records.
No new database connection or table is required. Existing conversation/audit email
keys and Brain `as_user` email mapping are preserved. Legacy cookies require a
fresh sign-in; all non-auth records remain valid.
