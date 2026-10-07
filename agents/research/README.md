# Toir research agent

A single-concurrency internal Bun service embedding Oh My Pi 18.8.3. The coordinator owns durable state; this pod holds only temporary sessions. Restarting it drops sessions and returns 404 for lost task IDs, allowing the coordinator to mark runs interrupted instead of silently repeating paid work.

## Development

Use Bun **1.3.14**. Run `bun install --frozen-lockfile --ignore-scripts`, `bun run typecheck`, and `bun test` in this directory. Production uses the digest-pinned amd64 Dockerfile under infrastructure/docker. Optional local inference packages are removed from the runtime image; OMP's native Linux bindings are retained. SDK tests construct the real restricted session without paid model calls.

`bun run start` listens on port 8000. Credentials come from Kubernetes Secrets synchronized from AWS Secrets Manager; do not write credentials into source, image files or command arguments:

- `RESPAN_API_KEY`
- `SCALEKIT_ENVIRONMENT_URL`, `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`
- `SCALEKIT_CONNECTION_NAME` defaults to `exa`; `SCALEKIT_ACCOUNT_ID` defaults to `toir` (Scalekit account identifier, not the `ca_` resource ID).
- `RESPAN_MODEL` defaults to `gpt-5.4`. The gateway URL is fixed to Respan and direct-provider fallbacks are disabled.

The Toir-owned Exa connected account must already exist in Scalekit. No Exa key is exposed to this pod. Only the read-only `exa_search`, `exa_crawl`, and `exa_find_similar` connector tools are registered. Exa-generated summaries and answer/research tools are disabled. OMP tool names use this exact prefix because the SDK aliases generic `search` to its built-in file search.

## Contract v1

- `GET /health`, `GET /ready`: process health; credentials may still be missing, with `configured: false`.
- `GET /v1/capabilities`: configuration status, missing credential **names**, supported capability and limits.
- `POST /v1/tasks`: version, task_id, run_id, brief, deadline_at, queries, focus, prior_report, cumulative usage. Returns 202. Repeating an identical canonical payload returns the existing task; changing its payload returns 409. A concurrent different task returns 409; missing credentials return 503.
- `GET /v1/tasks/:task_id`: status (`running`, `completed`, `failed`, `cancelled`), progress, cumulative usage, trusted sources, and terminal report or sanitized error. Unknown/lost sessions return 404.
- `POST /v1/tasks/:task_id/cancellation`: aborts active model work and suppresses further connector calls. An in-flight connector call may finish within its 25-second timeout.

The source registry is authoritative. Model-provided source text is discarded. IDs are `src_` plus the first 16 hex characters of SHA-256 over the canonical URL. Every citation must quote retrieved source text. The Python coordinator performs freshness and semantic qualification after this basic grounding check.

Usage counters are `searches`, `pages`, `model_turns`, `input_tokens`, and `output_tokens`. Limits apply cumulatively across follow-up passes: 30 searches, 60 page slots, 30 research model turns, and the original absolute deadline (never more than ten minutes from task acceptance). Search/similarity text retrieval reserves returned-page capacity before requests; retries also consume budget. Coordinator planning/review calls are counted separately. Provider cost figures are not fabricated.

The SDK has native GenAI OTel tracing with content capture disabled. W3C trace headers are extracted from task submission and run/task IDs accompany spans exported to Respan. Prompt/source content and credentials are not added to custom trace attributes.

The JSON schemas in `contracts/` are exported from the Python Pydantic models. When those contracts change, regenerate these snapshots and run tests on both runtimes. No runtime schema generation or Python dependency is needed in the research image.
