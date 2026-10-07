# Re: database handoff — Spark side (Jared)

Replies to `coms/database-handoff.md` (f4d5294) and sets the Spark ↔ AWS
contract for today. Hackathon rules: `cognee-hackathons/.../COMPANY_BRAIN.md`
(≥2 Scalekit sources, ≥2 users with different access, Cognee memory per user,
Respan traces + before/after eval).

## Integration status (single list)

Updated for Curran on October 7, 2026 (America/Los_Angeles). Repository files,
storage tests and AWS secret presence were checked directly. EC2/Tailscale membership, pod DNS and network reachability were tested directly;
Spark database/API ports currently refuse connections. See the linked handoff
for exact results. Broader Scalekit setup is not verified end to end.

| # | Status / remaining work | Owner | Unblocks |
|---|---------|-------|----------|
| 1 | **Done (Jared, 16:10):** read from `/company-brain-hackathon/respan` and installed on the Spark. A live gateway call works (`claude-haiku-4-5`, `gpt-5-mini`). | — | — |
| 2 | Respan evaluator (judge `gpt-5-mini`, temp 0, on workflows `eval.scenario` + `brain.recall`). Jared accepts the invite to `jared@neptuneops.com` and sets it up. | Jared | scored before/after, live-traffic scoring |
| 3 | **Sources changed (Jared, 16:15): we use what you connected** (`slack`, `hubspot`, `google-drive`) **plus a new `github` connection**. Notion is dropped and Gmail is unused. Jared tries to create `github` via the SDK with the env creds; if that's refused, please create it in the dashboard (AgentKit → Connections → GitHub, name exactly `github`, Scalekit's OAuth app). | Jared, fallback Curran | GitHub pull + triage action |
| 4 | **Done (Jared, 16:10):** read from `/company-brain-hackathon/scalekit` and installed on the Spark. A client-credentials token works. | — | — |
| 5 | **M2M dropped:** brain-api uses a static bearer, tailnet only. Token + URL are in **`/company-brain-hackathon/brain-api`** (`BRAIN_API_TOKEN`, `BRAIN_API_URL=http://100.87.113.122:8200`). Send `Authorization: Bearer <token>`. | Curran (consume) | your pods → brain-api |
| 6 | We seed the fictional world **into your Toir sandbox** Slack/HubSpot/Drive through Scalekit as `curran@toirinc.com`: channels `toir-general`, `acme-eng`, `acme-deal`, `initech-eng`, `initech-deal`, `globex-eng`, `globex-deal`; HubSpot companies Acme Logistics / Globex Health / Initech Finance + `Toir Prospect: *`; Drive folder `Toir FDE/` with 8 dataset-named subfolders. Seeding is in progress. | Jared | all pulls |
| 7 | **Invite `jared@neptuneops.com` with less access than you:** Slack (all 7 channels **except `globex-eng`**), HubSpot (user), Drive `Toir FDE/` subfolders **except `globex-eng`**. This is the Scalekit-layer access difference. | Curran | Jared as user #2 |
| 8 | GitHub org `toir-fde-demo` (Jared creates it). Repos `acme-agent-rollout`, `initech-evals`, `globex-clinical-rag`, `toir-playbooks`; `curranToir` is a collaborator on globex + playbooks only. | Jared | GitHub seed + pull |
| 9 | Both users authorize `slack`, `github`, `hubspot`, `google-drive` via Scalekit links. You're ACTIVE on slack/hubspot/google-drive; still to do: you on `github`, Jared on all four. | Jared + Curran | per-user pulls, actions as the user |
| 10 | **EC2 joined and pod network verified:** `toir-hackathon` / `100.86.7.62` in `neptuneops.com`; pod DNS resolves `waffle-spark` and host/pod TCP 22 succeeds. Spark `100.87.113.122:5432` and `:8200` return connection refused; database/API service readiness is still pending. See [EC2 handoff](ec2-tailscale-handoff.md). | Curran (network done), Jared (Spark listeners) | your pods reaching brain-api and Postgres |
| 11 | **Done:** files on `main` (`5d66e70`). **Jared is building the Postgres adapter now**: `storage/postgres.py`, factory/checkpointer selection by `DATABASE_URL`, and the suite parameterized over both backends. DB `toir_runs` on the Spark; its `DATABASE_URL` will go into your Secrets Manager. | Jared | your durable run state |
| 12 | Triage agent endpoint shared with us: `POST {as_user, request}` → JSON with an `action` field (tool, repo, assignee, labels) so the eval can score action scenarios. | Curran | scoring action scenarios |

The status table includes Jared's latest reported changes and supersedes the
earlier integration proposal below, including its source list and M2M-auth plan.

### Verified files and secret handoff

The requested files were published in
[`5d66e70`](https://github.com/curranToir/october-7-th-hack-a-ton/commit/5d66e707f260f0e6d204806d98ca2120d81bfeda):

- [`apps/orchestrator/storage/ports.py`](../apps/orchestrator/storage/ports.py): `RunRepository` protocol and `Conflict`.
- [`apps/orchestrator/models/research.py`](../apps/orchestrator/models/research.py): validated brief, run, event and research/evidence models.
- [`tooling/tests/test_research_storage.py`](../tooling/tests/test_research_storage.py): `.venv/bin/python -m pytest tooling/tests/test_research_storage.py -q` → **14 passed** on October 7.

The current tests instantiate `SQLiteRunRepository` directly. Extend or
parameterize them for Postgres, including adapter-specific cases; this result
does not validate a Postgres implementation. The startup integration point is
[`apps/orchestrator/storage/factory.py`](../apps/orchestrator/storage/factory.py).
It currently uses SQLite when `DATABASE_URL` is absent and rejects a configured
`DATABASE_URL` until the replacement adapter is installed.

Both AWS secret checks verified nonempty required fields in the `AWSCURRENT`
versions, without displaying values. Use authorized Secrets Manager access or
an out-of-band handoff to Jared; no credential values belong in this repository.
Stored credentials do not establish Spark access, Respan project membership,
successful provider calls, or Scalekit M2M readiness. Setup remains owned by
the owners listed in the current status table above.
The EC2 tailnet join and pod network checks are complete;
Spark service readiness remains pending.

## Your database ask: accepted, on the Spark, private

- Postgres 17 on the Spark, reached **over Tailscale only**. Nothing public, no
  Funnel, no public inbound EC2 rule. **The EC2 node has joined the tailnet**
  as `100.86.7.62`, using persistent `tailscaled` and interactive enrollment.
  The repeatable SSM helper is documented in [Tailscale operations](../docs/tailscale.md).
  Pod egress over `tailscale0` with existing K3s/Flannel NAT is verified;
  PostgreSQL on the Spark Tailscale address is currently refusing connections.
- Database `toir_runs`, role `toir_runs` (only that DB, connection limit 10).
  `hostssl` + scram only, from 100.64.0.0/10. TLS uses a cert for the Spark's
  MagicDNS name, so use `sslmode=verify-full`. If the tailnet has HTTPS certs
  disabled we'll hand you a `ca.crt` instead. The DATABASE_URL goes to your
  Secrets Manager out of band, never in this repo.
- Adapter: we write `apps/orchestrator/storage/postgres.py` (RunRepository on
  Postgres, `one_active_run` partial unique index, monotonic event sequence)
  and the `AsyncPostgresSaver` selection by `DATABASE_URL`, then run
  `tooling/tests/test_research_storage.py` against it. **The requested interfaces,
  models and tests are now on `main` (`5d66e70`); the file-publication blocker is
  cleared.** Missing `DATABASE_URL` keeps SQLite, as you specified.

## Company Brain: what the Spark serves

`rag-db-api/` becomes `brain-api/`. The old RAG package is deleted, and Cognee
1.6.3 is embedded in one FastAPI process (one writer, ACL on). Firm =
**Toir Inc** (FDE shop), clients Acme Logistics, Globex Health and Initech
Finance. Sources are pulled through Scalekit: Slack (`toir-fde` workspace),
GitHub (org `toir-fde-demo`), Notion (`Toir FDE`). Notion is added between the
before and after eval runs; that's our named change.

Users (Scalekit identifier == Cognee user):

- `jared@neptuneops.com`: Acme/Initech lead and firm principal. Reads all
  commercial datasets plus acme/initech eng.
- `curran@toirinc.com`: FDE engineer on Globex. Reads `globex-eng` and
  `toir-firm`. Live demo grant: `acme-eng` → you; `acme-commercial` stays hidden.

API: `http://<spark tailnet IP>:8200`, tailnet only. Every route except
`/health` and `/graph` needs a **Scalekit M2M bearer**: audience `brain-api`,
scopes `brain:query` / `brain:ingest` / `brain:admin`. We mint your API client
once we have env creds.

```
POST /recall {as_user, question, mode:"answer"|"context", session_id?, top_k?}
  -> {answer, context:[{text, dataset, sources}], sources, datasets_searched,
      withheld:[{client, layer, owner}]}
POST /pull {as_user, sources?} -> {job_id};  GET /pull/{job_id}
GET  /access/{user};  POST /grant | /revoke {owner, grantee, dataset};  POST /forget
POST /remember/research {as_user, run_id, report}   # your accepted lead reports
GET  /graph?dataset=<name>                           # graph view for the demo
```

- `withheld` tells your agent that a client/layer exists that this user can't
  read, plus who owns it. Use it to route an access request instead of
  answering.
- `session_id` is scratchpad memory only and is never written into the graph.
- Accepted research reports go to `toir-pipeline` (commercial, Jared-only), with
  cited source text kept.

## What you own today

1. **Agents**: (a) eng-request triage: request in Slack → `/recall` → open a
   GitHub issue **as the asking user via Scalekit** (`execute_tool`, never a bot
   token); (b) pre-call account brief → Slack DM as the user. Route your LLM
   calls through the Respan gateway (`https://api.respan.ai/api`, event key) and
   trace with `respan-ai`.
2. **Scalekit env**: environment/Slack setup is made per Curran's update;
   finish connections named exactly `github` and `notion`. The required env
   credentials are verified in `/company-brain-hackathon/scalekit`; arrange
   Jared's access or an out-of-band handoff, and confirm the M2M / API-client
   feature is enabled. Both of us then authorize all three connections.
3. **EC2 on the tailnet**: joined as `toir-hackathon` (`100.86.7.62`). Pod
   connectivity and MagicDNS are verified. Spark ports 5432/8200 currently refuse
   connections; see [EC2 handoff](ec2-tailscale-handoff.md) for the source address
   and remaining database/API checks.

## What we own

Spark infra, seeding the fictional world into Slack/GitHub/Notion (we create the
workspace, org and Notion; invites coming), the Scalekit pulls, Cognee memory
(custom FDE graph model, per-client eng/commercial datasets), the API, the
scenario set (`brain-api/eval/scenarios.json`), and the eval runner plus the
Respan-hosted judge (`openai/gpt-5-mini`, temp 0). Action scenarios
(`expected_action`) will call your triage agent once you give us its endpoint.
