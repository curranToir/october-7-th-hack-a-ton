# Company Brain

A hackathon foundation using Athena's application layout: Next.js, React,
TypeScript, and FastAPI on one dedicated AWS `t3.medium` running K3s.

The web shell says **No agents configured**. The API and orchestration host
provide health probes only. The deployed foundation has no task execution,
database, AI provider, login, GitHub Actions workflow, or predefined agent.

The separate [RAG service](rag-db-api/README.md) has its own setup instructions
and is not included in the foundation's Kubernetes deployment.

## Layout

```text
apps/
  web/                 Next.js application shell
  api/                 Browser-facing FastAPI host
  orchestrator/        Internal orchestration placeholder
agents/                Reserved for agent implementations
products/              Reserved for product boundaries
compositions/          Reserved for workflows
rag-db-api/            Separate RAG service with its own setup
infrastructure/
  docker/              Three production Dockerfiles
  deployment/
    cloudformation/    Dedicated AWS resources and bootstrap
    kubernetes/        Deployments, Services, ingress, network policies
    scripts/           Provision, build, deploy, access, verify, rollback
tooling/
  templates/           Unapplied agent Deployment/Service template
  tests/               Health and deployment tests
docs/                  Architecture and operations
```

Each future agent gets **its own always-running pod**, image, Deployment, and
internal Service. Agent types remain undefined.

## Deploy and open

Prerequisites: AWS CLI with appropriate account access, Python 3.12+, Docker
with Buildx, and the AWS Session Manager plugin. Builds target `linux/amd64`
even on Apple Silicon. The server does not need Docker or Node tooling.

```sh
# Creates chargeable AWS resources in the explicitly selected account/region.
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 provision
python3 infrastructure/deployment/scripts/manage.py build
python3 infrastructure/deployment/scripts/manage.py deploy
python3 infrastructure/deployment/scripts/manage.py tunnel
```

Keep the tunnel running and open <http://localhost:8080>. There are no open
inbound ports on the server. Access and deployment use SSM, not SSH.

Global `--account`, `--region`, and `--stack` flags precede the command. The
equivalent environment variables appear in `.env.example`; scripts do not
load dotenv files or copy credentials to the server.

```sh
python3 infrastructure/deployment/scripts/manage.py verify --agent-template
python3 infrastructure/deployment/scripts/manage.py status
python3 infrastructure/deployment/scripts/manage.py rollback
```

`rollback` restores the last successful release after a failed rollout.
`rollback --previous` switches to the preceding successful release.

See [operations](docs/operations.md) for permissions, troubleshooting, cleanup,
and rollback, and [architecture](docs/architecture.md) for boundaries.

## Local development

Use Node 24 and Python 3.12. Python resolution uses uv 0.12.23:

```sh
python3 -m venv .tools-venv
.tools-venv/bin/pip install uv==0.12.23
.tools-venv/bin/uv sync --frozen
npm ci
npm run dev:web
# In separate terminals:
.venv/bin/python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
.venv/bin/python -m uvicorn apps.orchestrator.main:app --host 127.0.0.1 --port 8001 --reload
```

The web scaffold makes no backend calls yet, so no local proxy is needed.

```sh
.venv/bin/pytest -q
.venv/bin/ruff check apps tooling infrastructure/deployment/scripts
npm run typecheck:web
npm run build:web
```
