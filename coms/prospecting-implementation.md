# Continuous prospecting implementation status

October 7, 2026 (`America/Los_Angeles`). This document records repository work;
no AWS deployment, Spark mutation or live CRM write was performed for the current
implementation. Earlier handoff observations are explicitly historical below.
The definitive API/model contract is [prospecting-contract.md](prospecting-contract.md).

## Integration status

| Area | Current evidence | Remaining release work / owner |
|---|---|---|
| Research Postgres adapter | Existing repository/checkpointer retained; full Python suite **360 passed, zero skipped**, using disposable local Postgres. | Validate production TLS/configuration before cutover; this was not a Spark test. |
| Additive workflow persistence | Reuses the existing connection/lock with additive sales schema; included in the local Postgres full suite. Production lifespan and maintenance race checks **2 passed**. | No production migration performed; verify cutover/backups. |
| Dedicated contacts worker | Separate Bun service/image and task slot; **13 contacts and 21 research Bun tests passed**, typecheck passed. | Coordinator/contact Docker builds succeeded; nonroot, network-isolated containers passed `/health` (200). Real provider acceptance remains after deploy. |
| Durable scheduler | Included in full Python suite; workflow suite **16 passed**, exercising real coordinator services with fake providers for chat and background paths. | Confirm all live readiness dependencies before enablement. |
| Sign-in and authorization | Scalekit server identity and opaque sessions; **65 auth checks passed**, including sales access boundaries. | Register exact callback `http://localhost:8080/api/auth/callback` and verify both users live. |
| Shared CRM | Agent verified live company/contact reads and all **11** required tool catalog entries. Local workflow tests assert zero mutations before approval and journal completed CRM IDs with fake providers. | Actual granted write scopes remain unconfirmed; coordinator attestation defaults false. A live mutation still requires a concrete approved application task. |
| Browser sessions/Tasks | Complete browser smoke with fake APIs passed: 390px viewport, session/message reload, edit to v2, approval/history with queued execution, settings and sign-out. Checked-in `apps/web/tests/prospecting-flow.js`. | Live authenticated/provider acceptance remains; demo fixtures cannot become real approvals. |
| Existing Spark secrets | CloudFormation references exact ARN parameters for existing `/database` and `/brain-api` entries; no duplicate resource creation. Host IAM and secret sync route DB/Brain credentials to coordinator only. | Run reviewed stack update + sync during deliberate cutover. No cloud mutation performed here. |
| Network/TLS | October 7 owner handoffs report EC2/pod TCP5432 with hostname/system-CA TLS and subsequent TCP8200 plus unauthenticated Brain `/health` 200. | Verify authenticated Brain contracts and database from the final deployed image; reachability alone does not satisfy workflow readiness. |
| Migration and backend-aware backup | Research migration export/import validates hashes/IDs/events, preserves evidence, rejects nonempty destination, resets sequence. PG explicitly refuses SQLite backup/restore, including rollback bypass. | Spark owner full PG17 dump/restore verification; operator records backup and stops writers before import. |
| Memory API and ACL | Current checked-in `remember_research` accepts both known users and retains nested citations; coordinator uses verified identity and durable outbox. Initial grants give Curran `toir-firm`, not `toir-pipeline`. | Verify deployed Brain health/version, grant Curran pipeline reads, implement missing idempotency and `/capabilities` below. |

## Spark-owner contract requirements

Keep authentication as the agreed static `Authorization: Bearer` over Tailscale.
No Scalekit M2M migration is implied. Continue using verified TLS for Postgres at
`waffle-spark.taild4c940.ts.net`, with the system CA bundle path in `DATABASE_URL`.
The production role is restricted and the coordinator remains one replica/two
connections. Workers/API/web receive no database or Brain bearer credentials.

Source inspection during implementation: `brain-api/brain/memory.py` now accepts
either registered user in `remember_research`; the prior Jared-only restriction
is historical handoff evidence, not the current checked-in behavior. The running
Spark version has not been verified in this implementation. `apply_initial_grants`
still grants Curran only `toir-firm`, and no `/capabilities` route is present.

`toir-pipeline` must be readable by the authorized sales members
`curran@toirinc.com` and `jared@neptuneops.com`. `/remember/research` must accept
both authorized sales identities; Curran must never be substituted with Jared.
Keep independent client engineering/commercial access restrictions for other
Brain datasets. `/recall` retains its `withheld` behavior; because sales sessions
are shared, the coordinator copies recall text only from `toir-pipeline` and
`toir-firm` intersected with both sales users' readable grants. A Jared-only
pipeline stays absent until Curran's grant is present; `as_user` remains the
actual requester. It never copies a caller's private client datasets.

Provide idempotent ingestion keyed by the coordinator's stable outbox item/run and
proposal version. A retry after an uncertain response must resolve to the same
stored report. Preserve nested contact evidence and retrieved source text, verified
user/session/run links, and proposal/CRM status in the report. Publish readiness
capabilities documenting that both sales identities have the intended grants and
that the idempotent ingestion contract is available. The concrete coordinator
checks are (the current Spark implementation needs this new `/capabilities`
endpoint):

```json
GET /capabilities
{
  "research_idempotency": true,
  "research_writers": ["curran@toirinc.com", "jared@neptuneops.com"]
}
```

`GET /access/{user}` must return a `readable` array containing `toir-pipeline`
for each user. The coordinator sends the same outbox ID in `Idempotency-Key` and
`report.ingestion_id` to `/remember/research`. The ID hashes the full immutable report,
including proposal ID/version/decision/execution and CRM operation results. A
changed report is a new event; retries keep the identical payload/key and must
not insert duplicate memory. Preserve `leads[].workflow` (session/proposal/version/status/
execution/requester/approver), `leads[].contacts` with nested citations and the
report-level `sources`. Research-only findings also synchronize without a proposal;
their workflow metadata includes `session_id`, `run_id`, `requested_by`,
`status: research_only` and `execution: not_requested`.

A successful `POST /remember/research` response (200/201) must return:

```json
{"dataset":"toir-pipeline","documents":1,"ingestion_id":"<exact Idempotency-Key>"}
```

The document count must be a positive integer and the ingestion ID must exactly
match the supplied idempotency key. A generic successful HTTP response does not
acknowledge durable memory. Outbox claims use 120-second leases to prevent
concurrent sends and recover cancellation. Until the contract is ready, records
remain pending and continuous dispatch stays blocked by readiness.

## Cutover and rollback

See [operations](../docs/operations.md) for commands. Back up/drain before
synchronizing `DATABASE_URL`; that secret changes which backend a restarted
coordinator uses. Historical research import is additive to an empty Postgres run
store; it never translates LangGraph blobs. Preserve both SQLite files and the
full PG pre-import backup. After new Postgres writes, reconcile/export those
records before reverting to SQLite.

`manage.py backup/restore` currently supports SQLite and explicitly stops for
Postgres. A stale SQLite directory is not a PG backup. Automatic deployments and
rollback also stop at that PG backup gate, requiring the Spark owner's verified
full-database backup and an operator-coordinated release. The research JSON export
is not sufficient once sessions, approvals, CRM operations and memory outbox exist.

## Verification in this implementation

The following results were reported by the root/integration agents unless marked
as this deployment agent's direct run. External provider calls were read-only.

- Final full Python suite: **360 passed, zero skipped** against a fresh disposable
  **local** Postgres cluster. Includes scheduler (18), CRM (29), workflow (16),
  Brain/evidence (15), storage and migration coverage. The cluster was stopped
  and its temporary directory removed. No Spark database was used.
- Production application lifespan and chat maintenance race: **2 passed**.
- Integrated workflow suite: **16 passed**, with real ChatService, SalesStore,
  scheduler, CRM planner, proposal service and executor, but fake providers. Chat
  and background scenarios assert zero CRM mutations before approval and persist
  returned CRM IDs on completion.
- Authorization suite: **65 passed**. Evidence/Brain boundary: **15 passed**.
- Bun: **13 contact + 21 research** tests passed; Node `/coms`: **13 passed**.
- Deployment agent's focused infrastructure/migration run after adding the
  attestation configuration test: **55 passed, 1 skipped** (no test DB configured
  in that subprocess). Root's full run above covered the migration round trip
  using local Postgres. Ruff and diff checks passed.
- Complete frontend smoke passed with fake APIs: 390px mobile viewport, persisted
  session/message reload, edit to proposal v2, version consistency, duplicate
  approval prevention, pending/history with queued execution, settings and
  sign-out. Test is checked in at `apps/web/tests/prospecting-flow.js`.
- Live CRM: company/contact reads and 11 tool catalog entries verified. The vendor
  catalog declares search `filterGroups` as a string; live calls confirmed its
  pass-through execution requires the native array, which the adapter supplies.
- Coordinator and contacts Docker images built successfully. Both ran as nonroot
  with networking disabled and returned HTTP 200 for `/health`; smoke containers
  were removed. No AWS deployment, live Spark mutation or live CRM write occurred.

## CRM provider readiness attestation

`SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED=false` is configured only in the coordinator
and `.env.example`. ACTIVE account status and the scoped tool catalog cannot prove
OAuth grants when credential metadata access is disabled. An operator may attest
`true` after checking required companies/contacts read/write grants and note/
association access, or after a successful explicitly approved application task.
The attestation controls readiness only; every actual mutation still requires the
persisted, version-specific approval and deterministic execution checks.

Local implementation and validation are complete. Production readiness remains
conditional on the explicitly listed callback, CRM scope verification, Spark
capability/permission/acknowledgement contract and operator backup/cutover steps.
