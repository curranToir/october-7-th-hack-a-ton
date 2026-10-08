# Agent runtime ↔ Spark integration

## Deployment record

This handoff accompanies the October 7–8, 2026 integration. The implementation,
local checks and live PostgreSQL cutover are complete; research acceptance is
being recorded below as it finishes. Historical fixtures and Cognee demo evaluations
are not new prospecting runs.

## Ownership and connections

- EC2: `i-0d5e3f4859de3ea93`, `100.86.7.62`, Kubernetes namespace
  `company-brain`. The application origin is
  `https://toir-hackathon.taild4c940.ts.net`.
- Spark: `waffle-spark.taild4c940.ts.net`, `100.87.113.122`. PostgreSQL uses
  port `5432`; the existing Brain API/Cognee service uses port `8200`.
- The coordinator owns PostgreSQL research runs, events, LangGraph checkpoints,
  sales sessions/jobs, and the memory outbox. Research and contact workers remain
  separate pods with temporary sessions. They return evidence to the coordinator
  and receive no PostgreSQL or Kubernetes administration credentials.
- The coordinator recalls shared permitted Cognee context before planning.
  Recalled material is a research hint, not verified public evidence. Accepted,
  sourced reports are saved to PostgreSQL and enqueued for `toir-pipeline` memory.
- Research completion and Cognee ingestion are separate states. The UI exposes
  pending, synced, blocked, and not-requested memory status. A provider outage
  does not erase accepted findings or turn an unacknowledged write into success.

## Credentials and access

AWS account `904469541651`, region `us-east-1` remain authoritative.
The EC2 role now references the exact existing secret ARNs for
`/company-brain-hackathon/database` and `/company-brain-hackathon/brain-api`.
Values enter only the coordinator's service-specific Kubernetes Secret.
`DATABASE_URL` requires `sslmode=verify-full` and the Spark DNS name, with
`sslrootcert=/etc/ssl/certs/ca-certificates.crt` in the Linux container.

The HTTPS origin is provided by private Tailscale Serve:

```sh
tailscale set --shields-up=false
tailscale serve --bg --https=443 http://127.0.0.1:80
tailscale serve status --json
```

No Funnel/public ingress was enabled. EC2's AWS security group still has no
inbound rules. SSM remains the administrative path. The Scalekit environment
has the exact HTTPS `/api/auth/callback` URL registered, alongside the existing
localhost callback. Runtime `SALES_PUBLIC_URL` selects the HTTPS origin; use
that origin for browser sign-in and writes.

Spark's existing operator account is `curran`; the Brain service runs as
`jlyon:hackathon`. Its restricted service control is
`sudo oct7-rag-ctl restart brain`. Preserve the engineer's checkout changes,
`.env`, `.cognee`, existing datasets, and persistent receipts during deployment.
The engineer is actively editing that checkout. This rollout leaves Spark's
files and process untouched and connects to its already deployed API. The
combined per-document SQLite receipt implementation on GitHub `main` requires
a later coordinated Spark update; do not describe it as deployed there.

## Persistence cutover and backups

The deployment tool stages the database credential until an explicit
`storage-cutover`. It first backs up SQLite and the destination PostgreSQL
database, stops the coordinator writer, and validates/copies terminal runs,
events, and sales/auth/outbox records. It compares record hashes before selecting
PostgreSQL and restarting the coordinator. SQLite checkpoint blobs are retained
in backup; new runs use the PostgreSQL checkpointer.

PostgreSQL backups use the pinned PG17 client, a consistent exported snapshot,
complete table hashes, and encrypted S3 objects under the dedicated backup
prefix. They include graph checkpoint tables and sales records. App rollback
keeps PostgreSQL selected; missing database credentials fail closed instead of
reactivating stale SQLite. The rollback helper can back up through an isolated
last-successful image if the current coordinator cannot start.

From the repository root, use the existing operator CLI with explicit inputs:

```sh
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 backup
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 storage-cutover
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 verify
```

See [database-handoff.md](database-handoff.md) for repository contracts and
[the Brain API instructions](../brain-api/README.md) for its separate durable
receipt ledger and recovery procedure. Never retry an uncertain Cognee document
by assigning a fresh ingestion ID: operator reconciliation must establish whether
the provider committed it.

## Live acceptance record

- Verified PostgreSQL login over TLS 1.3 using the scoped `toir_runs` role.
- Verified the existing database initially held zero runs/events/checkpoints.
- Verified private HTTPS `/api/health` returns 200 from a second tailnet device.
- Live cutover preserved **2 historical runs, 29 events, and 1 sales record**.
  Source/destination hash:
  `08d64b8de67ac0b84e93bb2e992d8b61f93badb60f38041a76d1488f6db174d8`.
- Final SQLite backup: `20261008T000839Z-1125e56e0854`.
  Pre-import PostgreSQL backup: `20261008T000823Z-e1666ac13cf0`.
  First active PostgreSQL backup: `20261008T000946Z-fade097cef19`.
- Staging application release: `5c69aec0ba2e-4c77c4071dd9` (five healthy pods).
  Google SSO integration release: `8c0fb9cadeaf-02703df864ec`, all five application
  Deployments healthy. Normal Google/Scalekit browser sign-in is verified.
- Successfully rolled back `eaa38337c2ec-49a408d52591` to the preceding staging
  application release, retaining PostgreSQL selection and historical records,
  then deployed the combined Google SSO release.
- Restored the **actual** first active PostgreSQL S3 backup into isolated local
  PostgreSQL 17 and matched all nine table counts/hashes. Spark was untouched;
  temporary containers and downloaded artifacts were removed. The S3 object
  uses AES256 encryption; archive SHA256
  `48e7ac6110c6814f802dee89d7c08768f5aaabbd6c1d8eb5f2958eda283bfed0`.
- Spark's running `/capabilities` now reports research ingestion and both sales
  members; Curran's pipeline read access is verified. This running version uses
  `data/research_ingestions.json` completed acknowledgments. It does not expose
  the newer per-document receipt/status endpoint; verify exact legacy receipts
  separately and retain the crash-window limitation until coordinated upgrade.
- The user's first new request, run `5893d113-b0b6-43b1-b46d-e23baecab22b`,
  retained four reviewed companies but failed when a follow-up exhausted the
  60-page budget. Its original failure and evidence remain unchanged. A follow-up
  budget handling fix and bounded acceptance requests are being verified.

Continuous automation, CRM writes, outbound messages, and private client dataset
access are outside these research acceptance runs. Explicit company-only
requests can bound the target count and disable contact enrichment.
