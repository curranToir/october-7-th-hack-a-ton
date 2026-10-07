# EC2 is on Tailscale — database engineer handoff

Historical verification on October 7, 2026 (America/Los_Angeles). This records
the network checks at handoff time, not a new deployment health check. See
[prospecting implementation](prospecting-implementation.md) for subsequent
repository work and release dependencies. The EC2 node has joined the
`neptuneops.com` tailnet and the coordinator pod can reach the confirmed Spark
host. After the Spark owner rebound PostgreSQL, both the host and pod passed
PostgreSQL connectivity and certificate verification over Tailscale.

## Connection details

| Item | Value |
|---|---|
| AWS account / region | `904469541651` / `us-east-1` |
| CloudFormation stack | `company-brain-hackathon` |
| EC2 instance | `i-0d5e3f4859de3ea93` |
| EC2 Tailscale name | `toir-hackathon.taild4c940.ts.net` |
| EC2 Tailscale IPv4 | **`100.86.7.62`** |
| EC2 Tailscale IPv6 | `fd7a:115c:a1e0::7337:73f` |
| Tailscale version installed | `1.104.1` |
| Confirmed database host | `waffle-spark.taild4c940.ts.net` |
| Spark Tailscale IPv4 | **`100.87.113.122`** |
| PostgreSQL destination | `waffle-spark.taild4c940.ts.net:5432` |
| Kubernetes namespace | `company-brain` |
| Coordinator Deployment | `orchestrator` |

The connection was authorized using `curran@neptuneops.com`. `tailscaled` is
active and enabled at boot; node identity persists in its root-owned state on
the encrypted EC2 disk. Reprovisioning a replacement instance requires a new
Tailscale enrollment. No enrollment key was written into Git, CloudFormation,
release bundles or application Secrets.

## Verified network path

Coordinator pod → existing K3s/Flannel outbound NAT → EC2 `tailscale0` → Spark.
The database should see **`100.86.7.62`** as the client source, not the pod CIDR.
No pod/subnet route is advertised to the tailnet and no additional NAT rule was
needed. The EC2 security group still has no public inbound rules.

Tailscale is configured with shields-up for outbound client access. EC2
initiates database/API connections; unsolicited tailnet connections into EC2
are blocked. Application and administrative ingress continue through SSM.
Tailscale SSH, exit-node use, route acceptance and route advertisement are off.

Host DNS remains unchanged. Kubernetes has a conditional CoreDNS zone for
`taild4c940.ts.net`, forwarding only that suffix to `100.100.100.100`. The
configuration is in `kube-system/coredns-custom`, key `tailnet.server`; the
standard cluster/public DNS configuration is preserved.

| Check | Result |
|---|---|
| EC2 tailnet membership | Running in `neptuneops.com`, correct node IP/name |
| EC2 route to Spark | `tailscale0`, source `100.86.7.62` |
| Tailscale ping to Spark | 3/3 succeeded, direct path |
| Coordinator pod MagicDNS | Spark FQDN resolves to `100.87.113.122` |
| Host and coordinator pod → Spark TCP 22 | Connected; no SSH authentication attempted |
| Host and coordinator pod → Spark TCP 5432 | **Connected** after the Spark rebind |
| PostgreSQL SSLRequest and TLS handshake | **Passed** on host and pod: TLS 1.3, system CA trust, exact Spark FQDN verification |
| Host and coordinator pod → Spark TCP 8200 | **Connected** after the brain-api startup |
| Host and coordinator pod → brain-api `/health` | **HTTP 200** without credentials |

The successful pod TCP connection proves the outbound/return path through the
host. The earlier PostgreSQL refusal was superseded by the successful recheck
after the Spark owner's rebind. The TLS certificate's SAN matches
`waffle-spark.taild4c940.ts.net` and expires January 5, 2027 at 22:15:15 UTC.
SSM verification command: `bec37c5f-0226-43b2-b4e0-cbb07fc710e7`.

Host system DNS remains unchanged; the host check connected to the Tailscale IP
and used the FQDN for SNI/certificate verification. Normal pod DNS resolves the
FQDN. The later brain-api recheck passed TCP 8200 and unauthenticated `/health`
on host and pod, superseding the earlier refusal. SSM command:
`04aeb49a-eb6a-4d20-ae34-7a1bb2ed025e`. Tailscale remained Running with the same
identity after the application rollout. The Spark configuration was not changed here.

## Next steps for the database engineer

1. PostgreSQL is reachable on `100.87.113.122:5432`. The EC2 source is
   `100.86.7.62/32` if narrowing the Spark access rule.
2. Use the previously agreed database and restricted role, both `toir_runs`,
   with `hostssl`/SCRAM and verified TLS. Use
   `waffle-spark.taild4c940.ts.net` as the certificate hostname; pod DNS is ready.
3. Supply the connection configuration through the project AWS Secrets Manager
   handoff. Do not paste credentials into `/coms`. The coordinator still uses
   SQLite until a deliberate storage migration/cutover. The Postgres adapter
   has landed on `main`; its presence does not migrate existing SQLite history
   or enable `DATABASE_URL` in the deployed coordinator.
4. `brain-api` TCP 8200 and `/health` now pass over the tailnet. Application
   integration still needs the separately configured bearer credential;
   authenticated brain-api operations were not exercised by this network check.
5. Repeat the network check below, then validate database login and TLS using
   the actual runtime credentials. Certificate verification passed; database
   authentication and a SQL query have **not** been tested from EC2 yet.

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py verify \
  --peer 100.87.113.122 --port 5432
```

See [Tailscale operations](../docs/tailscale.md) for installation, membership,
conditional DNS and repeatable checks, and [database handoff](database-handoff.md)
for the repository/checkpointer integration contract.

## Exa research update

The Toir Exa connected account is **ACTIVE** in Scalekit. A real search through
Scalekit returned two sources with URLs and text, and the server runtime secrets
were synchronized with controlled coordinator/research restarts. Exa's direct
key stays in its setup secret and Scalekit's connected account; the research
pod receives Scalekit credentials.

The initial full run preserved 44 sources after a report-format failure. Its
explicit retry, `51be4938-7297-476b-b2d4-895f418f30e3`, completed in 96 seconds:
two companies, 74 sources and four competitor facts. All 19 citations matched
retrieved evidence; employee counts, source IDs, dates and unique company domains
passed checks. The two results are AI vendors with possible delivery-capacity
opportunities; this is a narrow prospecting result, not verified buying intent.
The report explicitly records missing funding, partnership, ad and pricing coverage.

Coordinator and worker spans share trace `5902a7809174a2f5b7bf2967156717ca`.
The run used six searches and five total model calls. Peak observed research
memory was 326 MiB, coordinator 130 MiB, with about 1,978 MiB host memory available
at the lowest sample and no pod restarts.

Release `b50d6a099099-c919469a363d` fixes the report format and derives final
rationale/coverage notes from accepted findings, avoiding stale candidate prose.
The saved acceptance report's presentation was regenerated without new provider
calls or changes to its sources, findings or citations; an event records that
repair. Its prior state is retained in backup
`20261007T233543Z-a2384338a956`. The original failed run and execution checkpoints
remain intact. Local verification: 154 Python tests, 21 Bun tests, TypeScript and
Ruff passed; 13 Postgres contract tests were skipped without a dedicated test DB.
