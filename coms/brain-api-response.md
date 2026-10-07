# Re: database handoff — Spark side (Jared)

Replies to `coms/database-handoff.md` (f4d5294) and sets the Spark ↔ AWS
contract for today. Hackathon rules: `cognee-hackathons/.../COMPANY_BRAIN.md`
(≥2 Scalekit sources, ≥2 users with different access, Cognee memory per user,
Respan traces + before/after eval).

## Blocked on people, not code (single list)

| # | Blocker | Owner | Unblocks |
|---|---------|-------|----------|
| 1 | Respan event key (`RESPAN_API_KEY`) shared with both of us, out of band | whoever received it at kickoff (confirm) | all LLM calls, cognify, tracing, eval |
| 2 | Respan-hosted evaluator (LLM judge `openai/gpt-5-mini`, temp 0) configured in the Respan UI | Jared | scored before/after runs, live-traffic scoring |
| 3 | Scalekit environment created; connections named exactly `slack` (user scope), `github`, `notion` | Curran | every pull and write action |
| 4 | `SCALEKIT_ENVIRONMENT_URL` / `SCALEKIT_CLIENT_ID` / `SCALEKIT_CLIENT_SECRET` sent to Jared out of band | Curran | Spark pulls, minting your M2M client |
| 5 | Confirm the Scalekit M2M / API-client feature is enabled on that env | Curran | auth between your pods and brain-api |
| 6 | Slack workspace `toir-fde` + seeding app; invites to Curran | Jared | Slack seed + pull |
| 7 | GitHub org `toir-fde-demo`; Curran added as collaborator per the access matrix | Jared | GitHub seed + pull |
| 8 | Notion workspace `Toir FDE` + seeding integration; pages shared per the access matrix | Jared | Notion seed + pull (the "after" run) |
| 9 | Both users authorize `slack`, `github`, `notion` through the Scalekit links | Jared + Curran | per-user pulls, actions as the user |
| 10 | EC2 node joins our tailnet; pod egress over `tailscale0` verified | Curran | your pods reaching brain-api and Postgres |
| 11 | Push `apps/orchestrator/storage/ports.py`, `models/research.py`, `tooling/tests/test_research_storage.py` | Curran | our Postgres RunRepository adapter |
| 12 | Triage agent endpoint shared with us | Curran | scoring action scenarios |

## Your database ask: accepted, on the Spark, private

- Postgres 17 on the Spark, reached **over Tailscale only**. Nothing public, no
  Funnel, no inbound EC2 rule. **Your EC2 node joins our tailnet**: add
  `tailscaled` + an auth key to the CloudFormation bootstrap. Pods reach the
  Spark through node NAT; please verify pod egress over `tailscale0` under
  K3s/flannel.
- Database `toir_runs`, role `toir_runs` (only that DB, connection limit 10).
  `hostssl` + scram only, from 100.64.0.0/10. TLS uses a cert for the Spark's
  MagicDNS name, so use `sslmode=verify-full`. If the tailnet has HTTPS certs
  disabled we'll hand you a `ca.crt` instead. The DATABASE_URL goes to your
  Secrets Manager out of band, never in this repo.
- Adapter: we write `apps/orchestrator/storage/postgres.py` (RunRepository on
  Postgres, `one_active_run` partial unique index, monotonic event sequence)
  and the `AsyncPostgresSaver` selection by `DATABASE_URL`, then run
  `tooling/tests/test_research_storage.py` against it. **Blocked until you push
  `storage/ports.py`, `models/research.py` and that test file.** Missing
  `DATABASE_URL` keeps SQLite, as you specified.

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
2. **Scalekit env** (you create it): connections named exactly `slack`
   (user scope), `github`, `notion`. Send us `SCALEKIT_ENVIRONMENT_URL`,
   `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET` out of band, and confirm the
   M2M / API-client feature is enabled on the env. Both of us then authorize
   all three connections.
3. **EC2 on the tailnet** (above).

## What we own

Spark infra, seeding the fictional world into Slack/GitHub/Notion (we create the
workspace, org and Notion; invites coming), the Scalekit pulls, Cognee memory
(custom FDE graph model, per-client eng/commercial datasets), the API, the
scenario set (`brain-api/eval/scenarios.json`), and the eval runner plus the
Respan-hosted judge (`openai/gpt-5-mini`, temp 0). Action scenarios
(`expected_action`) will call your triage agent once you give us its endpoint.
