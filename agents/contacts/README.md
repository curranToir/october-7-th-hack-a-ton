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
`RESPAN_MODEL` to `gpt-5-mini`. Never configure a CRM connector on this worker.

`POST /v1/tasks` returns 202, or 200 for a retained task with an identical canonical
payload. A reused ID with different contents or a second concurrent task returns 409.
`GET /v1/tasks/{id}` returns progress, cumulative usage, retrieved source evidence and
the validated final report. `POST /v1/tasks/{id}/cancellation` requests cancellation.
Retained history is bounded to 20 tasks; unknown/lost tasks return 404 and require an
explicit coordinator retry. The service never replays work after restarting.

Each task is capped at 60 searches, 200 pages, 60 model turns and ten minutes from
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

Both workers share `agents/research/src/research/limits.ts`. Respan Chat Completions uses low reasoning and `max_completion_tokens=16384`, without `temperature` or legacy `max_tokens`. The application configures a conservative 272,000-token context ceiling; [GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) supports a 400,000-token context, 272,000-token maximum input, and 128,000-token maximum output. The tool response includes current remaining budgets so the model can reserve its final report turn. Retrieved evidence remains capped at 100 unique sources and 12,000 characters per source; terminal task retention remains 20.

The worker sends model-only source excerpts of up to 4,000 characters, including windows around headcount, leadership and buying-signal evidence, plus verified quotes needed by prior findings. The trusted evidence registry retains the original retrieved text. The SDK tokenizer requests a final report without tools at 220,000 input tokens and blocks requests above 264,000, leaving room for provider framing below the configured 272,000-token ceiling.
