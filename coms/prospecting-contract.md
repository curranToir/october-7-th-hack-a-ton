# Prospecting v2 implementation contract

Status: implemented and locally validated; production rollout remains gated by
the external dependencies below. October 7, 2026. This additive workflow uses
Jared's existing Postgres repository/checkpointer; it does not replace research v1.

## Responsibilities

Coordinator: sole durable-state owner, user authorization, scheduling, evidence
review, immutable proposal decisions, deterministic CRM execution and memory outbox.
Workers: temporary bounded public research sessions, no CRM write tools or DB access.
API: authenticated browser facade. Frontend: server-backed chats/tasks, existing UI.

## Shared Python models

Defined in `apps/orchestrator/sales/models.py`. All JSON uses snake_case. Session,
job and proposal IDs are UUID strings; message and outbox IDs are opaque and must
not be parsed as UUIDs. Workspace is `toir`; automation uses the singleton ID
`default` and starts disabled. Approved sales members are Curran and Jared.
Source and Citation reuse research v1 models, preserving original retrieved text.

## Storage interface

`sales/store.py`: `SalesStore(run_repository)` reuses its connection and lock.
`await store.setup()` applies additive schema. `async with store.transaction() as tx`
serializes all reads/writes in that transaction across processes (Postgres advisory
lock / SQLite BEGIN IMMEDIATE). Tx methods: `get(kind,id) -> dict|None`,
`list(kind) -> list[dict]`, `put(kind,id,payload)`, `delete(kind,id)`.
Kinds: member, session, message, automation, job, proposal, proposal_revision, decision, crm_operation,
outbox, auth_session, auth_flow, identity, suppression. Every payload is scoped to
workspace_id=toir. Store operates only on this workspace; kind allowlist and ID values
are bound parameters. Typed application models validate payloads at service boundary.
`store.is_postgres` reports backend. Snapshot reads also use transactions.

## Browser API

Auth routes: GET `/api/auth/login` (Google via Scalekit), GET `/api/auth/callback`,
POST `/api/auth/logout`. GET `/api/auth/sessions` lists the caller’s browser sign-ins;
DELETE `/api/auth/sessions/{uuid}` revokes one, DELETE `/api/auth/sessions` revokes all.
`/api/me` also returns a stable user `id` and `email_verified` flag.
See [auth handoff](auth-handoff.md) for persistence, rollout and Cognee identity.
GET `/api/me` returns authenticated email/name; no browser-supplied actor is trusted.
Browser facade forwards the session cookie to internal routes; coordinator validates
it from durable auth state and enforces membership. Mutations require same-origin
Origin checks. No user-provided as_user or connector identifier is accepted.

Prefix `/api/sales` (internal `/v1/sales`):
- GET `/workspace`: `{user, sessions, messages, tasks, jobs, automation, capabilities}`.
  `tasks` are Proposal objects; messages carry session_id and task_ids.
- POST `/sessions` `{title?}` -> Session (201).
- PATCH `/sessions/{id}` `{title}`; DELETE -> 204. Task audit remains.
- POST `/sessions/{id}/messages` `{content}` with Idempotency-Key -> Message (202).
- PATCH `/tasks/{id}` `{version, excluded_contact_ids, excluded_operation_ids, excluded_fields}` -> Proposal.
- POST `/tasks/{id}/decisions` `{version, decision:approved|denied}` with Idempotency-Key
  -> Proposal. Versions conflict with 409; decisions are atomic and immutable.
- POST `/tasks/{id}/retries` -> Proposal; only unfinished execution operations retry.
- PATCH `/automation` -> Automation (editable fields only).
- POST `/jobs/{id}/cancellation`, POST `/jobs/{id}/retries` -> Job.
- GET `/capabilities` -> `{ready, reasons:string[], postgres, research, contacts, crm, brain, auth}`.
Polling every five seconds and on window focus; 401 displays sign-in.

## Worker API

Contact service `/v1/tasks`: ContactTask (version=1; task_id,run_id,mode=resolve|enrich,
query,company?,deadline_at,prior_sources,usage). GET `/v1/tasks/{id}` -> ContactStatus
(status running|completed|failed|cancelled, progress, usage, sources, report?, error?).
POST cancellation; GET capabilities with configured/missing_credentials/limits.
ContactReport: candidates, company?, contacts, sources, gaps, summary. A resolve pass
returns company only for an unambiguous identity, otherwise candidates for user choice.
New JSON schemas exported from the Python models into agents/contacts/contracts.

## Operational defaults and dependencies

25 background enrichment attempts and 10 discovery batches per Los Angeles day;
score 70; US/20–1000 employees; five contacts; ten-minute task deadlines and existing
research budgets. Explicit chat work has priority and separate per-run limits.
Brain uses the static bearer from `/company-brain-hackathon/brain-api`. Spark owner
must grant Curran pipeline read access, confirm both identities can ingest, and
provide idempotent research ingestion with an explicit durable acknowledgement.
No impersonating Jared. Pending memory sync is retained. Continuous scheduling remains
blocked until its reported dependencies are ready. All CRM writes require an approved
persisted proposal, including requests originating in chat.


## Brain ingestion handshake

The coordinator requires `/capabilities` with `research_idempotency: true` and
`research_writers` containing both sales emails, plus `toir-pipeline` in each
user's `/access/{user}.readable`. The current checked-in Brain ingestion function
accepts both users, but initial grants only give Curran `toir-firm`; capability
handshake, pipeline read grant and durable idempotent acknowledgement remain
Spark-owner requirements.

`POST /remember/research` sends the same opaque stable ID in `Idempotency-Key`
and `report.ingestion_id`. The ID hashes the full immutable report, including
proposal/version/decision/execution state and CRM operation results. Each changed
report gets a new event ID; retries keep the identical payload/key. A successful
200/201 response must acknowledge the exact key:

```json
{"dataset":"toir-pipeline","documents":1,"ingestion_id":"<exact Idempotency-Key>"}
```

`documents` must be a positive integer. Unacknowledged responses do not complete
outbox work. Research-only reports also enter the outbox with workflow metadata
`session_id`, `run_id`, `requested_by`, `status: research_only` and
`execution: not_requested`, without requiring a proposal. A 120-second durable
outbox lease prevents concurrent sends and recovers after cancellation. Recall
copies only `toir-pipeline`/`toir-firm` text readable by both sales users into
shared sessions, while preserving the actual requesting identity in `as_user`.
