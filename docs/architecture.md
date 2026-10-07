# Toir research architecture

The initial customer is Toir's internal sales team. The application follows
Athena's folder conventions without importing its credentials or application
logic. Rhea's evidence-first research pattern informs the new research harness;
Rhea queues, services and deployment are independent.

```text
Team browser → SSM tunnel → server port 80 → Traefik
  /       → web:3000 → /api/* → api:8000
                                ↓
                      orchestrator:8000
                        LangGraph/Python
                                ↓
                         research:8000
                          Oh My Pi/Bun
                                ↓
                         Scalekit → Exa

Coordinator + research → Respan gateway and tracing
Coordinator → persistent local SQLite volume → encrypted S3 backups
```

The API proxies research requests; the custom coordinator owns admission,
planning, dispatch, evidence review, follow-up and reports. The research agent
owns its temporary Oh My Pi session and permitted search/retrieval tools. It has
no shell tools or nested agent spawning. Each future agent gets its own image,
one-replica Deployment and internal Service.

## Workload and access boundaries

Four application pods run in the `company-brain` namespace. Kubernetes system
pods run separately in `kube-system`. All application pods run as UID/GID 1000,
use read-only root filesystems and have bounded writable `/tmp`. Service-account
tokens are disabled, with no application RBAC. Only the coordinator mounts the
2 GiB `coordinator-data` claim at `/data`.

Ingress is denied by default. Allowed paths are Traefik → web/API, web → API,
API → coordinator, and coordinator → research. Neither coordinator nor research
has a public ingress route. Outbound HTTPS is available for provider calls;
IMDSv2 hop limit 1 prevents normal pod access to the instance metadata role.
No blanket egress network policy is imposed.

The same dedicated `t3.medium` supplies 2 vCPU and 4 GiB RAM. The application
memory requests/limits are web 128/384 MiB, API 128/256 MiB, coordinator
256/384 MiB and research 256/512 MiB. Each requests 100m CPU with a 500m limit.
Kubelet reserves 2 GiB and 500m CPU for the host/cluster, with a 200 MiB memory
eviction buffer. One research run is active at a time. External model calls do
not require a local GPU; the independent Spark RAG service is not deployed here.

Every Deployment uses one replica, zero surge and one unavailable pod during
updates. Separate pods share one machine; updates can interrupt service and
this deployment is not highly available. T3 Standard avoids surplus CPU-credit
charges but sustained work can throttle after credits are exhausted.

## State and credentials

The coordinator exclusively owns `runs.sqlite` and `checkpoints.sqlite` on its
local-path persistent volume. Pod replacement and server reboot preserve these
files. Instance replacement or root-disk loss requires restoring an S3 backup.
The run repository and LangGraph checkpointer have separate replacement seams;
see [the database handoff](../coms/database-handoff.md).

AWS Secrets Manager is the source of runtime credentials. A host-side SSM runner
reads only the exact Respan and Scalekit secret ARNs and synchronizes separate
Kubernetes Secrets. The coordinator receives Respan; the research agent receives
Respan and Scalekit. Exa's setup key is stored in AWS and in Scalekit's connected
account vault, but is not granted to the EC2 runtime role or application pods.
K3s encrypts Kubernetes secret data at rest on the encrypted root disk.

Secret values never enter release archives or SSM command parameters. Rotation
requires secret synchronization and pod restart because environment variables
are loaded at startup. Provider setup state is distinct from process health:
missing credentials keep the application inspectable while research capability
reports the missing configuration.

The coordinator's internal maintenance endpoint blocks new work and lets an
active run finish. Deployment and secret rotation use it before replacing pods.
The persisted admission flag remains set after a failed deployment; operators
repair or roll back, then explicitly resume when necessary. Consistent snapshots
use SQLite's online backup API after admission is blocked and work is drained.
