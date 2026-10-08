# Toir prospecting architecture

Toir's sales team uses one coordinator, two public-research workers and a meeting
analysis worker.
The coordinator owns authorization, durable queueing, evidence review, versioned
approvals, deterministic CRM writes, meeting capture, GitHub publication and the
memory outbox.

```text
Sales browser → Scalekit sign-in → /api (facade) → coordinator
                                                 ├─ discovery → research worker → Scalekit Exa
                                                 ├─ enrichment → contacts worker → Scalekit Exa
                                                 ├─ meeting notes → meetings worker → Respan
                                                 ├─ mentioned subjects → research /v1/subjects → Scalekit Exa
                                                 ├─ Zoom capture → Recall → signed callbacks → /api
                                                 ├─ approved meeting issue → GitHub
                                                 ├─ Postgres on Spark (or local SQLite)
                                                 ├─ approved CRM operations → Scalekit HubSpot
                                                 └─ outbox / recall → Spark Brain API → Cognee
Coordinator + workers → Respan gateway and traces
```

Research and contacts workers own temporary bounded Oh My Pi sessions with
restricted search/retrieval tools, source validation and usage limits. The meeting
worker performs structured Respan extraction with exact transcript evidence.
Workers do not open the database or receive CRM write tools. Pending approvals
consume no worker slot.
The original research repository and its one-active-research-run constraint stay
intact. Sales queues dispatch one discovery run and one contact task concurrently.

## State and access

`DATABASE_URL` selects the existing `PostgresRunRepository` and
`AsyncPostgresSaver`; absent means local SQLite. A single coordinator uses the
existing two database connections. Sales storage reuses the repository connection
and lock with additive `sales_schema_version` and `sales_records` tables. Records
include workspace membership, sessions/messages, jobs, proposals and revisions,
approval decisions, CRM operation journals, identity state and memory outbox jobs.
Postgres is authoritative for approvals and execution; Cognee unavailability does
not imply a CRM failure or lose a pending proposal.

Scalekit sign-in verifies identity before establishing a server-managed session.
Curran and Jared are the initial sales members. Browser actor claims and local
approval preferences cannot authorize CRM writes. Both use Toir's shared HubSpot
connection owned by `curran@toirinc.com`; requester and approver remain separate
audit identities. Coordinator-only `SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED` starts
false because ACTIVE connection/tool availability does not prove granted write
scopes; an operator verifies provider scope configuration before enabling it.
It never bypasses per-proposal approval. The shared `toir-pipeline` Cognee dataset requires explicit
permissions for both people. Shared recall checks the intersection of both users'
readable grants before copying pipeline/firm text to sessions; it keeps the actual
requesting user identity. Never impersonate Jared to bypass missing grants.

The browser follows the resource contract in
[`coms/prospecting-contract.md`](../coms/prospecting-contract.md). Sessions and
Tasks display the same versioned proposal. A decision is atomic; execution has
its own status and per-operation journal. A changed CRM baseline requires renewed
approval, and uncertain creates require reconciliation before another attempt.

Meeting transcripts, notes, tasks, signed callback inbox and immutable decisions
use the additive `meeting_records` table. Product issues require approval of the
exact task version before GitHub publication. Unknown publication outcomes require
read-only reconciliation and are never automatically resent. Public professional
research on explicitly mentioned companies/people uses dedicated `/v1/subjects`
jobs in the existing research worker. Those jobs have no sales lead geography,
headcount or buying filters. The coordinator persists subject task IDs, polls
results and stores cited identity facts, gaps and summaries inside the meeting;
they are separate from the discovery workspace's `/research` runs. See the
[meeting agent runbook](meeting-agent.md) for setup and the demo.

## Deployment and credentials

Six application pods run in `company-brain`: web, API, coordinator, research,
contacts and meetings. Each has one replica, zero update surge, no service-account token,
read-only root filesystem and bounded `/tmp`. Only the coordinator mounts the
retained 2 GiB data claim. The research and contacts worker images use the same
pinned Bun/Oh My Pi dependencies. Contact code keeps the repository directory
layout to import shared research tools and evidence handling. The meeting worker
uses the locked Python dependencies and an independent Uvicorn process.

Ingress is denied by default. Allowed paths are Traefik → web/API, web → API,
API → coordinator, and coordinator → each worker. Workers and coordinator have
no public ingress route. The existing browser entry is the SSM tunnel at
`http://localhost:8080`; Scalekit permits that exact local callback. Any remote
browser origin must use HTTPS. No public inbound EC2 rule is added.

Host secret access uses exact ARNs. Respan, Scalekit and the optional
`meetings-provider` group are stack-owned; database
and Brain API secrets already exist and are referenced by ARN parameters, never
recreated by CloudFormation. The coordinator receives Respan, Scalekit server
credentials, `DATABASE_URL`, `BRAIN_API_URL`, `BRAIN_API_TOKEN`, and configured
Recall/GitHub values from `meetings-provider`. Research and contacts receive
Respan and Scalekit Exa configuration; meetings receives Respan only. The direct Exa API key remains in its
setup secret and Scalekit's vault. API and web receive no provider credentials.

The dedicated t3.medium remains 2 vCPU/4 GiB. Each pod requests 100m CPU and has a
500m limit. Memory requests/limits: web 128/384 MiB, API 128/256 MiB, coordinator
256/512 MiB, research 256/512 MiB, contacts 256/512 MiB, meetings 256/512 MiB. Observe a representative
concurrent run before increasing limits; deployment adds no autoscaling or HA.

## Operations

Maintenance blocks new discovery, contact, CRM and meeting dispatch, and drains
all active work. `active_meeting_operations` includes remote subject task IDs;
during maintenance the coordinator only polls running subject jobs until they
finish, without admitting new jobs. Recall callbacks receive a retryable 503
during maintenance. Deployment and secret rotation use this aggregate view. Pending approvals
remain durable. Verified TLS uses the Spark FQDN and the coordinator CA bundle.
Brain API uses a static bearer over Tailscale, not a Scalekit M2M token.

SQLite backup/restore uses the online backup API and integrity/checksum validation.
When Postgres is active, these commands explicitly stop; retained SQLite files
are never presented as a Postgres backup. Spark's owner must retain and verify a
Postgres 17 dump covering research, sales and meeting records, and checkpoints. See
[operations](operations.md) for migration, readiness, backups and cutover.
