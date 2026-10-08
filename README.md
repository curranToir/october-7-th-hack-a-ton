# Company Brain

A hackathon foundation using Athena's application layout: Next.js, React,
TypeScript, and FastAPI on one dedicated AWS `t3.medium` running K3s.

The web app includes light/dark chat, sessions, task approvals and user settings.
Its fictional demo data stays in browser storage behind the root-level
[`coms/` data adapter](coms/README.md), ready for database integration.
The separate **Toir research workspace at `/research`** uses the API and a custom
LangGraph coordinator to run an Oh My Pi research agent in its own pod. It saves
research history and grounded evidence in local SQLite. Models use the Respan
gateway; public-web retrieval uses Exa through Scalekit. The chat/approval demo
remains browser-local and does not execute its example actions.

Research setup: [Scalekit connection](docs/scalekit-setup.md),
[server operations](docs/operations.md), and
[database engineer handoff](coms/database-handoff.md).

The separate [RAG service](rag-db-api/README.md) has its own setup instructions
and is not included in the foundation's Kubernetes deployment.

## Customer meeting agent

The [meeting agent plan and setup guide](docs/meeting-agent.md) covers Zoom attendance,
customer notes, source-backed company/person research, and Tasks approvals that publish
GitHub issues. Open **Meetings → Run demo** for a fictional saved call; live
Zoom capture uses Recall.ai, with owner `curran@toirinc.com`. Scalekit supplies workspace
sign-in and the Exa research connection. Live capture and publishing require the
provider credentials and public webhook setup described in the guide.

## Layout

```text
apps/
  web/                 Next.js application shell
  api/                 Browser-facing FastAPI host
  orchestrator/        Internal LangGraph coordinator and SQLite adapters
agents/research/       Internal Oh My Pi research agent (Bun)
products/              Reserved for product boundaries
compositions/          Reserved for workflows
rag-db-api/            Separate RAG service with its own setup
infrastructure/
  docker/              Four production Dockerfiles
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
internal Service. The first implemented capability is Toir sales research.

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

The browser-local workspace demo does not require the backend. For live research,
use the deployed SSM tunnel at `http://localhost:8080/research`; the ingress sends
`/api/*` to FastAPI. Runtime secrets stay on the server. See operations for local
service ports and the secure secret-entry commands.

```sh
.venv/bin/pytest -q
.venv/bin/ruff check apps tooling infrastructure/deployment/scripts
npm run typecheck:web
npm run test:web:coms
npm run build:web
```
