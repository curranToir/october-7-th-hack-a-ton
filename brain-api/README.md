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

Demo: ask ENG an Acme question using `POST /recall` (`mode=answer` or `context`). Unreadable client layers are listed in `withheld` without content, titles or counts. Grant `acme-eng` from LEAD to ENG with `POST /grant`, repeat the question, then `POST /revoke`. `acme-commercial` stays withheld. `GET /access/<email>` returns owned/readable datasets. Both known sales identities have default read access to `toir-firm` and read/write access to `toir-pipeline`; no client-dataset grants are added. `POST /remember/research` requires the idempotency contract below and stores each cited lead in `toir-pipeline` as the authenticated request’s `as_user`, resolved by canonical dataset ID. `POST /forget` deletes only datasets owned by the requesting identity.

## Durable research ingestion

`GET /capabilities` requires the bearer token and returns `research_idempotency: true` once the receipt ledger is open, plus `research_writers` containing the known sales identities that currently have both read and write access to the canonical pipeline dataset. Startup adds only the `toir-pipeline` read/write grants for Curran, alongside the existing `toir-firm` read grant. The dataset owner remains Jared. Cognee receives the actual requesting user and the owner’s canonical dataset ID after both grants are checked by ID, preventing a same-named user dataset or an owner-privilege fallback. All research writes stay confined to that pipeline dataset.

`POST /remember/research` accepts the existing `{as_user, run_id, report}` body. Supply a lowercase 64-character hexadecimal `Idempotency-Key` header equal to `report.ingestion_id`. The report must contain a nonempty `leads` array (at most 100), and `sources` with unique IDs for all nested `source_id` references. The canonical request is limited to 2 MB. Each document preserves the complete lead object, including nested contact/workflow evidence, and all original report sources, with cited source records also identified separately. The original report is also retained in the local receipt ledger.

After every document returns Cognee's explicit `completed` result and all receipt commits succeed, the API returns exactly:

```json
{"dataset":"toir-pipeline","ingestion_id":"<the same 64-character ID>","documents":1}
```

Same-ID/same-payload requests replay that durable result without another model or ingestion call, including after restart. A changed canonical request under the same ID returns `409 ingestion_id_conflict`; mismatched/missing identity or invalid citations return `422`. Ingestion is anchored independently of the HTTP waiter: a disconnect or 30-second caller timeout leaves the same tracked task running, and concurrent matching requests await that task. Retry the same immutable payload and ID to receive its eventual receipt. Shutdown drains ingestion, with a 15-minute service stop window; a forced stop is handled as uncertain on restart. Requests serialize with existing pull, recall and permission work. Capability checks use initialized relational ACL reads independently of the writer lock, so a long graph build does not itself block readiness. Write authorization is checked again under the writer lock before ingestion.

Receipts live at `data/research-ingestion.sqlite3`, overridable with `BRAIN_RESEARCH_LEDGER`. The single writer holds an exclusive process lock for this ledger; a second Brain API instance fails startup. The ledger uses short SQLite transactions with WAL and full synchronous commits, independently of Cognee's Postgres/LanceDB/Ladybug stores. This small ledger does not use the nested SQLite ORM joins implicated in the earlier Cognee crash.

This is **durable idempotency, not an exactly-once transaction across Cognee stores**. A per-document started marker commits before calling Cognee. If the process crashes, is cancelled, receives an errored Cognee result, or cannot commit the completion receipt, the item becomes `uncertain` and returns `503 research_ingestion_uncertain` on subsequent attempts. It is never automatically re-ingested or falsely acknowledged. A failure after all document completion receipts commit can safely finish the acknowledgment on retry. Automatic `improve()` is excluded from research ingestion; completed `remember()` already performed graph ingestion, and extra improvement must not obscure receipt semantics.

`GET /remember/research/<ingestion_id>` is authenticated and returns only status, completed-document count, active-document index and recorded Cognee pipeline/content IDs. It never returns raw reports or source text. For an uncertain item, the Spark operator must inspect the corresponding Cognee pipeline/data records and reconcile against the saved document before making a recovery decision. Do not clear the receipt or generate a new ingestion ID simply to force retry: partially stored data may already exist. If completion cannot be established, restore a consistent backup or perform explicit owner-approved repair; the API intentionally provides no blind reset endpoint.

Stop all API/CLI writers before copying the receipt ledger and Cognee relational, graph and vector stores together. Retain them as one restore set; restoring only the ledger or only Cognee can invalidate acknowledgments. `POST /forget` invalidates pipeline receipts before deleting that owned dataset, so deleted memory cannot be acknowledged by an old receipt. Reusing such an ID returns `409 research_ingestion_forgotten`.

Deploy by stopping the existing Brain writer, backing up these stores, updating the code, and starting the same one-worker service. Startup creates the receipt table without changing existing Cognee content. The ledger is additive; keep it on rollback, and do not run an older non-idempotent research endpoint while the sales outbox is active. The alternate `data/research_ingestions.json` file is preserved. An empty file is compatible; a nonempty legacy receipt map blocks startup because it contains no request hashes or in-progress state. Reconcile those receipt IDs against saved immutable requests before deploying this ledger; never discard the file to force replay. No dependency changes are needed. Check `/health`, authenticated `/capabilities`, and both `/access/<email>` routes before enabling outbox delivery.

For the existing Spark checkout, an operator with systemd permission can use this sequence. Set `approved_commit` to the reviewed commit containing this change. Confirm that no separate manual server or CLI pull is running first. Existing local changes cause the fast-forward step to stop; do not reset or clean the checkout to bypass that failure.

```sh
set -eu
cd /srv/october-7-th-hack-a-ton
approved_commit='REPLACE_WITH_REVIEWED_COMMIT_SHA'
git fetch origin
git cat-file -e "$approved_commit^{commit}"
sudo systemctl stop brain-api.service
test "$(systemctl is-active brain-api.service)" = inactive
umask 077
brain_backup_dir="/srv/brain-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$brain_backup_dir"
docker exec oct7-rag-db-1 pg_dump -U rag -Fc cognee > "$brain_backup_dir/cognee.dump"
tar -C brain-api -cf "$brain_backup_dir/cognee-files.tar" .cognee data
git merge --ff-only "$approved_commit"
sudo install -m 0644 brain-api/deploy/brain-api.service /etc/systemd/system/brain-api.service
sudo systemctl daemon-reload
sudo systemctl start brain-api.service
sudo systemctl is-active brain-api.service
```

The backup parent directory must already be writable by the Spark operator. If there is no `data` directory on the initial deployment, create the empty directory before taking the file archive. If `SYSTEM_ROOT_DIRECTORY`, `DATA_ROOT_DIRECTORY`, or `BRAIN_RESEARCH_LEDGER` are overridden, include those actual paths in the same stopped-writer backup. These commands preserve `.env` and existing `.cognee` content and perform no reseed. Keep the backup private; it contains company data. Use the existing secret-aware verification client for authenticated probes rather than putting the bearer token on a command line.

Offline tests require only the application test environment's FastAPI, HTTPX and pytest; they do not import Cognee or call providers:

```sh
python -m pytest -q brain-api/tests
```

The completion check is based on the pinned [Cognee 1.6.3 RememberResult implementation](https://github.com/topoteretes/cognee/blob/v1.6.3/cognee/api/v1/remember/remember.py). Cognee content-hash skipping alone is not used as proof that a prior interrupted graph build completed.

CLI: `serve`, `pull --as-user EMAIL --sources ... [--from-recorded]`, `grant --owner EMAIL --grantee EMAIL --dataset NAME`, `access --user EMAIL`, `auth-links --user EMAIL`. Session ids are passed only to Cognee recall session memory, never to cognify.

Default LLM: `openai/claude-haiku-4-5` through `https://api.respan.ai/api`; default embedding endpoint: `http://127.0.0.1:8101/v1`, model `nemotron-embed`, 2048 dimensions. ACL is always enabled before importing Cognee. The unpublished `cognee-community-observability-respan` adapter is deliberately omitted: direct `respan-ai` workflow/task spans and gateway logs provide tracing. Cognee relational storage uses the local Postgres database `cognee` as `rag`, mapping `POSTGRES_PASSWORD` to `DB_PASSWORD` in memory; Ubuntu SQLite 3.45.1 segfaulted on Cognee's nested user joins. LanceDB and Ladybug stay under `.cognee/`. One process owns those stores; background writers are serialized by a process-local lock. Stop the manual server before starting the systemd unit or any CLI writer.

Google Drive was dropped for this demo; supported pull sources are `slack`, `github`, and `hubspot`. GitHub repositories live in `Toir-FDE-Team`; their Scalekit connection is `github-connect`, while their source label remains `github`.

Infrastructure: compose project `oct7-rag` provides Postgres on loopback and `100.87.113.122:5432` with TLS, and embedding only on loopback `:8101`. MagicDNS `waffle-spark.taild4c940.ts.net` uses a Tailscale-issued certificate. The `toir_runs` role may access only `toir_runs` and `toir_runs_test`, using TLS over loopback or tailnet. The installed `brain-api.service` remains disabled/inactive until Main starts it. Curran can use `sudo oct7-rag-ctl ps`, `logs [-f] [db|embed|brain]`, and `restart [db|embed|brain]`.

Acceptance probes (not test suites): `python -m brain.check` checks the empty-brain HTTP contract against a running manual server. Stop it, then `python -m brain.scratch_check` performs one real remember/recall in temporary directories and a disposable Postgres database, and deletes both afterwards.
