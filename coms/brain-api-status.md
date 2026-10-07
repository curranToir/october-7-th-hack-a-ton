# brain-api: current status (Jared, 16:55 PT)

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
- **Before pull running** (Slack + GitHub), followed by eval `before`, then the
  HubSpot pull and eval `after`. Scores will be posted here.

## Decisions since the last table

| Topic | Decision |
|---|---|
| Slack identity | Jared's `slack` connection is the only one used, for seeding and pulls of all 7 channels. Your Slack account was reset by mistake on our side; since you aren't re-authorizing, it isn't needed. Consequence: no Slack-layer access difference. |
| Access story | Enforced by Cognee dataset ACLs (per-client eng/commercial datasets) plus GitHub repo access (you are a collaborator only on `globex-clinical-rag` and `toir-playbooks`). Demo: you ask about Acme → `withheld` → Jared runs `/grant acme-eng` → answered, while `acme-commercial` stays withheld. |
| HubSpot | Pulled with your `hubspot` connection (ACTIVE). Jared doesn't need a HubSpot invite. |
| GitHub | Connection `github-connect`. Pulls fall back to Jared's account until yours is ACTIVE. |
| `/remember/research` | Accepts any known `as_user`. Documents always land in `toir-pipeline`, owned by Jared, one document per lead with its cited source text. Idempotency: the same `run_id` + report produce byte-identical documents, which Cognee's content-hash incremental loading should skip (not yet exercised). Read access for a second identity is one call: `POST /grant {"owner":"jared@neptuneops.com","grantee":"<email>","dataset":"toir-pipeline"}`. |

## Asks for Curran

1. **Accept the GitHub invitations** from `Toir-FDE-Team` (`globex-clinical-rag`,
   `toir-playbooks`), so the Globex issues get assigned to you. Authorizing
   `github-connect` with your account is optional.
2. **Triage agent endpoint** for the 2 action scenarios. Input:
   `POST {"as_user","request"}`. Response JSON must include `"action": {"tool","repo","assignee","labels"}`.
   Tell us the URL here.
3. `brain-api-response.md` links `prospecting-implementation.md`, but that file
   is not in the repo. If it holds requirements for us (e.g. "both sales
   identities"), please push it and name the second identity's email.
