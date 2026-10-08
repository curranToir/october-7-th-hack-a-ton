# brain-api: current status (Jared, 17:20 PT)

This file is the current source of truth for the Spark side. It supersedes the
status table in `brain-api-response.md`, which is now historical.

## Live now

- **brain-api**: `http://100.87.113.122:8200` (tailnet only, systemd
  `brain-api.service`). Every route except `/health` and `/graph` needs
  `Authorization: Bearer <BRAIN_API_TOKEN>` from `/company-brain-hackathon/brain-api`.
  Contract: `brain-api/README.md`, code in `brain-api/brain/api.py`.
- **Postgres** `toir_runs`: `DATABASE_URL` is in `/company-brain-hackathon/database`.
  Adapter and checkpointer are on `main`. Your host/pod TLS checks passed.
- **Seeded fictional Toir FDE world**:
  - Slack: 56 messages across the 7 channels in the Toir Inc workspace.
  - GitHub `Toir-FDE-Team`: 4 private repos, 22 issues.
  - HubSpot: 5 companies, 5 deals, 8 contacts, 10 notes (the SOW/contract text).
  - Source of truth: `brain-api/seed/world.json`. Scenarios:
    `brain-api/eval/scenarios.json` (14).
- **Eval done** (`brain-api/eval/results/`): before (Slack + GitHub) **4/11** pass, judge mean 0.48 →
  after (+ HubSpot) **11/11**, judge mean 0.64. Grant scenario s11: PASS. Named change: *Added HubSpot
  (CRM: deals, contacts, SOW notes) as a third Scalekit source.* The 2 action scenarios wait on your agent endpoint.
- **Access demo** verified live: `brain-api/demo.sh` (ask → withheld → grant `acme-eng` → answered, commercial
  still withheld → revoke).
- **Submission draft**: `brain-api/SUBMISSION.md`, for you to submit (PR to topoteretes/cognee-hackathons or hand
  the link to organizers). Please swap in real Respan trace links.

## Decisions since the last table

| Topic | Decision |
|---|---|
| Slack identity | Jared's `slack` connection is the only one used, for seeding and pulls of all 7 channels. Your Slack account was reset by mistake on our side; since you aren't re-authorizing, it isn't needed. Consequence: no Slack-layer access difference. |
| Access story | Enforced by Cognee dataset ACLs (per-client eng/commercial datasets) plus GitHub repo access (you are a collaborator only on `globex-clinical-rag` and `toir-playbooks`). Demo: you ask about Acme → `withheld` → Jared runs `/grant acme-eng` → answered, while `acme-commercial` stays withheld. |
| HubSpot | Pulled with your `hubspot` connection (ACTIVE). Jared doesn't need a HubSpot invite. |
| GitHub | Connection `github-connect`. Pulls fall back to Jared's account until yours is ACTIVE. |
| `/remember/research` | **Ready per your `prospecting-contract.md` handshake.** `GET /capabilities` → `{"research_idempotency":true,"research_writers":["curran@toirinc.com","jared@neptuneops.com"]}`. `/access/{user}.readable` includes `toir-pipeline` for both users (Curran has read+write). Requires `Idempotency-Key` == `report.ingestion_id` (400 `idempotency_key_required` / `idempotency_key_mismatch`). Writes as the requesting user, never substituted. Retries return the stored ack `{"dataset":"toir-pipeline","documents":n,"ingestion_id":key}` without re-ingesting. Empty report → 400 `empty_report`. Verified live, including retry after restart; test data removed. |

## Asks for Curran

1. **Accept the GitHub invitations** from `Toir-FDE-Team` (`globex-clinical-rag`,
   `toir-playbooks`), so the Globex issues get assigned to you. Authorizing
   `github-connect` with your account is optional.
2. **Triage agent endpoint** for the 2 action scenarios. Input:
   `POST {"as_user","request"}`. Response JSON must include `"action": {"tool","repo","assignee","labels"}`.
   Tell us the URL here.
3. **Submit** `brain-api/SUBMISSION.md` before 18:00, with real Respan trace links.
