# Spark PostgreSQL deployment and recovery

The database and Brain API credentials remain in the existing project Secrets Manager
secrets. CloudFormation grants the host exact-ARN reads. Only the coordinator's
Kubernetes Secret receives either credential; agent pods never receive database
credentials. The database URL must use `sslmode=verify-full` and the image's system CA
bundle. Native PostgreSQL clients receive parsed connection settings through their
process environment, never command arguments, SSM parameters, logs or releases.

Installing IAM access does **not** activate PostgreSQL. `/var/lib/company-brain/storage.json`
selects the durable backend; before a verified cutover it defaults to SQLite. An absent
PostgreSQL secret after selection fails deployment/rotation before altering Kubernetes
Secrets. Brain API synchronization is independent of this storage selection.

## Deployment and cutover

Use the normal explicit account/region inputs for each command:

```sh
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 stack-update
python3 infrastructure/deployment/scripts/manage.py build
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 deploy
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 storage-cutover
```

Deploy the image containing PostgreSQL client **17.11-1.pgdg12+2** before cutover. It comes
from PostgreSQL's signed Bookworm APT repository and matches Spark's PostgreSQL 17 major.
The `storage-cutover` operation:

1. Enables persistent maintenance and drains research, contacts and CRM work.
2. Takes an encrypted S3 whole-database backup of the destination using the staged secret.
3. Stops the coordinator completely, including authentication/chat writes that maintenance
   alone does not block. Starts a temporary same-image administrative pod with no service
   account token or application server, mounting the SQLite PVC read-only.
4. Takes the final consistent SQLite backup and imports terminal research runs, ordered
   events and **all sales records** (including auth, automation, proposals and outbox).
   Source and destination must pass validation and identical canonical record hashes.
   Existing different records or occupied graph checkpoints reject the import. An exact
   previous import is safe to retry.
5. Records the verified import and both backup IDs before selecting PostgreSQL. Syncs
   credentials, restarts the coordinator, verifies the selected backend and creates its
   first whole-database backup. Only then clears maintenance.

SQLite LangGraph checkpoint blobs are retained in the encrypted SQLite backup and PVC;
new PostgreSQL threads use fresh checkpointers. Historical runs and reports remain
available. The v2 migration bundle includes sales records; legacy v1 exports are still
readable but cannot overwrite sales data. Exports are mode 0600, temporary and never printed.

Before selection, a failed import preserves SQLite selection and restores its replica in
maintenance. After the receipt selects PostgreSQL, failure retains that selection; rerun
`storage-cutover` to complete activation. Never remove DATABASE_URL or reset selection
as a rollback shortcut: that would hide PostgreSQL writes behind stale SQLite files.

## Backup and recovery

```sh
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 backup
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 restore --backup-id YYYYMMDDTHHMMSSZ-012345abcdef
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 rollback
```

`backup` inspects the live backend. PostgreSQL backups include every user table and graph
checkpoint using a custom-format `pg_dump`, a shared repeatable-read exported snapshot,
row counts/hashes, server/client version, archive checksums and source release. Dumps are
uploaded with S3 AES256 encryption under `backups/`; the existing bucket lifecycle expires
them after 30 days. Credentials and source row values never appear in operator output.

`restore` is an explicit replacement operation. It validates the archive, drains and
stops the application, takes a fresh backup of the final stopped-writer state, then runs
`pg_restore --single-transaction --clean --if-exists` in the temporary administrative pod.
It verifies all table hashes before restarting. New target tables absent from the backup
require a schema-aware operator procedure and are rejected before replacement. A failure
leaves the writer stopped and the safeguard dump retained. The scoped Spark role needs no
superuser, role creation or database creation permission; it restores its own objects.

Application rollback preserves the selected database. An unhealthy coordinator is backed
up using an isolated pod from the last successful, checksum-verified image before rollback.
A backup failure blocks rollback rather than accepting stale SQLite. Releases predating the
PostgreSQL backup capability marker are rejected after cutover. To return to SQLite after
new PostgreSQL writes would require a separately reviewed reverse migration, not app rollback.

The normal application has a disk-backed `/tmp` limit of 512Mi for the backup utility. If a
whole-database archive outgrows this limit, increase measured storage capacity before the
next backup; never bypass the backup gate. This remains a single coordinator replica.

## Isolated restore validation

The Spark role has no CREATE DATABASE privilege. Never use the shared application or test
database for destructive validation. `tooling/tests/test_postgres_deployment.py` includes an
opt-in local Docker PostgreSQL 17 round trip that checks binary checkpoints, complete table
hashes, replacement restore and rejection of a newer target schema. Set
`TOIR_PG17_TEST_CONTAINER` to an explicitly isolated local PostgreSQL 17 container and
`TOIR_PG17_TEST_IMAGE` to the built coordinator image. Normal unit tests do not contact Spark.

References: [official Debian packages](https://www.postgresql.org/download/linux/debian/),
[pg_dump 17](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore 17](https://www.postgresql.org/docs/17/app-pgrestore.html).
