# Operating Toir prospecting

Defaults are AWS account `904469541651`, region `us-east-1`, stack
`company-brain-hackathon`. Global CLI flags override them and every cloud command
checks the authenticated account. Run commands from the repository root.

The dedicated Ubuntu 24.04 `t3.medium` has a 30 GiB encrypted gp3 root disk, a
public IPv4 address for outbound internet, IMDSv2 and no security-group inbound
rules. SSM provides administration and the browser tunnel. The EC2 role can read
this project's release objects and exact Respan/Scalekit/database/Brain API secret ARNs, and
read/write only the artifact bucket's `backups/` prefix. It cannot read the Exa
setup secret. Operators need CloudFormation/IAM/EC2/S3/SSM permissions and secret
write permission. Treat SSM Run Command as privileged host access.

## Provision or update infrastructure

Install locked development dependencies (`uv sync --frozen`) for the operator
CLI's boto3 and PyYAML dependencies. The remote runner requires only Python's
standard library, AWS CLI and K3s.

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py provision
# Existing foundation: add exact IAM references to the existing Spark secrets.
.venv/bin/python infrastructure/deployment/scripts/manage.py stack-update
```

Provision creates a missing stack and waits for SSM, cloud-init, a Ready node and
Traefik. Existing stacks are only checked. `stack-update` preserves the deployed
Host resource, pins the running AMI instead of re-resolving Canonical's current
SSM parameter, and inspects a change set before execution. It resolves the existing database and Brain API secret ARNs through metadata-only
`describe-secret` calls; it does not create duplicate Secrets Manager resources.
It refuses changes outside IAM, artifact bucket lifecycle and the three
stack-owned runtime/setup secrets, and
rejects replacement/removal. It does not update user data or replace EC2.

Fresh bootstrap pins K3s `v1.36.5+k3s1` and AWS CLI 2.35.6. The K3s binary and
installer have committed SHA256 hashes. The initial AMI resolves from Canonical's
Ubuntu 24.04 amd64 SSM parameter. K3s supplies containerd, DNS, Traefik and metrics.
Application images build locally for `linux/amd64`, including from Apple Silicon.

## Connect providers and enter secrets

Create the Toir Exa API-key connection in Scalekit first, using its connector
setup flow. Record the environment URL, server client credentials, connection
name and connected-account identifier. Use `toir` as the account identifier when
creating a new account. Connector/account identifiers must match Scalekit exactly.
The approved tools are search, similar-company discovery and page retrieval;
generated-answer and autonomous Exa research tools are not enabled.

CloudFormation creates three empty retained Secrets Manager entries. The Spark
owner supplies the existing database and Brain API entries separately. Enter the
stack-owned JSON
fields through hidden local prompts; do not paste credentials into shell commands:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-set --name respan
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-set --name scalekit
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-set --name exa
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-sync --restart
```

Required fields:

| Secret | JSON fields |
| --- | --- |
| `/<stack>/respan` | `RESPAN_API_KEY` |
| `/<stack>/scalekit` | `SCALEKIT_ENVIRONMENT_URL`, `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`, `SCALEKIT_CONNECTION_NAME`, `SCALEKIT_ACCOUNT_ID` |
| `/<stack>/exa` | `EXA_API_KEY` |
| `/<stack>/database` (existing, Spark-owned) | `DATABASE_URL` |
| `/<stack>/brain-api` (existing, Spark-owned) | `BRAIN_API_URL`, `BRAIN_API_TOKEN` |

`secret-set --stdin` accepts the complete JSON object from a secure producer's
pipe without creating a file. The command writes directly with the AWS SDK;
values are never command arguments or logged output. Do not use a shell literal
containing the credential. Exa rotation also requires updating Scalekit's vault.

`secret-sync` updates Kubernetes Secrets but existing processes retain their old
environment. Add `--restart` to drain and restart the coordinator and both research
worker pods. On a fresh server use `secret-sync` without restart, or deploy: deployment
always synchronizes before applying the new manifests. Empty provider secrets
are an expected setup state; IAM/auth failures are errors. Missing credentials
keep process health green but research capability blocked. No placeholder key
is supplied and no direct LLM provider fallback exists.

## Releases and deployment

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py build
.venv/bin/python infrastructure/deployment/scripts/manage.py deploy
```

Build freezes tracked and unignored source in a temporary directory, including
uncommitted changes. Never track credentials. The source-content hash supplements
the Git SHA. Locked dependencies and five production Dockerfiles produce amd64
images. The archive contains images, rendered manifests, release metadata and
checksums; its ID combines commit SHA and archive hash prefixes.

Deploy uploads the archive to private S3, blocks new discovery, contact and CRM dispatch and waits up to
660 seconds for active work to finish. Once persistent state exists, it makes a
consistent backup before changing workloads. It then verifies every release
checksum, imports containerd images, synchronizes runtime secrets and applies
manifests. Readiness and ingress checks must pass before maintenance is cleared
and the successful release pointer changes. Imported images use pull policy
`Never`. Concurrent deployment/backup/rotation operations are locked out.

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py deploy --archive .deployment/releases/RELEASE_ID.tar.gz
```

The host retains current and previous successful release payloads. Failed
artifacts remain until the next success; S3 retains release versions until
operator cleanup. At least 3 GiB disk space must be free before deployment.
Diagnostics print pod state/reasons, without raw pod logs or secret environment
values. Use application trace IDs in Respan for detailed model/tool diagnostics.

## Access, checks and resource measurement

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py tunnel
# Open http://localhost:8080 while the command stays running.
.venv/bin/python infrastructure/deployment/scripts/manage.py verify
.venv/bin/python infrastructure/deployment/scripts/manage.py verify --agent-template
.venv/bin/python infrastructure/deployment/scripts/manage.py status
```

The tunnel needs the local Session Manager plugin; `tunnel --port 8081` changes
its local port. AWS IAM/SSM controls tunnel access; Scalekit sign-in separately enforces the sales
workspace. Configure `SALES_PUBLIC_URL=http://localhost:8080` and register exactly
`http://localhost:8080/api/auth/callback` in Scalekit. Loopback HTTP uses cookies
without Secure; a remote browser origin requires HTTPS and Secure cookies. There
is no public DNS or public TLS endpoint in this stack. Internal traffic uses HTTP.

Verify checks node and deployment health, web/API ingress, internal coordinator
connectivity and lack of application Kubernetes privileges, then prints CPU,
memory and disk usage. The optional template check starts a temporary independent
agent pod using the coordinator image and writable `/tmp` data, probes it and
removes the Deployment, Service and policy. It is not a production agent.

Before adding agents or increasing concurrency, compare idle usage with a complete
representative research run. Missing provider credentials prevent a real research
acceptance run; health checks alone do not prove provider execution or trace delivery.

## Backup, restore and rollback

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py backup
.venv/bin/python infrastructure/deployment/scripts/manage.py restore --backup-id BACKUP_ID
.venv/bin/python infrastructure/deployment/scripts/manage.py rollback
.venv/bin/python infrastructure/deployment/scripts/manage.py rollback --previous
.venv/bin/python infrastructure/deployment/scripts/manage.py maintenance
.venv/bin/python infrastructure/deployment/scripts/manage.py maintenance --resume
```

When the live coordinator uses SQLite, backup blocks admission, drains active
research/contact/CRM work and uses SQLite's online backup API
for both `runs.sqlite` and `checkpoints.sqlite`. It validates integrity and schema
version, then uploads an encrypted archive and embedded checksum/source-release
manifest under `backups/`. The latest ID/hash is recorded on the host. Bucket
lifecycle expires backup objects and noncurrent versions after 30 days. Release
objects are not subject to this backup expiry rule.

Restore validates the complete archive, checksums, schema and SQLite integrity
before touching live state. It first creates a protective backup of current
records, scales the coordinator to zero and waits for its pod to disappear.
It replaces both databases, removes old WAL/SHM sidecars, restores UID/GID 1000
ownership, restarts the pod and clears maintenance. Failure while replacing files
leaves the writer stopped; inspect the host and restore the protective backup
before resuming. No database format migration is performed by restore. If `DATABASE_URL` is active,
backup and restore fail closed before touching local files. They never label the
retained SQLite volume as a Postgres backup. Postgres backup also gates automatic
deployment/code rollback; follow the Spark procedure below.

`rollback` restores the recorded successful release after a failed rollout;
`--previous` switches to its predecessor. Cached payloads are verified and images
reimported. A rollback to the older three-pod foundation removes the research
Deployment, Service and network policy while preserving its coordinator PVC and
records. A subsequent upgrade snapshots those retained databases without calling
the absent maintenance API only after confirming that the deployed coordinator
image matches the recorded foundation release and no pod still mounts the claim.
Unknown images or lingering research pods stop that offline backup. Restore into
a running database requires deploying the research runtime first. Code rollback
does not roll back database contents. If the coordinator
is unhealthy, rollback preserves the volume and reports that it could not make
a fresh backup. A successful rollback clears admission maintenance; a failed
rollout leaves maintenance enabled until recovery.

K3s and SSM restart on reboot. Local state survives pod replacement and reboot,
but not loss of the EC2 root disk. For disk loss, provision a replacement research
runtime, set provider secrets, then restore a verified S3 backup. The existing
S3 artifact bucket and Secrets Manager values are retained on stack deletion;
recover with their retained resources rather than creating a conflicting same-name
stack blindly. See [database handoff](../coms/database-handoff.md) for migration.

## Spark Postgres backups and cutover

The PostgreSQL adapter already exists. Set `DATABASE_URL` only after deliberate
cutover; synchronizing its secret changes the next coordinator process's backend.
Use `waffle-spark.taild4c940.ts.net:5432`, `sslmode=verify-full`, and
`sslrootcert=/etc/ssl/certs/ca-certificates.crt`. The coordinator image explicitly
installs CA certificates. Never connect by IP or disable certificate checks.

The EC2 backup command currently supports SQLite only. It rejects active Postgres,
including automatic pre-deploy and rollback backups. Coordinate Postgres backup
and deployment with the Spark owner; there is no flag that treats a local SQLite
snapshot or research-only JSON export as a full Postgres backup. This is an
explicit operational dependency before unattended deployments on Postgres.

The Spark owner must use PostgreSQL 17 `pg_dump --format=custom` with a trusted
service/pgpass configuration (no credentials in command arguments), record the
server version and dump checksum, and retain the encrypted backup outside the
Spark disk. Include the complete `toir_runs` database: research rows, sales rows,
operation journals, memory outbox, auth/session state and LangGraph checkpoints.
Verify `pg_restore --list`, restore into an isolated scratch database/schema with
no app writer, and compare table counts and key approval/operation records.
Only then record the backup reference in the deployment handoff. PostgreSQL
restore is an owner-run process; this CLI never writes a PG dump into the SQLite
volume or translates checkpoint formats.

For the initial SQLite → Postgres cutover:

1. Enter maintenance and drain all worker/CRM activity. Back up both SQLite files
   using the existing operator command. Retain the validated archive and hashes.
2. Have the Spark owner verify a pre-import full Postgres backup. Stop coordinator
   writers. The destination research tables must be empty. Sales tables are not
   imported from browser demo data.
3. Export and validate application rows from the verified SQLite snapshot:

   ```sh
   .venv/bin/python -m tooling.migrate_research export --source /secure/snapshot/runs.sqlite --output /secure/research-migration.json
   .venv/bin/python -m tooling.migrate_research validate --input /secure/research-migration.json
   # Inject DATABASE_URL securely into the process environment before this command.
   .venv/bin/python -m tooling.migrate_research import --input /secure/research-migration.json --backup-reference VERIFIED_BACKUP_ID --writers-stopped
   ```

4. The importer validates every Run/event and request hash, rejects active runs,
   duplicates/orphans and nonempty destination tables, preserves original IDs,
   event sequence and full source text, checks canonical record hashes inside the
   transaction, then advances the PG event sequence. It never imports checkpoint
   blobs. Keep the SQLite checkpoint archive for rollback; new Postgres runs start
   with the existing empty checkpoint store.
5. Synchronize secrets and start one coordinator with the verified Postgres URL.
   Validate history, a new run, retry/cancellation, sign-in as both sales members,
   proposal visibility and mandatory approval before enabling background work.
6. Before any new PG writes, rollback may restore the validated SQLite snapshot
   and old configuration. After new writes, drain and export/reconcile them first;
   never revert in a way that silently drops decisions or completed CRM operations.

The migration JSON contains source text and potential business contacts. It is
created mode 0600 and belongs in secure operator storage, not Git/release bundles.
The importer is specifically for historical research v1 records. Once sales
features are active, use a full DB backup; this research-only export cannot restore
sessions, approvals, CRM operations or outbox work.

## Continuous-dispatch readiness

New workspaces start with persisted `Automation.enabled=false`; authenticated
automation settings and readiness must both permit dispatch. Before enabling, verify Postgres and TLS,
Scalekit login/callback, both Exa workers, shared HubSpot tool permissions, and
Spark `/recall` + idempotent `/remember/research` for both sales identities.
Scalekit's ACTIVE connection and scoped tool catalog do not expose granted OAuth
scopes in this environment. The coordinator-only
`SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED` defaults to `false`; therefore reads alone
do not mark CRM write readiness. An operator can set it to `true` only after
confirming the connection's required companies/contacts read and write scopes
(and access for the configured note/association tools), or after a successful
concrete approved application task demonstrates those writes. This attests to
provider configuration; it never bypasses proposal-version approval or CRM
baseline revalidation. Do not enable it in web/API/worker environments.

Check `/api/sales/capabilities` while authenticated. The application must retain
pending memory-sync work while the Brain API is unavailable.

Keep one coordinator, one discovery worker and one contacts worker. Defaults are
10 discovery batches and 25 contact-enrichment attempts per Los Angeles day,
score threshold 70, US companies with 20–1,000 employees, and up to five contacts.
User chat requests have queue priority and per-run limits; every CRM mutation,
including chat-originated work, requires a persisted approved proposal version.

## Cleanup

EC2, EBS, public IPv4, S3 and Secrets Manager incur charges. Deleting this stack
removes the host/root disk and cluster state. Its retained bucket and secrets
need separate intentional cleanup after backup review. Scripts never delete the
stack, bucket or Secrets Manager secrets automatically.
