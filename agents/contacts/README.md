# Contact research worker

This bounded Bun/Oh My Pi worker resolves company identity and researches up to five
current purchasing stakeholders. Only public Exa tools are registered. The coordinator
owns durable jobs, identity, CRM proposals and decisions; this service cannot write CRM
records or access a database.

Install dependencies once in `agents/research` using its pinned Bun lock. The contact
worker imports that shared restricted harness, source registry, budgets, Scalekit Exa
transport and Respan telemetry. The Docker image retains both sibling source trees and
places the same dependencies in `/app/node_modules`.

From the repository root:

```sh
bun agents/contacts/src/server/index.ts
bun test agents/contacts/tests agents/research/tests
agents/research/node_modules/.bin/tsc -p agents/contacts/tsconfig.json --noEmit
python -m agents.contacts.contracts.export --check
```

Use `python -m agents.contacts.contracts.export` after changing the coordinator's
`ContactTask` or `ContactReport` Pydantic contracts. No independent wire schema is maintained.

`PORT` defaults to 8000. Required credentials: `RESPAN_API_KEY`,
`SCALEKIT_ENVIRONMENT_URL`, `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`.
`SCALEKIT_CONNECTION_NAME` defaults to `exa`; `SCALEKIT_ACCOUNT_ID` to `toir`;
`RESPAN_MODEL` to `gpt-5.4`. Never configure a CRM connector on this worker.

`POST /v1/tasks` returns 202, or 200 for a retained task with an identical canonical
payload. A reused ID with different contents or a second concurrent task returns 409.
`GET /v1/tasks/{id}` returns progress, cumulative usage, retrieved source evidence and
the validated final report. `POST /v1/tasks/{id}/cancellation` requests cancellation.
Retained history is bounded to 20 tasks; unknown/lost tasks return 404 and require an
explicit coordinator retry. The service never replays work after restarting.

Each task is capped at 30 searches, 60 pages, 30 model turns and ten minutes from
dispatch, including carried usage. Failed provider attempts consume budgets. Resolve
mode never returns contacts, and conflicting company identities become candidates.
Enrich requires a selected company. The host validates source quotes, company/name/role
linkage and field-specific LinkedIn/email/phone citations, drops unsupported contacts
or fields, and issues stable contact IDs. The coordinator applies its additional
semantic review before proposing any CRM write.

`GET /health` is process liveness. `GET /ready` requires credentials and rejects a
draining process; `/healthz` and `/readyz` are aliases. `GET /v1/capabilities` reports
credential names, readiness, active tasks and limits without revealing secret values.
SIGTERM/SIGINT reject new work, cancel active research and flush tracing.

Tests use the real restricted OMP session and a fake Respan stream/Scalekit transport;
they make no paid model, search or CRM calls.
