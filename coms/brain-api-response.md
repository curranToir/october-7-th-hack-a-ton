# Re: database handoff — Spark side (Jared)

Replies to `coms/database-handoff.md` (f4d5294) and sets the Spark ↔ AWS
contract for today. Hackathon rules: `cognee-hackathons/.../COMPANY_BRAIN.md`
(≥2 Scalekit sources, ≥2 users with different access, Cognee memory per user,
Respan traces + before/after eval).

## Historical October 7 owner handoff

The table below preserves the handoff at that time. The current application-side
implementation and owner requirements are tracked in
[prospecting implementation](prospecting-implementation.md). No new live network
or provider success is implied by repository changes.

## Integration status (historical owner reports and checks)

Updated for Curran on October 7, 2026 (America/Los_Angeles). Repository files,
storage tests and AWS secret presence were checked directly. EC2/Tailscale membership, pod DNS and network reachability were tested directly;
After the Spark PostgreSQL rebind, host and pod TCP 5432 and system-trusted,
exact-hostname TLS verification passed. The later brain-api recheck passed TCP
8200 and unauthenticated `/health` (HTTP 200) on host and pod.
See the [EC2 handoff](ec2-tailscale-handoff.md) for exact results. Broader Scalekit
setup is not verified end to end.

| # | Status / remaining work | Owner | Unblocks |
|---|---------|-------|----------|
| 1 | **Done (Jared, 16:10):** read from `/company-brain-hackathon/respan` and installed on the Spark. A live gateway call works (`claude-haiku-4-5`, `gpt-5-mini`). | — | — |
| 2 | Respan evaluator (judge `gpt-5-mini`, temp 0, on workflows `eval.scenario` + `brain.recall`). No invite needed; Jared sets it up with the shared key. Our eval runner already scores locally with the same judge. | Jared | hosted scoring, live-traffic bonus |
| 3 | **Done:** the GitHub connection already existed as **`github-connect`** (Scalekit's OAuth app); we use that name. Sources: `slack`, `github-connect`, `hubspot`. **Google Drive dropped**: Scalekit's Drive tools can't write file content, so SOW/contract text lives in HubSpot notes. Gmail is unused. | — | — |
| 4 | **Done (Jared, 16:10):** read from `/company-brain-hackathon/scalekit` and installed on the Spark. A client-credentials token works. | — | — |
| 5 | **M2M dropped:** brain-api uses a static bearer, tailnet only. Token + URL are in **`/company-brain-hackathon/brain-api`** (`BRAIN_API_TOKEN`, `BRAIN_API_URL=http://100.87.113.122:8200`). Send `Authorization: Bearer <token>`. | Curran (consume) | your pods → brain-api |
| 6 | **⚠ BLOCKED, needs you now:** your Scalekit Slack account lacks `channels:write`, so we can't create channels. **Please create 7 public channels** in the Toir Slack: `toir-general`, `acme-eng`, `acme-deal`, `initech-eng`, `initech-deal`, `globex-eng`, `globex-deal`. Our seeder polls for them and posts automatically (it has `chat:write`). HubSpot + GitHub seeding is running through Scalekit / gh. | **Curran** | Slack seed, before-run |
| 7 | **Invite `jared@neptuneops.com`:** Toir Slack (all channels **except `globex-eng`**) and HubSpot (user). Drive no longer needed. **Also accept the GitHub invitations** from `Toir-FDE-Team` to `globex-clinical-rag` and `toir-playbooks` (issues get assigned to you once you accept). | Curran | Jared as user #2, Globex issue assignment |
| 8 | **Done:** GitHub org is **`Toir-FDE-Team`** (jaredlyon admin). Repos `acme-agent-rollout`, `initech-evals`, `globex-clinical-rag`, `toir-playbooks`; `curranToir` is a collaborator on globex + playbooks only. | — | — |
| 9 | Both users authorize via Scalekit: you on **`github-connect`**; Jared on `slack`, `hubspot`, `github-connect`. | Jared + Curran | per-user pulls, actions as the user |
| 10 | **Postgres is listening now** on `waffle-spark.taild4c940.ts.net:5432` (100.87.113.122), Tailscale-issued cert. Role `toir_runs` is TLS-only and limited to DB `toir_runs`. Your earlier refusal predates the rebind. **`DATABASE_URL` is in `/company-brain-hackathon/database`** (verify-full; `sslrootcert=/etc/ssl/certs/ca-certificates.crt`, because psycopg's bundled libpq can't use `system`). brain-api `:8200` comes up shortly. | Curran (wire secret into coordinator) | your durable run state |
| 11 | **Done:** Postgres adapter + `AsyncPostgresSaver` selected by `DATABASE_URL` (`storage/postgres.py`, `factory.py`). Storage suite **27 passed** on both backends; full `tooling/tests` 130 passed. Production schema and checkpointer tables are initialized (no runs). See `database-handoff.md` § Postgres option. | — | — |
| 12 | Triage agent endpoint shared with us: `POST {as_user, request}` → JSON with an `action` field (tool, repo, assignee, labels) so the eval can score action scenarios. | Curran | scoring action scenarios |

The status table includes Jared's latest reported changes and supersedes the
earlier integration proposal below, including its source list and M2M-auth plan.

### Verified files and secret handoff

The requested files were published in
[`5d66e70`](https://github.com/curranToir/october-7-th-hack-a-ton/commit/5d66e707f260f0e6d204806d98ca2120d81bfeda):

- [`apps/orchestrator/storage/ports.py`](../apps/orchestrator/storage/ports.py): `RunRepository` protocol and `Conflict`.
- [`apps/orchestrator/models/research.py`](../apps/orchestrator/models/research.py): validated brief, run, event and research/evidence models.
- [`tooling/tests/test_research_storage.py`](../tooling/tests/test_research_storage.py): `.venv/bin/python -m pytest tooling/tests/test_research_storage.py -q` → **14 passed** on October 7.

The suite is now parameterized for SQLite/Postgres. The EC2-side local run on
October 7 passed 14 checks and skipped 13 Postgres checks because no dedicated
`TEST_DATABASE_URL` was configured. Jared's Spark-side result is recorded in
the status table above. The startup integration point is
[`apps/orchestrator/storage/factory.py`](../apps/orchestrator/storage/factory.py).
It uses SQLite when `DATABASE_URL` is absent and selects the new Postgres
repository/checkpointer when configured. The production dependency lock now
includes the Postgres driver and checkpointer; database migration/cutover is
still separate from this network handoff.

Both AWS secret checks verified nonempty required fields in the `AWSCURRENT`
versions, without displaying values. Use authorized Secrets Manager access or
an out-of-band handoff to Jared; no credential values belong in this repository.
Stored credentials do not establish Spark access, Respan project membership,
successful provider calls, or Brain API readiness. Setup remains owned by
the owners listed in the current status table above.
The EC2 tailnet join, pod network checks and PostgreSQL TLS checks are complete;
database login/cutover and authenticated brain-api integration remain pending.

## Your database ask: accepted, on the Spark, private

- Postgres 17 on the Spark, reached **over Tailscale only**. Nothing public, no
  Funnel, no public inbound EC2 rule. **The EC2 node has joined the tailnet**
  as `100.86.7.62`, using persistent `tailscaled` and interactive enrollment.
  The repeatable SSM helper is documented in [Tailscale operations](../docs/tailscale.md).
  Pod egress over `tailscale0` with existing K3s/Flannel NAT is verified;
  PostgreSQL TCP and verified TLS now pass from host and pod after the Spark rebind.
- Database `toir_runs`, role `toir_runs` (only that DB, connection limit 10).
  `hostssl` + scram only, from 100.64.0.0/10. TLS uses a cert for the Spark's
  MagicDNS name, so use `sslmode=verify-full`. If the tailnet has HTTPS certs
  disabled we'll hand you a `ca.crt` instead. The DATABASE_URL goes to your
  Secrets Manager out of band, never in this repo.
- Adapter: `apps/orchestrator/storage/postgres.py` and `AsyncPostgresSaver`
  selection by `DATABASE_URL` are implemented, with the one-active-run index
  and monotonic events. The owner reported storage contract tests passing. **The requested interfaces,
  models and tests are now on `main` (`5d66e70`); the file-publication blocker is
  cleared.** Missing `DATABASE_URL` keeps SQLite, as you specified.

## Company Brain API (updated handoff)

`rag-db-api/` becomes `brain-api/`. The old RAG package is deleted, and Cognee
1.6.3 is embedded in one FastAPI process (one writer, ACL on). Firm =
**Toir Inc** (FDE shop), clients Acme Logistics, Globex Health and Initech
Finance. Sources are pulled through Scalekit: Slack (`toir-fde` workspace),
GitHub (org `Toir-FDE-Team`, connection `github-connect`) and HubSpot. Earlier
Notion/Drive source plans are retired; SOW/contract text lives in HubSpot notes.

Users (Scalekit identifier == Cognee user):

- `jared@neptuneops.com`: Acme/Initech lead and firm principal. Reads all
  commercial datasets plus acme/initech eng.
- `curran@toirinc.com`: FDE engineer on Globex. Reads `globex-eng` and
  `toir-firm`. Live demo grant: `acme-eng` → you; `acme-commercial` stays hidden.

API: `http://<spark tailnet IP>:8200`, tailnet only. Every route except
`/health` and `/graph` uses the agreed **static bearer** from `/company-brain-hackathon/brain-api`.
M2M authentication was dropped; do not build against the retired audience/scope
proposal.

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
- Accepted research reports go to `toir-pipeline` with cited source text kept.
  The earlier handoff described Jared-only ingestion. Current checked-in
  `memory.remember_research` accepts either known user, preserving nested source
  citations, while `apply_initial_grants` grants Curran only `toir-firm`. Verify
  the deployed version, explicitly grant pipeline reads, and provide the missing
  idempotency/capability handshake. See the current implementation handoff.

## What you own today

1. **Agents**: (a) eng-request triage: request in Slack → `/recall` → open a
   GitHub issue **as the asking user via Scalekit** (`execute_tool`, never a bot
   token); (b) pre-call account brief → Slack DM as the user. Route your LLM
   calls through the Respan gateway (`https://api.respan.ai/api`, event key) and
   trace with `respan-ai`.
2. **Scalekit env**: environment/Slack setup is made per Curran's update;
   use current connections `github-connect`, `slack`, and `hubspot`. The env
   credentials were verified in `/company-brain-hackathon/scalekit`; arrange
   per-user authorizations as needed. The Brain API itself uses a static bearer.
3. **EC2 on the tailnet**: joined as `toir-hackathon` (`100.86.7.62`). Pod
   connectivity and MagicDNS are verified. PostgreSQL TCP/TLS and brain-api
   TCP 8200/unauthenticated `/health` now pass. See [EC2 handoff](ec2-tailscale-handoff.md) for the
   source address and remaining database/API checks.

## What we own

Spark infra, seeding the fictional world into Slack/GitHub/HubSpot, the Scalekit pulls, Cognee memory
(custom FDE graph model, per-client eng/commercial datasets), the API, the
scenario set (`brain-api/eval/scenarios.json`), and the eval runner plus the
Respan-hosted judge (`openai/gpt-5-mini`, temp 0). Action scenarios
(`expected_action`) will call your triage agent once you give us its endpoint.
