# Customer meeting agent

The meeting agent joins a supplied Zoom call as **Toir · Customer notes**, saves a
speaker-attributed transcript, drafts customer notes and product issues, and puts
those issues in **Tasks** for review. A signed-in person must approve the exact task
version before the coordinator creates an issue in
`curranToir/october-7-th-hack-a-ton`. The meeting owner defaults to
`curran@toirinc.com`.

## Implementation plan and boundaries

1. **Capture:** the browser submits a Zoom invitation and the participant-consent
   confirmation. The coordinator persists the request before asking Recall to
   schedule a bot. The bot announces recording/transcription in meeting chat.
2. **Receive:** Recall sends signed live transcript events and lifecycle events
   through the API proxy. The coordinator verifies the raw body before persisting
   the event. A durable inbox processes accepted callbacks asynchronously.
3. **Understand:** on `transcript.done`, the coordinator downloads the final
   transcript and asks the independent meeting worker for structured notes,
   customer needs, next steps, issue drafts and explicitly named research subjects.
   Each issue and mention must have an exact quote from a known transcript segment.
4. **Review and publish:** drafts appear in Tasks. Editing increments the proposal
   version. Approval persists the reviewer, title, body, destination and version;
   the coordinator then publishes through its GitHub outbox. Rejection creates no
   GitHub issue. Neither the model nor a transcript instruction can approve a task.
5. **Research:** company and person mentions become dedicated `/v1/subjects` jobs
   in the existing research worker. Its restricted Oh My Pi harness uses Scalekit
   Exa to find public professional information, verifies sources and returns cited
   identity facts, summaries and gaps. Subject jobs have no prospect-discovery
   geography, headcount or buying filters. The coordinator persists a task ID per
   mention before submitting, polls its status and saves the results within the
   meeting. These are separate from `/research` discovery runs. Research has
   independent status and never blocks issue review. It does not contact the
   subject or modify CRM records. Ambiguous identities remain explicitly unresolved.

The coordinator owns durable meeting, task, event and decision records in the
additive `meeting_records` table of its existing SQLite/Postgres database. The
worker is stateless, receives only Respan credentials, and has no Recall, GitHub,
database or application-session credentials. Only the coordinator can reach its
private port 8000. Browser requests require the existing authenticated workspace;
provider callbacks instead require valid signatures.

## Demo without a live Zoom connection

1. Sign into Toir, open **Meetings**, and click **Run demo**.
2. Inspect the fictional customer's empty CSV export report, transcript quotes,
   customer needs, and the proposed task. The sample also mentions Linear.
3. Open **Tasks**, inspect or edit the GitHub issue draft, and verify its repository.
4. Approve the reviewed version to create a **real** GitHub issue, or reject it.
   Approval is disabled until GitHub credentials are configured. The sample issue
   is clearly labelled `[Demo]`; demo notes use saved fixture content, not a live
   meeting or model call.
5. Inspect **Related research**. When research providers are configured it performs
   a real source-backed research run; otherwise the UI reports blocked setup and
   offers retry after connection.

**Import transcript** exercises real model analysis without Recall. Use one speaker
turn per line, optionally prefixed by `[MM:SS]` or `[HH:MM:SS]`, confirm permission
to save/analyze it, and choose **Save and analyze**. Respan and the meeting worker
must be running for imports and live calls. The saved demo itself works without
Respan or Recall.

## Recall and Zoom setup

Use the [Recall us-west-2 dashboard](https://us-west-2.recall.ai/) with
`curran@toirinc.com`. Account signup, email verification and any required account
terms must be completed before creating credentials. No Recall account activation,
live Zoom capture, webhook subscription or production deployment is implied by
this code change.

Current [Recall Zoom documentation](https://docs.recall.ai/docs/zoom-overview)
supports standard Zoom meetings without a separate Zoom app setup. The host may
need to admit the named bot from the waiting room. The owner email is attribution;
it does not sign the bot into that person's Zoom account. Authentication-required
meetings need Recall's signed-in bot setup, which this implementation does not
configure.

For live capture:

1. Create the region's API key and workspace verification secret (`whsec_...`).
2. Set `MEETING_WEBHOOK_BASE_URL` to the backend's stable, internet-reachable HTTPS
   origin, such as `https://calls.example.com`. Supply an origin without a path,
   query or credentials. A private Tailscale address or SSM localhost tunnel is not
   reachable from Recall. Arrange the public HTTPS route separately; the supplied
   infrastructure changes do not open EC2 inbound ports or publish a new endpoint.
3. Register this dashboard webhook URL:
   `https://calls.example.com/api/meetings/webhooks/recall/dashboard`.
   Subscribe to bot lifecycle events (including joining, waiting room, recording,
   call ended, done and fatal), `transcript.done`, `transcript.failed`, and
   `recording.failed`.
4. Live transcript delivery is configured per bot by the application at
   `https://calls.example.com/api/meetings/webhooks/recall/realtime` for
   `transcript.data`. Preserve the request body and signature headers through the
   public proxy; do not redirect or rewrite the payload.
5. The workspace verification secret validates live callbacks and, by default,
   dashboard callbacks. If the workspace uses legacy dashboard Svix signing, set
   `RECALL_SVIX_WEBHOOK_SECRET` to that endpoint's signing secret as well. Never use
   the API key as a webhook verification secret.
6. In **Meetings → Connections**, confirm the configured owner, analysis worker and
   repository, then add an ordinary Zoom call you control. Confirm permission to
   record, admit the bot, and speak a sample product issue plus a named company.
   End the call, wait for final notes, and review its task before approving.

Recall's [real-time transcription guide](https://docs.recall.ai/docs/bot-real-time-transcription)
documents streaming configuration, the final transcript subscriptions and the
public webhook requirement. This implementation uses Recall's built-in streaming
transcription provider in English.

## Runtime configuration and secret entry

| Variable | Destination | Default or requirement |
| --- | --- | --- |
| `MEETING_AGENT_URL` | Coordinator | `http://meetings:8000` in Kubernetes; `http://127.0.0.1:8004` locally |
| `MEETING_OWNER_EMAIL` | Coordinator | `curran@toirinc.com` |
| `MEETING_GITHUB_REPOSITORY` | Coordinator | `curranToir/october-7-th-hack-a-ton` |
| `RECALL_REGION` | Coordinator | `us-west-2`; must match the credential workspace |
| `RECALL_API_KEY` | Coordinator secret | Required to schedule bots |
| `RECALL_WORKSPACE_VERIFICATION_SECRET` | Coordinator secret | Required `whsec_` signing key |
| `RECALL_SVIX_WEBHOOK_SECRET` | Coordinator secret | Optional legacy dashboard signing key |
| `MEETING_WEBHOOK_BASE_URL` | Coordinator | Public HTTPS origin required for live capture |
| `MEETING_GITHUB_TOKEN` | Coordinator secret | Fine-grained token limited to target repository, Issues read/write |
| `RESPAN_API_KEY` | Meeting worker secret | Required for real transcript analysis |

Use the existing operator secret flow from the repository root:

```sh
.venv/bin/python infrastructure/deployment/scripts/manage.py stack-update
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-set --name meetings-provider
.venv/bin/python infrastructure/deployment/scripts/manage.py secret-sync --restart
```

These are operator actions, not automatically executed setup. `stack-update` adds
one empty retained Secrets Manager entry at `/<stack>/meetings-provider` and exact
host IAM access to it. `secret-set` reads hidden prompts; blank input retains an
existing field or skips an unset optional field, so Recall and GitHub can be
connected in stages. At least one field must be supplied. `--stdin` instead reads
an explicit replacement JSON object from a secure producer's pipe; omitted fields
are removed in that mode. No credential belongs in command arguments, chat, Git,
release files or logged output. Secrets Manager read/write permission is needed
for the incremental prompt flow. The region and webhook origin may live in this
configuration group even though they are not secrets.

`secret-sync` creates a separate optional `meetings-provider` Kubernetes Secret
referenced only by the coordinator and a `meetings-runtime` Secret containing only
Respan. Missing Recall/GitHub fields keep health probes green and expose unavailable
capabilities in the app. They do not prevent unrelated services from starting.
Provider failures do not fall back to fake successful calls or fake issue URLs.

For local development, securely inject coordinator credentials into its environment
and start the worker in a separate terminal after installing locked dependencies:

```sh
.venv/bin/python -m uvicorn agents.meetings.main:app --host 127.0.0.1 --port 8004 --reload
```

If using an ignored `.env.meetings.local`, keep it mode `0600` and load it only into
the coordinator process; it is not automatically read by the deployment CLI.
Supply the Respan key separately to the worker. The rest of the app follows the
existing [local setup](../README.md#local-development).

## Retry, deployment and recovery

- Reusing a meeting request's idempotency key returns the persisted meeting; using
  that key for a different request is rejected. Webhook IDs and payload hashes
  deduplicate redelivery. Live utterances are deduplicated and ordered by timestamp.
- Transient capacity/rate-limit failures have bounded join retries. An unknown bot
  creation result or restart during creation becomes `join_unknown`; check Recall
  before scheduling another bot. Unknown writes are never blindly repeated.
- If a final transcript webhook is missed, open an ended call and choose **Check
  completed transcript**. This performs read-only bot/artifact retrieval and queues
  the saved transcript for analysis without sending another bot. Calls with multiple
  recording artifacts require importing a combined transcript for this recovery path.
- Failed analysis preserves the transcript and can be retried. A restart during
  analysis requeues it. Pending approvals and decisions survive restarts.
- Subject research keeps durable task IDs and saved results per mention. After a
  coordinator restart it resumes polling running jobs. If the research worker has
  lost a running job, the subject shows an interrupted/failed state and can be
  retried from the saved mention; it is not shown as completed research.
- Confirmed GitHub publication failures are retryable. Timeouts, server errors or
  restart during a publication become `publish_unknown`. **Reconcile** performs a
  read-only search for the task's marker among up to 1,000 recent issues. If none is
  found, it leaves the outcome unresolved for manual checking and does not resend.
- Deployment/secret rotation maintenance blocks new meeting actions and waits for
  active meeting operations in the same 660-second drain window used by existing
  workers. `active_meeting_operations` includes remote subject task IDs. During
  maintenance the coordinator only polls already-running subject jobs until they
  finish, with no new subject admission. Webhooks return a retryable 503 during
  maintenance. Keep maintenance short and check Recall delivery history after recovery.
- The sixth application pod has one replica, 100m/500m CPU request/limit,
  256/512 MiB memory, read-only root filesystem and bounded temporary storage.
  Build, deployment, verification, rollback and credential restart lists include
  `meetings`. Rollback removes its deployment/service/policy when the target release
  omits it, while retaining coordinator data.
- Include `meeting_records` in full database backups. Rolling application code back
  does not roll back notes or decisions. There is no automatic meeting retention or
  provider recording deletion workflow in this implementation.

`/health` and `/ready` prove the worker is reachable, not that Recall, Respan or
GitHub credentials work. A successful real meeting, notes extraction, signed
callback delivery and approved issue publication are the final live acceptance
checks after account and public-endpoint setup.
