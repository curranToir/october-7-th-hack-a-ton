# Foundation architecture

This project follows Athena's organization and stack. It has no source imports,
shared credentials, business features, or runtime dependencies on Athena/Rhea.

```text
Team browser → SSM encrypted tunnel → server port 80 → Traefik
  /       → web:3000         (one pod)
  /api/*  → api:8000         (one pod)

Internal only:
  orchestrator:8000          (one pod)
  future agent-name:8000     (one separate pod per agent)
```

No task flow is implemented. The API owns `/api/health` and `/api/ready`;
the orchestrator owns `/health` and `/ready`. They return `status: ok` and a
service name. No database/provider is needed for readiness.

## Workload boundaries

- Three application Deployments live in `company-brain`; K3s system pods are
  additional and live in `kube-system`.
- Containers use UID/GID 1000, read-only root filesystems, and bounded writable
  `/tmp`. Service-account token mounting is disabled; no application RBAC is granted.
- Ingress is denied by default. Network policies permit Traefik → web/API,
  web → API, API → orchestrator, and orchestrator → future agent Services.
  No blanket egress policy is imposed yet.
- Deployments use one replica, `maxSurge: 0`, and `maxUnavailable: 1`. Updates
  may interrupt service. Separate pods share one machine; this is not high
  availability or a sandbox for untrusted code.
- Web requests/limits: 128/384 MiB. API/orchestrator: 128/256 MiB each. CPU:
  100m request and 500m limit per application. Kubelet reserves 2 GiB memory
  and 500m CPU for host/cluster services, with a 200 MiB eviction buffer.
- T3 uses Standard credits to avoid surplus credit charges. Sustained CPU use
  above baseline can throttle. Builds happen off the server.

## Adding an agent later

Leave `agents/`, `products/`, and `compositions/` empty except for their READMEs
until the team defines capabilities. A new agent gets its own source directory
and Dockerfile. Render `tooling/templates/agent.yaml` with a DNS-safe name and
immutable image reference. Its process listens on `0.0.0.0:8000` and implements
`/health` and `/ready`.

Extend the release image list/builds and foundation manifests when adding a
real agent. Import its image before applying because pull policy is `Never`.
The template runs continuously and starts with 128/256 MiB requests/limits;
adjust this based on actual workload measurements. It is not applied by normal
deployments. The optional live template check temporarily uses the orchestrator
image, proves it runs in its own pod, then removes it.

Task contracts, routing, providers, persistence, retries, and inter-agent
communication will be designed once the first agent capabilities are specified.
