# Existing EC2 host → Spark over Tailscale

The connection is outbound from the coordinator pod to a confirmed Spark service.
The existing server stays in its VPC with no public inbound security-group rules.
SSM remains the application/admin access path. The target tailnet is
`neptuneops.com`, with MagicDNS suffix `taild4c940.ts.net`; confirm the specific
Spark device and service port before checking connectivity.

## Verified connection state — 2026-10-07

The existing EC2 host joined as `toir-hackathon` at `100.86.7.62`. The confirmed
database target is `waffle-spark.taild4c940.ts.net` (`100.87.113.122`), TCP 5432.
The coordinator pod can query MagicDNS directly, and the suffix-only CoreDNS
configuration resolves that FQDN to the confirmed address through normal pod DNS.

Bounded checks from both the EC2 host and coordinator pod reached Spark TCP 22.
Tailscale ping succeeded over a direct connection. After the Spark owner's
PostgreSQL rebind, host and pod both connected to TCP 5432 and completed a
PostgreSQL SSLRequest followed by TLS 1.3 with system CA trust and exact FQDN
verification. After brain-api started, host and pod both connected to port 8200
and received HTTP 200 from unauthenticated `/health`, replacing earlier refusals.
No database login, SSH login or Spark mutation was attempted, and no extra
NAT/firewall rule was needed. See the [verified handoff](../coms/ec2-tailscale-handoff.md)
for certificate details and the distinction between connectivity and DB cutover.

The verification helper reports host and pod outcomes independently, including
safe error class, errno and elapsed time, even when the first connection fails.

## Install and approve the device

The operator helper targets the existing CloudFormation stack's instance through
SSM. It does not replace EC2, change user data, modify Kubernetes manifests, add a
Tailscale operator, or expose pod/subnet routes.

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py install
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py login
```

Install uses Tailscale's official Ubuntu Noble APT repository and pins amd64
version `1.104.1`. The helper verifies the repository key hash and pinned package
SHA256 against signed APT metadata, then enables `tailscaled` at boot and holds
the package version. Updates require reviewing the constants and deliberately
unholding/updating the package. [Official Linux installation](https://tailscale.com/docs/install/linux),
[official package repository](https://pkgs.tailscale.com/stable/)

Login prints `LOGIN_REQUIRED` followed by a Tailscale login URL. Open it and
approve `toir-hackathon` in `neptuneops.com`. A pending URL does not mean the
machine has joined. If the tailnet requires device approval, an administrator
must approve the new device before it connects. Then verify membership:

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py status
```

The expected result is `state: Running`, a Tailscale address, and the correct
tailnet. The helper refuses to treat a device already in a different tailnet as
success. It does not force reauthentication or switch an existing device's owner.

Join explicitly uses `shields-up=true` for outbound-only connectivity and leaves
Tailscale SSH disabled. DNS acceptance, subnet-route acceptance, route
advertisement, exit-node use and exit-node advertisement are all disabled.
The normal daemon state remains in its root-owned `/var/lib/tailscale` directory
and survives reboot; never copy its private state into Git or an application
image. [Tailscale CLI settings](https://tailscale.com/docs/reference/tailscale-cli/up)

## Optional unattended auth-key flow

Browser approval is the initial path. For future rebuilds, a tailnet administrator
can issue a one-use, non-ephemeral server auth key with an approved device tag and
restricted grants. Store it as `{"TS_AUTH_KEY":"…"}` in a dedicated project AWS
Secrets Manager entry using a hidden local prompt or SDK input. Do not paste its
value into shell arguments, CloudFormation user data or SSM commands.

Grant the host role `secretsmanager:GetSecretValue` for that exact secret ARN
only. This helper does not create or broaden IAM policy. Run:

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py login --secret-id SECRET_ARN
```

Only the ARN travels through SSM. The host reads the key into memory and passes
it to Tailscale using `--auth-key=file:/dev/stdin`; no credential appears in
process arguments, a temporary file or command output. Authentication failures
suppress provider output. After enrollment, the daemon's device identity persists
without repeatedly consuming an auth key. Review device key expiry in the tailnet;
SSM remains available for recovery. [Auth keys](https://tailscale.com/docs/features/access-control/auth-keys),
[file-based key input](https://tailscale.com/docs/reference/tailscale-cli/up)

## Verify host and pod egress

Use the Spark device's confirmed Tailscale IPv4 address and actual service port.
The values below are placeholders, not a guessed endpoint:

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py verify --peer SPARK_TAILSCALE_IP --port SPARK_PORT
```

The check requires the correct connected tailnet, an enabled/running daemon,
IPv4 forwarding, and a kernel route through `tailscale0`. It then opens a TCP
connection first from the host and then from the running coordinator pod.
`POD_TAILNET_TCP_VERIFIED` means both TCP connects succeeded. This does not verify
Postgres login, TLS policy, database schema or application authorization; the
database owner must supply those settings separately.

K3s's existing Flannel network remains in place. For an external destination,
Flannel's normal masquerade rules should rewrite pod traffic to the host's
outgoing Tailscale address. This is the expected behavior to verify on the live
node, not an assumption that a successful host ping proves pod connectivity.
[Tailscale route injection](https://tailscale.com/docs/reference/route-injection),
[Flannel masquerade implementation](https://github.com/flannel-io/flannel/blob/master/pkg/trafficmngr/iptables/iptables.go)

Tailscale grants should allow the EC2 device identity to only the Spark service
ports needed. The Spark service must listen on its tailnet address or an
appropriate interface; a service bound only to `127.0.0.1` is not reachable via
the Spark tailnet IP. That binding and the database's client authentication
rules are owned by the Spark/database operator.

The default host DNS configuration is preserved to avoid disrupting Kubernetes
DNS. Use the confirmed Tailscale IP for the initial check. If stable MagicDNS
names are needed later, configure a conditional CoreDNS forward for this tailnet
suffix to Tailscale's resolver and verify it from the coordinator pod. Do not
replace all cluster DNS forwarding or enable unrelated accepted subnet routes.
[Tailscale DNS behavior](https://tailscale.com/docs/reference/faq/dns-resolv-conf)

## Enable the confirmed Spark hostname for verified TLS

After joining and confirming the Spark FQDN, install a suffix-only CoreDNS forward:

```sh
.venv/bin/python infrastructure/deployment/scripts/tailscale_host.py dns --peer-name SPARK_FQDN
```

The helper first confirms the connected tailnet and its exact MagicDNS suffix,
checks the installed CoreDNS custom-config mount, and makes a direct DNS query
from the coordinator pod to `100.100.100.100`. If that query fails, cluster DNS
is unchanged. On success, it applies only this key in `kube-system/coredns-custom`
using a dedicated server-side field manager, preserving unrelated custom keys:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns-custom
  namespace: kube-system
data:
  tailnet.server: |
    taild4c940.ts.net:53 {
      errors
      cache 30
      forward . 100.100.100.100
    }
```

The operator command waits for the projected file and CoreDNS reload, then verifies
normal hostname resolution from the coordinator pod. It does not restart CoreDNS,
change the main Corefile or alter default/public/cluster.local DNS. The confirmed
FQDN can then be used with the database owner's CA and hostname verification;
resolving the name is not itself a Postgres TLS handshake test. CoreDNS's optional
`coredns-custom` mount and `*.server` import were verified on this running K3s
installation. [K3s custom DNS imports](https://docs.k3s.io/advanced#coredns-custom-configuration-imports)

To remove this optional DNS customization, remove only `data["tailnet.server"]`
from `coredns-custom`, preserving other custom zones. Do not delete the shared
ConfigMap if another operator has added unrelated settings.

## If host connectivity works but pod connectivity fails

Inspect these read-only details through SSM before changing any routing:

- `sysctl net.ipv4.ip_forward` must report `1`; K3s already needs forwarding.
- Compare `ip route get SPARK_TAILSCALE_IP` with the node's pod CIDR and routes.
- Inspect `iptables -t nat -L FLANNEL-POSTRTG -n -v` counters while making the pod
  TCP request, plus existing Tailscale forwarding/NAT rules.
- Confirm Spark receives the node's Tailscale source address and replies to it.
- Check the tailnet grant, Spark bind address/firewall, and relevant Kubernetes
  NetworkPolicies. Existing application policies restrict ingress, not egress.

Do not flush or replace Kubernetes/Tailscale firewall chains, turn off netfilter,
advertise the pod CIDR, accept all subnet routes, or add a blanket NAT rule to
work around a failed check. If evidence proves a missing masquerade rule, scope
the repair to the actual pod CIDR, the confirmed Spark destination `/32`, and
outgoing `tailscale0`; persist that single reviewed rule with an idempotent
systemd unit after both K3s and Tailscale. No such rule is installed by this helper.
[Tailscale netfilter behavior](https://tailscale.com/docs/reference/netfilter-modes)

No new public inbound port is necessary. Tailscale can relay encrypted traffic
when a direct path is unavailable, so a relay result is not a failed connection.
After a controlled reboot, repeat status, pod TCP verification and the existing
application `manage.py verify` checks. [Tailscale connection types](https://tailscale.com/docs/reference/connection-types)
