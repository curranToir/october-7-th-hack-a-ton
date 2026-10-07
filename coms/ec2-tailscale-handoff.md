# EC2 is on Tailscale — database engineer handoff

Verified October 7, 2026 (America/Los_Angeles). The EC2 node has joined the
`neptuneops.com` tailnet and the coordinator pod can reach the confirmed Spark
host. PostgreSQL is not accepting TCP connections on its Tailscale address yet.

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
| Host and coordinator pod → Spark TCP 5432 | **Connection refused**, errno 111 |
| Host and coordinator pod → Spark TCP 8200 | **Connection refused**, errno 111 |

The successful pod TCP connection proves the outbound/return path through the
host. The refusals are not a successful database or brain-api test: those
services are not accepting connections on the tested Spark address/ports. A
missing listener, loopback-only/container binding, or an explicit firewall
reject can cause this; the Spark configuration has not been changed or inspected.

## Next steps for the database engineer

1. Start/bind PostgreSQL on the Spark Tailscale interface at
   `100.87.113.122:5432`; check container port publication or host firewall rules
   if the service is already running. Allow the EC2 source `100.86.7.62/32`.
2. Use the previously agreed database and restricted role, both `toir_runs`,
   with `hostssl`/SCRAM and verified TLS. Use
   `waffle-spark.taild4c940.ts.net` as the certificate hostname; pod DNS is ready.
3. Supply the connection configuration through the project AWS Secrets Manager
   handoff. Do not paste credentials into `/coms`. The coordinator still uses
   SQLite until the Postgres adapter/checkpointer replacement is installed.
4. Publish `brain-api` on port `8200` if that remains the agreed API port, then
   confirm its `/health` endpoint over the tailnet.
5. Repeat the network check below, then validate database login and TLS using
   the actual runtime credentials. Database authentication, certificate
   verification and a SQL query have **not** been tested yet.

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
pod receives Scalekit credentials. A full research test retrieved 44 sources
but exposed a report-validation issue in a follow-up pass; that application fix
is being validated separately and is unrelated to Tailscale connectivity.
