# Cognee technical feedback

Event: Build a Company Brain, October 7, 2026. Environment: Cognee 1.6.3,
Python 3.12, Ubuntu on a DGX Spark, local Nemotron embeddings, Ladybug graphs,
LanceDB vectors and Postgres relational storage. Text-model calls use Respan.

This is a public technical report prepared for the submission at the user's
request. It summarizes checked-in implementation evidence and the Spark
engineer's handoff; it is not a private participant sentiment log. Original
incident logs, exact timings, repeated-error counts and time lost were not
available to the submission author, and are not estimated here.

## Summary

Cognee enabled useful cross-source recall with per-client engineering/commercial
permissions and a live grant/revoke demonstration. Adding the missing CRM source
improved the recorded deterministic Q&A/access results from 4/11 to 11/11.
Most integration friction concerned configuration precedence, shared-dataset
storage lifecycle and knowing when ingestion was durably complete. A versioned,
multi-user reference service covering these boundaries would have saved work.

## Reported issues and workarounds

### Relational SQLite crashed during nested user lookups

- Report: Ubuntu SQLite 3.45.1 segfaulted on Cognee's nested user joins.
- Impact: the multi-user permission path could not reliably use that relational backend.
- Workaround: use Postgres for Cognee's relational store. The separate application
  receipt ledger uses simple SQLite transactions and does not use those ORM joins.
- Requested improvement: document tested SQLite/platform combinations and add a
  regression covering nested user/dataset authorization. Detect or clearly warn
  about an unsupported runtime before ingestion begins.
- Evidence: [Brain README](README.md), [configuration](brain/config.py).
- Confidence: engineer-reported failure with an implemented workaround; no crash
  traceback or independent root-cause reproduction is attached.

### Dotenv reload could overwrite enforced configuration

- Report: Cognee reloaded dotenv with `override=True`, undoing explicit ACL/default
  configuration unless the service controlled which env file Cognee loaded.
- Workaround: set `COGNEE_ENV_FILE` to an intentional file and enable
  `ENABLE_BACKEND_ACCESS_CONTROL` before importing/initializing Cognee.
- Requested improvement: document and test configuration precedence; explicit
  application policy should survive initialization. Expose a sanitized effective
  configuration/readiness check without secrets.
- Evidence: [configuration and explanatory comment](brain/config.py),
  [dedicated Cognee env file](brain/cognee.env).

### Shared datasets needed explicit graph-engine lifecycle handling

- Report: kept-alive Ladybug engines held locks when a shared dataset was opened
  across different users.
- Workaround: set `SUBPROCESS_IDLE_TTL_SECONDS=0` so cached graph engines close
  on release; keep one Brain process responsible for local graph/vector stores.
  The code notes that this trades slower cold opens for reliable shared access.
- Requested improvement: provide documented acquire/release semantics for
  multi-user shared-dataset access and test grant → recall as a second user → revoke.
- Evidence: [engine-release configuration](brain/config.py),
  [change introducing the workaround](https://github.com/curranToir/october-7-th-hack-a-ton/commit/34912f4f438bf28744072052ae2d63d344aeccc2),
  [Brain README](README.md).
- Confidence: reported runtime behavior plus checked-in lifecycle handling;
  exact lock exception and incident duration are unavailable.

### The suggested Respan adapter was not installable

- Report: `cognee-community-observability-respan` was unavailable from PyPI.
- Workaround: use direct `respan-ai` workflow/task instrumentation and gateway logs;
  the service does not configure the unpublished adapter.
- Requested improvement: keep published installation instructions synchronized
  with available distributions and provide a supported minimal tracing example.
- Evidence: [configuration](brain/config.py), [workflow/task spans](brain/memory.py).
- Confidence: engineer's installation report; availability was not independently
  retested during submission preparation.

### Documentation/API versions did not always line up

- Report: documentation encountered during the build recommended the removed
  `temporal_cognify` API.
- Requested improvement: version documentation and examples alongside released
  APIs, publish a migration path and execute example snippets in release CI.
- Evidence: [submission handoff](../coms/submission-todo.md#optional).
- Confidence: reported documentation friction; the exact page/version and error
  output were not retained, so this is not a claim about today's documentation.

### Ingestion completion needed a durable receipt boundary

- Need: retry a research report after an HTTP timeout/restart without silently
  duplicating ingestion or acknowledging a partially built graph.
- Workaround: the application records immutable request hashes and per-document
  start/completion receipts, requires Cognee's explicit completed result, and
  marks ambiguous outcomes uncertain for reconciliation. Shared writes use the
  canonical dataset ID and the actual requesting user after checking grants.
- Requested improvement: offer a documented idempotency key, durable ingestion
  handle and status/recovery contract spanning graph/vector work. Clearly state
  what content-hash skipping and a successful `remember()` result guarantee.
- Evidence: [ingestion contract](README.md#durable-research-ingestion),
  [Cognee result validation](brain/research_cognee.py), [receipt ledger](brain/research_ledger.py).
- Qualification: this is an integration requirement, not a claim that Cognee
  promised a cross-store exactly-once transaction. The newer receipt implementation
  is committed separately from the still-running legacy Spark acknowledgment path.

## What worked

- Dataset grants and revokes supported a clear two-user access demonstration;
  an engineering grant did not expose the commercial dataset.
- A custom graph model and provenance tags supported answers across messages,
  engineering records and CRM notes.
- Local embeddings worked alongside a separate hosted LLM gateway.
- Recorded before/after evaluation made the value of an additional source measurable.

Outcome: Cognee remains the company-memory implementation. No personal sentiment,
private source text, credentials, participant identifiers or invented timings are included.
