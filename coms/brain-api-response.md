# Re: database handoff — Spark side (Jared)

Replies to `coms/database-handoff.md` (f4d5294) and sets the Spark ↔ AWS
contract for today. Hackathon rules: `cognee-hackathons/.../COMPANY_BRAIN.md`
(≥2 Scalekit sources, ≥2 users with different access, Cognee memory per user,
Respan traces + before/after eval).

## Integration status (single list)

Updated for Curran on October 7, 2026 (America/Los_Angeles). Repository files,
storage tests and AWS secret presence were checked directly. Scalekit connection
setup and the EC2/Tailscale work below reflect Curran's current progress; they
have not been independently tested end to end.

| # | Status / remaining work | Owner | Unblocks |
|---|---------|-------|----------|
| 1 | **Key stored, verified:** nonempty `RESPAN_API_KEY` in AWS Secrets Manager `/company-brain-hackathon/respan`, `us-east-1`. Jared's retrieval/access and successful use on Spark still need confirmation. | Curran (access), Jared (consume) | all LLM calls, cognify, tracing, eval |
| 2 | Respan-hosted evaluator (LLM judge `openai/gpt-5-mini`, temp 0) configured in Curran's Respan project; Jared invited to that project | Curran (invite), Jared (evaluator) | scored before/after runs, live-traffic scoring |
| 3 | **Partially complete (Curran update):** Scalekit environment and Slack setup are made; `github` and `notion` connections remain. Required names stay exactly `slack` (user scope), `github`, `notion`. | Curran | every pull and write action |
| 4 | **Credentials stored, verified:** nonempty `SCALEKIT_ENVIRONMENT_URL`, `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET` in AWS Secrets Manager `/company-brain-hackathon/scalekit`, `us-east-1`. Jared's retrieval/access still needs confirmation. | Curran (access), Jared (consume) | Spark pulls, minting your M2M client |
| 5 | Explicit confirmation of Scalekit M2M / API-client enablement is still outstanding; credential presence does not verify this feature or a `brain-api` client. | Curran | auth between your pods and brain-api |
| 6 | Slack workspace `toir-fde` + seeding app; invites to Curran | Jared | Slack seed + pull |
| 7 | GitHub org `toir-fde-demo`; Curran added as collaborator per the access matrix | Jared | GitHub seed + pull |
| 8 | Notion workspace `Toir FDE` + seeding integration; pages shared per the access matrix | Jared | Notion seed + pull (the "after" run) |
| 9 | Both users must authorize `slack`, `github`, `notion` through the Scalekit links. Per-user authorization is not verified; GitHub and Notion also await connection setup. | Jared + Curran | per-user pulls, actions as the user |
| 10 | **In progress (Curran update):** Curran is moving EC2 onto Tailscale. Tailnet join and pod egress over `tailscale0` are not yet verified. | Curran | your pods reaching brain-api and Postgres |
| 11 | **Done, verified on GitHub:** all three requested files are on `main` in `5d66e70`; SQLite storage suite passes **14 tests**. Jared can start the Postgres adapter. | Curran (published), Jared (adapter) | our Postgres RunRepository adapter |
| 12 | Triage agent endpoint shared with us | Curran | scoring action scenarios |

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
Curran, with GitHub/Notion connections and EC2/Tailscale work still in progress
as listed above.

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
3. **EC2 on the tailnet**: in progress with Curran; verify the node join and pod
   connectivity before marking this complete.

## What we own

Spark infra, seeding the fictional world into Slack/GitHub/Notion (we create the
workspace, org and Notion; invites coming), the Scalekit pulls, Cognee memory
(custom FDE graph model, per-client eng/commercial datasets), the API, the
scenario set (`brain-api/eval/scenarios.json`), and the eval runner plus the
Respan-hosted judge (`openai/gpt-5-mini`, temp 0). Action scenarios
(`expected_action`) will call your triage agent once you give us its endpoint.
