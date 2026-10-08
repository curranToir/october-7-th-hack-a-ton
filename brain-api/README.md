# Toir Company Brain

Cognee 1.6.3 with dataset ACLs, Scalekit per-user source connections, Nemotron embeddings, and a Respan LLM gateway. Run from `brain-api/` on the Spark, using Python 3.12.

## Setup → seed → authorize → pull → eval → demo

```bash
git clone https://github.com/curranToir/october-7-th-hack-a-ton.git
cd october-7-th-hack-a-ton/brain-api
cp .env.example .env
# Fill RESPAN_API_KEY, SCALEKIT_ENVIRONMENT_URL, SCALEKIT_CLIENT_ID,
# SCALEKIT_CLIENT_SECRET, BRAIN_API_TOKEN, POSTGRES_PASSWORD, TOIR_RUNS_PASSWORD.
# Leave other empty keys to use code defaults.
python3 -m venv .venv
.venv/bin/pip install -e .
docker compose up -d
# One-time relational database setup (if it does not already exist):
docker exec oct7-rag-db-1 createdb -U rag cognee
# Seed sandbox data following seed/ scripts and world.json instructions.
.venv/bin/python -m brain auth-links --user jared@neptuneops.com
.venv/bin/python -m brain auth-links --user curran@toirinc.com
# Open each printed link manually and approve the intended sandbox account.
.venv/bin/python -m brain serve
```

The API binds only to `100.87.113.122:8200` (or loopback). `/health` and `/graph?dataset=<name>` are public on that private network. All remaining routes require `Authorization: Bearer <BRAIN_API_TOKEN>`. Graph intentionally exposes the selected dataset to tailnet viewers; do not publish it outside the tailnet.

With the service running, use `POST /pull` with `{"as_user":"jared@neptuneops.com","sources":["slack","github"]}` and inspect `GET /pull/<job_id>`. Pull writes as the routed dataset owner, choosing that owner's ACTIVE Scalekit connection first and falling back to the other identity. Raw source JSON is stored under `data/recorded/`. The CLI can replay it with `pull --as-user ... --sources slack github --from-recorded`; do not run CLI writers concurrently with the service.

```bash
.venv/bin/python eval/run.py --label before
# POST /pull with sources ["hubspot"], then wait for done.
.venv/bin/python eval/run.py --label after
```

Demo: ask ENG an Acme question using `POST /recall` (`mode=answer` or `context`). Unreadable client layers are listed in `withheld` without content, titles or counts. Grant `acme-eng` from LEAD to ENG with `POST /grant`, repeat the question, then `POST /revoke`. `acme-commercial` stays withheld. `GET /access/<email>` returns owned/readable datasets. `POST /forget` deletes only datasets owned by the requesting identity.

`GET /capabilities` requires bearer auth and returns `{"research_idempotency":true,"research_writers":["curran@toirinc.com","jared@neptuneops.com"]}`.
Both users can read/write `toir-pipeline`; `POST /remember/research` writes each lead as the actual `as_user`, preserving workflow, nested contacts/citations, and report sources.
Send `Idempotency-Key` equal to `report.ingestion_id`; missing/mismatched keys return 400 (`idempotency_key_required`/`idempotency_key_mismatch`), and zero leads return 400 `empty_report`.
Success returns `{"dataset":"toir-pipeline","documents":1,"ingestion_id":"<exact key>"}` (count varies); retries return the same acknowledgement without ingesting again.
Acknowledgements persist atomically in `data/research_ingestions.json` under the single writer lock.

CLI: `serve`, `pull --as-user EMAIL --sources ... [--from-recorded]`, `grant --owner EMAIL --grantee EMAIL --dataset NAME`, `access --user EMAIL`, `auth-links --user EMAIL`. Session ids are passed only to Cognee recall session memory, never to cognify.

Default LLM: `openai/claude-haiku-4-5` through `https://api.respan.ai/api`; default embedding endpoint: `http://127.0.0.1:8101/v1`, model `nemotron-embed`, 2048 dimensions. ACL is always enabled before importing Cognee. The unpublished `cognee-community-observability-respan` adapter is deliberately omitted: direct `respan-ai` workflow/task spans and gateway logs provide tracing. Cognee relational storage uses the local Postgres database `cognee` as `rag`, mapping `POSTGRES_PASSWORD` to `DB_PASSWORD` in memory; Ubuntu SQLite 3.45.1 segfaulted on Cognee's nested user joins. LanceDB and Ladybug stay under `.cognee/`. One process owns those stores; background writers are serialized by a process-local lock. Stop the manual server before starting the systemd unit or any CLI writer.

Google Drive was dropped for this demo; supported pull sources are `slack`, `github`, and `hubspot`. GitHub repositories live in `Toir-FDE-Team`; their Scalekit connection is `github-connect`, while their source label remains `github`.

Infrastructure: compose project `oct7-rag` provides Postgres on loopback and `100.87.113.122:5432` with TLS, and embedding only on loopback `:8101`. MagicDNS `waffle-spark.taild4c940.ts.net` uses a Tailscale-issued certificate. The `toir_runs` role may access only `toir_runs` and `toir_runs_test`, using TLS over loopback or tailnet. The installed `brain-api.service` remains disabled/inactive until Main starts it. Curran can use `sudo oct7-rag-ctl ps`, `logs [-f] [db|embed|brain]`, and `restart [db|embed|brain]`.

Acceptance probes (not test suites): `python -m brain.check` checks the empty-brain HTTP contract against a running manual server. Stop it, then `python -m brain.scratch_check` performs one real remember/recall in temporary directories and a disposable Postgres database, and deletes both afterwards.
