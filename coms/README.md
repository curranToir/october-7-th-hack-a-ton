# Frontend, prospecting and database handoff

`/coms` documents the boundaries between the browser, coordinator, research
workers, CRM integration and Spark-owned persistence/memory. The current sales
workflow replaces executable demo sessions/tasks with authenticated server state.
Historical demo fixtures remain test/reference data; never import their approvals
into the real workflow.

Google login, user records, browser sessions and the Cognee identity boundary are
documented in [auth handoff](auth-handoff.md).

Start with [prospecting contract](prospecting-contract.md) for current APIs and
[implementation status](prospecting-implementation.md) for validation and external
dependencies. [Database handoff](database-handoff.md) remains the research storage
contract; [Spark response](brain-api-response.md) records the owner handoff and
[EC2/Tailscale handoff](ec2-tailscale-handoff.md) records network checks.

## Workflow

**Discover company → qualify → research decision-makers → check shared HubSpot →
request approval → execute the approved changes.** Regular chat can request
research only or prepare a company for CRM. Ambiguous names are resolved before
enrichment. Discovery and contact research each have a separate worker slot;
waiting for approval holds neither.

Defaults: fit score at least 70, US companies with 20–1,000 employees, up to five
executives/budget owners, ten-minute per-run deadlines, 10 discovery batches and
25 background enrichment attempts per day in `America/Los_Angeles`. Explicit
chat requests have priority without consuming background quota. Pending proposals
are reused by canonical company domain; seven-day research and 30-day denial
suppression avoid unwanted repeats unless explicitly requested again.

## Frontend contracts and behavior

`types.ts` holds browser-facing contracts and `sales-client.ts` supplies the server API
adapter. The workspace hook loads authenticated server state, polls while open and
refreshes on focus. A proposal is shared between its originating session and the
Tasks queue, surviving reloads and navigation. Session messages link to task IDs.

An approval displays company/contact evidence and the exact proposed CRM fields,
associations and cited notes. Reviewers can remove contacts, fields or operations;
that creates a new proposal version. The decision endpoint atomically approves
or denies the exact version. Execution status is separate, including partial and
failed writes. Browser preferences can never bypass server approval.

The API facade forwards the opaque session cookie; verified server identity and
sales membership control every operation. Initial sales members are Curran and
Jared. HubSpot uses the shared Toir connection owned by `curran@toirinc.com`, with
requester and approver separately audited. No provider token is stored in browser
contracts or localStorage.

## Durable data and migration

The coordinator owns all durable writes. The existing research repository and
LangGraph checkpointer select Postgres using `DATABASE_URL`; SQLite remains for
local development. Additive sales schema stores sessions, jobs, proposals,
decisions, per-operation CRM journals, auth state and memory-sync work. Existing
research history and its one-active-run constraint remain intact.

The migration command validates and preserves complete historical research rows,
IDs, hashes, source text/citations and ordered events. It refuses active runs or
a nonempty destination. It does not translate checkpoints or import fictional
browser fixtures. After sales features are enabled, backup must cover the whole
database; research-only JSON is not a complete workflow backup.

## Validation and ownership

Run the repository's Python tests, Bun worker tests, frontend type checking and
browser flow checks before release. See the implementation status table for the
actual checks performed, including skipped external integration checks. Presence
of a source file or stored secret is not evidence of a live deployment.

Spark ownership remains with Jared: verified Postgres backups and restore checks,
Brain API deployment, pipeline grants for both sales users, and idempotent memory
ingestion. The EC2 release must consume those interfaces without impersonating an
owner or replacing Spark's in-progress service.
