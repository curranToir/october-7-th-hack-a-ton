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

The integration rollout enabled no public application access. A separately
merged meeting-agent rollout subsequently added a signed Recall-only Funnel
listener on port 8443; see [meeting activation](../docs/meeting-agent.md). Private
application HTTPS 443 and EC2's security group with no inbound rules remain
unchanged. SSM remains the administrative path. The Scalekit environment
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

## Requested model and prospecting update

Application release `b9730136262d-7eae5ee824ee` uses `gpt-5-mini` through Respan for planning,
review, chat routing, research and contact research. A live EC2 gateway probe
returned `gpt-5-mini-2025-08-07` successfully. Worker and coordinator limits are
now 60 searches, 200 retrieved-page slots and 60 model turns per run, still
cumulative across follow-up passes and bounded by the ten-minute deadline.
Reviewed findings survive a later usage-budget failure with an explicit report
gap. Historical failed runs are not relabeled.

HubSpot identity and permission verification on October 8 UTC:

- Scalekit account `ca_146541202500485386`, connection
  `conn_146541157638209802`, named `hubspot` / `curran@toirinc.com`, is ACTIVE.
- The authenticated read-only `/integrations/v1/me` proxy returned ToirInc
  portal `247630342`. Real company/contact search calls returned 34 companies
  and 43 contacts; no customer record contents were copied into this handoff.
- HubSpot's [connected-app grant screen](https://app-na2.hubspot.com/connected-apps/247630342/installed/basic/40203579/overview)
  for “Scalekit Test Account” explicitly grants company and contact View and
  Create/delete/change permissions. These match the connection's mandatory
  company/contact read/write scopes; optional scopes are empty. All eleven
  required connector tools are available.
- This evidence supports `SCALEKIT_HUBSPOT_WRITE_SCOPES_VERIFIED=true` in the
  project deployment manifest. It is permission attestation, not evidence of
  a successful CRM write. No CRM records were changed during verification.

Continuous prospecting was enabled through the authenticated application after
the smaller-model rollout. Persisted settings permit ten discovery batches and
25 company enrichments daily, with a minimum fit score of 70. The first real
background job is `5f9132dc-7159-4446-b74b-b2f60badaeaa`, research run
`cf6794b7-8f18-402e-b7b7-521532aadc31`, in
[the continuous prospecting session](https://toir-hackathon.taild4c940.ts.net/#chat/284510b7-eef4-5474-8520-a0d0628e9642).
Its trace ID is `c4d734e5536379a48d40456c1f1c7250`. Target: ten companies, with
contact enrichment and proposed CRM updates. CRM mutations still require
explicit proposal approval; none were executed during connection verification.
Outbound messages and private client dataset access remain outside this rollout.

The first two automatic runs failed on malformed model reports; they remain
failed in history. The third, `86d400a7-0c6d-4a47-8f46-8d18033a0195`, completed
with one accepted company (Jazwares), ten retrieved sources and six valid
citations. Its contact-enrichment child is `55a1653f-e8cc-5587-b103-09cb508a738a`.
Admissions were then drained without cancelling work to deploy JSON-mode
output enforcement and per-candidate schema isolation. These changes preserve
strict citation checks, discard malformed candidates without fabricating values,
and reject truncated or ambiguous final JSON. Combined worker validation passed
65 tests and both TypeScript checks; the backend suite passed 385 tests with 46
environment-dependent skips. Prospecting remains persisted as enabled during
maintenance. Follow-up release `a32aea998053-7e85693a34ed` passed with all five
pods Ready and zero restarts before the separate six-pod meeting rollout. Its pre-deployment PostgreSQL backup is
`20261008T010203Z-fd96eabeabc8`; maintenance is off and both the queued contact
task and next discovery resumed. The exact discovery outbox
`6ad532321b18028ee91b9963f09f66333fd327d2234c6107cc6c67015e47ee32`
is now synced on its first attempt. A read-only check on Spark found the same
completed receipt in the legacy JSON ledger: `toir-pipeline`, one document.
The PostgreSQL outbox maps that receipt to the real research run; the legacy
receipt itself does not store a run ID. This verifies actual memory delivery
without changing Spark configuration or restarting its services.

The first contact-enrichment attempt failed the identity gate: selected legal
name `Jazwares, LLC` did not equal the fresh official display name `Jazwares`.
The follow-up patch permits only standard trailing legal-suffix differences on
the same verified domain, then uses the literally cited official display name.
It preserves fit metadata and rejects different brands/domains. The failed task
and its evidence remain in history; one explicit retry will verify the patch.

The fourth automatic discovery completed the live company-to-contact-to-CRM
proposal flow under JSON mode: run `b6541022-c9f3-47a0-bb1e-5f3690c7a930`
returned PayNearMe with 15 retrieved sources and eight valid citations.
Independent review found its headcount evidence insufficient to establish the
20–1,000 employee ICP: the cited historical company blog says “100+”, which is
a lower bound, not a current exact count or an upper bound. Child
`97449f31-e74f-53eb-8e21-37f87ba135dc` completed with four verified contacts,
19 sources and six valid citations. Proposal
`79f4e705-b4c7-4c2a-87ef-ee5c4f482fad` originally contained 23 proposed
operations. It was corrected through the authenticated UI to version 3: the
unsupported company headcount and description fields, company note and its
dependent association are excluded. All four contact changes remain pending.
Read-only verification confirmed `pending` / `not_started`, with no approval
or execution for this proposal. Historical research still retains the original
headcount and its overstated rationale; exclusion prevents that claim from
being included in this pending CRM write, rather than rewriting history.
These are actual agent results, separate from the ten concurrently added
demo proposals. The legal-name follow-up passed 76 worker tests, both TypeScript
checks and eight downstream Python evidence checks.

Evidence precision note: the Jazwares source states 700–800 employees. The
stored value 750 is an estimate within that range, not a verified exact count.
The historical generated rationale overstates that precision; future report
rationales now explicitly describe the count as an estimate. The source
supports existing AI initiatives, not proven unmet demand or purchase intent.
There is no independent headcount corroboration in this run. Ten concurrently
added proposals are preserved separately and are not counted as results
of this observed automatic research run.

Brain code on `main` also selects GPT-5 mini for its text LLM stages, synthesis
and judging; its embedding model remains `nemotron-embed`. The live Spark
model switch is explicitly deferred: the user instructed **keep Spark unchanged
for now**. Its existing Cognee runtime remains on its loaded configuration;
no Spark files, services or embeddings were changed. Do not confuse the committed
model policy with the model already loaded by the running Spark process.

The release passed all five Deployment readiness checks, API/coordinator and
ingress probes, with zero pod restarts. Its pre-deployment PostgreSQL backup is
`20261008T004829Z-6afb55a7e8be`.


## Combined six-pod release and exact delivery evidence

The other engineer deployed `2b0d15355c55-fa3fc4ddf60d` and merged its meetings
feature into `main` (`cb33208`). The next combined build preserves all six pods
and the meeting provider configuration while applying the legal-name fix and
GPT-5 mini to meeting analysis and subject research as well. The built release
is `8133677f1ab4-adb0185c8185`, SHA256
`adb0185c81855b9fc775b15ec879d54a441f15e6a81d0cae2b20900277d70e65`.
Deployment is pending verification at this record's publication; the build alone
does not establish activation. The deployment command uses
`--expected-release 2b0d15355c55-fa3fc4ddf60d`; the exact check runs under the
host deployment lock before configuration, draining, backup or image imports.
Any intervening release aborts this rollout without application changes.

Combined backend validation passed 505 tests (46 environment-dependent skips).
Research/contact/subject validation passed 83 tests, followed by 11 focused
Mini provider/final-JSON tests after the subject change. The deployment guard
and runtime/release suites passed 54 tests. Meeting analysis uses the Respan
gateway but its new feature has no cross-service root-span instrumentation;
do not claim correlated meeting traces from the existing research trace proof.

PayNearMe's persisted discovery and contact report each have a synced outbox,
with the exact completed IDs independently found in Spark's existing legacy
receipt ledger (`toir-pipeline`, one document each):

- Discovery: `302a4a62252be2eec67ad4e70dd0bef9ec18cbe31495129b3b0ae66a2d74ccac`.
- Contacts/proposal: `d8714f5c32e5d80198c8e054acd8387daf1baa8a8ac5c51b7d58ab84feb77a5f`.

Its discovery trace `d627b7a272370297a0d518001c6c9757` recorded eight Mini
model calls across coordinator and research. Contact trace
`14a452b2b7be3bcfbcbf7f448dcffad4` recorded three Mini calls and a complete
final response. Public source quotations and source IDs passed the deterministic
citation audit; that check alone does not establish company qualification.
A fifth discovery (`560415a4-dcd3-4bf9-a79b-b81a6ee948df`) returned zero accepted
companies from 30 sources. Empty results are preserved as actual outcomes.

The user reiterated that Spark must remain unchanged. None of this EC2 work
alters its files, service configuration, loaded model or embeddings.
