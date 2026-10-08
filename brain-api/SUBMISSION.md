# Team Submission

## Team

- Team name: Toir Inc
- Participants: Curran McLaughlin, Jared Lyon
- Company Brain / project name: Toir FDE Brain: the institutional memory of a forward-deployed-engineering firm

## Company Brain Overview

Toir Inc is (role-played as) an FDE shop embedded with three clients: Acme Logistics, Globex Health and Initech Finance. Its knowledge is split across client Slack channels, per-client GitHub repos and the HubSpot CRM, where deals, contacts and SOW/contract notes live. The brain pulls all three through Scalekit per user, remembers them into Cognee as per-client **engineering** and **commercial** datasets, and answers cross-source questions such as "who owns the Acme rollout, what blocks go-live, and what did the SOW promise?". The engagement lead sees everything. An FDE engineer sees only the clients they're staffed on, and never commercial terms. Staffing someone onto a client is a live grant.

- Data sources connected through Scalekit (≥ 2 apps): Slack, GitHub, HubSpot
- Primary use case / team workflow: client-engagement Q&A, pre-call account briefs and engineering-request triage (an issue opened in the right client repo)
- Users in the demo and how their access differs: `jared@neptuneops.com` (engagement lead on Acme + Initech, firm principal: all commercial datasets plus Acme/Initech eng). `curran@toirinc.com` (FDE engineer on Globex: `globex-eng`, `toir-firm`, `toir-pipeline`).
- What makes it stand out:
  - Two-layer access: per-client eng vs commercial datasets.
  - The API tells the agent what it couldn't see (client, layer, owner) without leaking content, so the agent can route an access request.
  - A Spark-hosted Cognee with local Nemotron embeddings.

## The Three Layers

### Pull — Scalekit

- Connections created (`connection_name` → app): `slack` → Slack (Toir Inc workspace), `github-connect` → GitHub (org `Toir-FDE-Team`), `hubspot` → HubSpot
- Tools called: `slack_list_channels`, `slack_list_users`, `slack_fetch_conversation_history`, `slack_get_conversation_replies`, GitHub issues/comments/PR/readme list tools, HubSpot company/deal/contact/note read tools
- How users are identified: the Scalekit `identifier` **is** the Cognee user email. Each source container (channel / repo / company) routes to exactly one dataset with a fixed owner; the pull uses the owner's connected account when ACTIVE, otherwise the other user's.
- Write-back actions: the triage agent (Curran's orchestrator) opens GitHub issues as the acting user via Scalekit `execute_tool`.
- Code entry point: `brain-api/brain/scalekit_pull.py`, `POST /pull`

### Remember — Cognee

- Permanent graph: every pulled Slack channel transcript (one line per message with channel/author/timestamp), GitHub issue/PR (with comments), HubSpot company (deals, contacts, notes) and accepted research lead. Each document starts with a `[[source=…; container=…; client=…; layer=…; title=…; url=…]]` provenance header.
- Session memory: agent conversation turns (`session_id`) only. Never cognified into the graph.
- `node_set` tags: `source:<slack|github|hubspot|research>`, `client:<acme|globex|initech|toir>`, `layer:<eng|commercial|firm>`, `container:<channel|repo|company>`
- Datasets and who owns / can read each:

  | dataset | owner | also readable by |
  |---|---|---|
  | `acme-eng`, `initech-eng` | jared | (live grant demo: `acme-eng` → curran) |
  | `acme-commercial`, `initech-commercial`, `globex-commercial` | jared | — |
  | `globex-eng` | curran | — |
  | `toir-firm` | jared | curran |
  | `toir-pipeline` | jared | curran (read + write) |

- Access control: `ENABLE_BACKEND_ACCESS_CONTROL=true`. Each user+dataset gets its own Ladybug graph and LanceDB store. Grants and revokes go through `authorized_give/revoke_permission_on_datasets`.
- Beyond defaults:
  - Custom FDE graph model (Client, Person, Deal, SOW, Commitment, EngineeringRequest, Deployment, Decision) with identity fields, plus a custom extraction prompt and `improve()`.
  - `GRAPH_COMPLETION` per readable dataset, then a single synthesis over only the permitted context.
  - Local Nemotron-embed-1B (2048-d) on a DGX Spark; Postgres for Cognee's relational store.
- Code entry point: `brain-api/brain/memory.py`, `brain-api/brain/graph_model.py`

### Act + Evaluate — your agent(s) + Respan

- Agent(s) and the task each performs:
  - Curran's orchestrator (LangGraph coordinator + workers) calls `/recall` for briefs and triage, opens issues via Scalekit, and syncs accepted prospecting research into `toir-pipeline` via `/remember/research`.
  - The brain itself answers `/recall`.
- LLM calls routed through the Respan gateway? Yes: current code uses `gpt-5-mini` for Cognee extraction/completion, answer synthesis and the judge. Historical evaluation evidence below retains the model configuration used when those runs were recorded.
- How the runs are traced: `respan-ai` SDK. `@workflow("brain.recall")`, `@task` spans for pull, remember and synthesis, and `@workflow("eval.scenario")` per scenario with `scenario_id`, `run_label`, `expected` and `output` attributes.
- Scenario file: `brain-api/eval/scenarios.json` (14 scenarios: 9 qa, 4 access, 1 grant, 2 action)
- Evaluator:
  - Deterministic Python checks: every `must_mention` present, no `must_not_mention` leak, `expected_sources` ⊆ returned `source:*` tags.
  - LLM judge `gpt-5-mini` with low reasoning and an 8,192 completion-token budget via the Respan gateway; no temperature parameter.
  - Neither is the agent.
- Code entry point: `brain-api/eval/run.py`

## Evaluation Evidence

### Baseline Run

- Respan trace / eval run link: Respan project → workflow `eval.scenario`, `run_label=before`
- Scenarios run: 11 (grant + 2 action scenarios run separately)
- Mean score: **4/11 pass**; judge mean 0.48; must-mention coverage 0.74
- Worst scenario and why it failed:

```text
question: What date does the Globex clinical pilot SOW specify?
expected: 2026-11-16 (source:hubspot)
got:      "I cannot answer… the context contains no SOW"  (SOWs live only in HubSpot notes)
score:    judge 0.0, mention 0/1
```

### Improved Run

- Respan trace / eval run link: Respan project → workflow `eval.scenario`, `run_label=after`
- What changed: **Added HubSpot (CRM: deals, contacts, SOW notes) as a third Scalekit source.** Same code, same questions.
- Mean score: **11/11 pass**; judge mean 0.64; must-mention coverage 1.00

```text
Before:  pass = 4/11   judge mean = 0.48   (n = 11 scenarios)
After:   pass = 11/11  judge mean = 0.64   (n = 11 scenarios)
Grant scenario s11 (curran, after acme-eng grant): PASS, judge 1.0
```

Results: `brain-api/eval/results/{before,after,grant}.json`.

## Access Story

- User A: `jared@neptuneops.com`. Connections: slack, github-connect. Readable: all `*-commercial`, `acme-eng`, `initech-eng`, `toir-firm`, `toir-pipeline`.
- User B: `curran@toirinc.com`. Connections: hubspot (github-connect pending). Readable: `globex-eng`, `toir-firm`, `toir-pipeline`. GitHub: collaborator only on `globex-clinical-rag` and `toir-playbooks`.
- Question asked by both: "Who owns the Acme rollout and what is blocking go-live?"
- Result for A: Maya Chen (Acme technical DRI). The duplicate-shipment replay bug blocks go-live (dedupe on `shipment_id` + `event_version`; rollback above 0.2% for 15 min). Go-live 2026-11-02 per the signed SOW. Sources: github, hubspot, slack.
- Result for B before the share: no Acme information, and `withheld: [{client: acme, layer: eng, owner: jared@…}, {client: acme, layer: commercial, owner: jared@…}]`, so the agent can ask Jared for access instead of guessing.
- The grant: Jared → Curran, `read` on `acme-eng` (`POST /grant`)
- Result for B after the share: the same owner and blockers, from github + slack. The SOW go-live date is **still absent** and `withheld` still lists `acme commercial`. The engineering grant does not expose commercial terms. `POST /revoke` closes it again.

## Architecture

```text
Slack ─┐  GitHub ─┐  HubSpot ─┐            (Scalekit connected accounts, per user identifier)
       └──────────┴───────────┴─► brain-api /pull  ──► scalekit_pull.execute_tool(identifier=…)
                                        │  route container → dataset (owner fixed)
                                        ▼
                     Cognee 1.6.3 (ACL on) remember(dataset, node_set=[source,client,layer,container], user=owner)
                     per-user/per-dataset Ladybug graph + LanceDB · Postgres metadata · Nemotron embeddings (Spark GPU)
                                        │
                     /recall(as_user) → readable datasets only → GRAPH_COMPLETION context → one synthesis
                                        │                      + withheld[{client,layer,owner}]
                                        ▼
        Curran's coordinator (AWS K3s, over Tailscale, static bearer) ── triage/brief agents ── Scalekit write actions
                                        │
                     Respan: gateway (gpt-5-mini) · traces (brain.recall, eval.scenario) · judge
```

Access is enforced twice: by Scalekit (what each user's token can read) and by Cognee dataset permissions (what each user can recall).

## Reproduction

```bash
cd brain-api
cp .env.example .env            # fill RESPAN_API_KEY, SCALEKIT_*, BRAIN_API_TOKEN, POSTGRES_PASSWORD
docker compose up -d            # Postgres (pgvector) + vLLM Nemotron embed
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m brain serve &
# Judges without our SaaS accounts: replay the recorded pull
.venv/bin/python -m brain pull --as-user jared@neptuneops.com --sources slack github --from-recorded
.venv/bin/python eval/run.py --label before
.venv/bin/python -m brain pull --as-user curran@toirinc.com --sources hubspot --from-recorded
.venv/bin/python eval/run.py --label after
./demo.sh                       # live access story: ask → withheld → grant → ask → revoke
```

Environment variables required: see `brain-api/.env.example` (every key listed). No embedding key is needed when using the local Nemotron server. To use the gateway instead, set `EMBEDDING_PROVIDER=custom`, `EMBEDDING_ENDPOINT=https://api.respan.ai/api`, `EMBEDDING_MODEL=openai/text-embedding-3-large`, `EMBEDDING_DIMENSIONS=3072`.

Judges without our SaaS accounts: `brain-api/data/recorded/` holds the raw Scalekit responses, and `brain-api/seed/world.json` holds the full fictional world (all content is synthetic).

## Demo

- Local instructions: `brain-api/demo.sh` against the Spark API; graph view at `/graph?dataset=acme-eng`
- 3-minute pitch outline:

```text
1. Toir: FDE firm; knowledge scattered across client Slack, client repos, HubSpot
2. Pull: /pull as Jared (Slack+GitHub) and Curran (HubSpot): Scalekit identifiers
3. Brain: Cognee graph (/graph) + the Acme cross-source answer (Slack owner + GitHub blocker + HubSpot SOW date)
4. Access: Curran asks → withheld; Jared grants acme-eng → answered; SOW terms stay hidden; revoke
5. Agent: Curran's triage agent recalls and opens the Acme issue as the user via Scalekit (traced)
6. Eval: 4/11 → 11/11 after adding HubSpot; traces + judge scores in Respan
7. Next: continuous prospecting synced into toir-pipeline
```

## Links

- Repo: https://github.com/curranToir/october-7-th-hack-a-ton (`brain-api/`)
- Respan traces / eval runs: Respan project for the event key → workflows `eval.scenario`, `brain.recall`
- Anything else: seeded world `brain-api/seed/world.json`; scenario results `brain-api/eval/results/`
