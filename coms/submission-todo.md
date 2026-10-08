# Submission: what Curran needs to add (Jared, 17:35 PT)

## Response and current disposition

Curran authorized completion of these items and publication to the project
repository's `main`. The expanded [submission](../brain-api/SUBMISSION.md) now
includes the full application architecture, agent responsibilities, code links,
identity/approval boundaries, current model and research limits, recorded rollout
results, and the public Respan baseline/improved/research links.

- **Team confirmed:** Toir Inc; Curran McLaughlin and Jared Lyon.
- **Scalekit action/demo choice:** use the existing HubSpot proposal workflow.
  The external connector is Toir's shared `curran@toirinc.com` connection;
  requester and approver are independently audited. It is not an arbitrary
  per-approver connector. Every mutation requires the persisted exact-version
  approval. No GitHub issue/Slack-post execution is implemented by the app.
- **Action scenarios:** `s12`/`s13` remain explicitly unscored because their
  GitHub triage endpoint does not exist. The HubSpot demo is described separately
  and is not substituted for those expected GitHub actions. Chrome blocked the
  fresh sign-in callback, after which Curran explicitly limited this task to
  updating the main GitHub repository. No live mutation is claimed and no
  approval or write was bypassed to create evidence.
- **Trace links:** all three public URLs below returned HTTP 200 without auth;
  the research trace is linked from the agent section.
- **Architecture/model details:** complete, including five K3s services, Spark
  Postgres and Cognee, local embeddings, durable memory receipts, GPT-5 mini,
  60 searches / 200 page slots / 60 turns, the ten-minute deadline, JSON report
  enforcement and isolated candidate validation. The latest handoff's completed
  automatic discovery is included without implying contact/memory completion.
  Spark's running model/receipt upgrade remains separate from committed code.
- **GitHub invitations:** accepted both `globex-clinical-rag` and `toir-playbooks`;
  API read/write access verified as `curranToir`. The Acme repository remains
  inaccessible (404). This does not itself authorize a Scalekit GitHub connection.
- **Cognee feedback:** [technical feedback](../brain-api/cognee-feedback.md)
  prepared from recorded issues and implementation evidence. It includes no
  private sentiment, source data, credentials or invented incident timings.

The original request is retained below as historical context. Current readiness
is documented in the submission and [runtime handoff](spark-runtime-integration.md),
including verified HubSpot grants and enabled continuous prospecting.

## Original request

Draft: [`brain-api/SUBMISSION.md`](../brain-api/SUBMISSION.md), built on the event template.
The Spark side is complete: pull, Cognee memory, access story, eval
(4/11 → 11/11) and reproduction. Deadline **18:00 PT**: open a PR adding
`cognee-companybrain-scalekit-respan-hackathon-2026-10-07/submissions/<team-name>/SUBMISSION.md`
to `topoteretes/cognee-hackathons`, or hand the repo link to an organizer.

## Add these to SUBMISSION.md

1. **Team.** Confirm the team name and participants (the draft says "Toir Inc", Curran McLaughlin, Jared Lyon).
2. **Act + Evaluate → "Agent(s) and the task each performs".** Replace the one-line placeholder with your side:
   - which agents exist: coordinator, research worker, contacts worker, triage/brief, and what each does;
   - code entry points (`apps/orchestrator/…`, `agents/research/…`, `agents/contacts/…`);
   - how they call the brain: `/recall` with `as_user`, and `/remember/research` with `Idempotency-Key`;
   - which LLM models go through the Respan gateway on your side (your `.env.example` has `RESPAN_MODEL=gpt-5.4`).
3. **Write-back actions through Scalekit, as the acting user.** List every write the agents take (approved HubSpot CRM writes, GitHub issue creation, Slack posts), and note that each one requires an approved, persisted proposal. The rubric scores "takes a useful action through Scalekit", and only your side does that.
4. **Respan trace links: DONE by Jared.** Made public through Respan's API and added to SUBMISSION.md (Baseline, Improved, Links). These open without login:
   - eval before (s01): https://api.respan.ai/api/3f37a437-bd40-4bcb-9e37-f5f2686d5622/traces/042daa65fe1d2060f8aacce699f5b112/
   - eval after (s01): https://api.respan.ai/api/3f37a437-bd40-4bcb-9e37-f5f2686d5622/traces/d68d3cbef3f7ae98b9dc3b0a3ac0e907/
   - your research run (23 spans, 5 LLM calls): https://api.respan.ai/api/3f37a437-bd40-4bcb-9e37-f5f2686d5622/traces/5902a7809174a2f5b7bf2967156717ca/. Paste it into your agent section.

   These are Respan's public-trace API URLs (JSON). If you prefer the dashboard view, use Logs → trace → Copy trace URL.
5. **Action scenarios s12/s13.** They're unscored (skipped) because there's no triage endpoint. Either add the endpoint and we run `eval/run.py --label after --agent-url <url>`, or state in "Evaluation Evidence" that actions are demonstrated live rather than scored.
6. **Architecture.** Add your half below the brain: browser → API → coordinator (LangGraph, Postgres run store on the Spark via `DATABASE_URL`) → workers, and EC2 on the tailnet → brain-api.
7. **Demo (step 5 of the pitch outline).** Describe the agent task you'll show live, e.g. triage: recall the Acme blocker → open the issue in `Toir-FDE-Team/acme-agent-rollout` as the user → the trace in Respan.
8. **Access story (optional but stronger).** Accept the GitHub invites to `globex-clinical-rag` and `toir-playbooks` so the Scalekit-layer difference (repo membership) is real for you.

## Optional

- `cognee-feedback.md`: the organizers ask teams to attach it. We hit: SQLite 3.45.1 segfault on Cognee's nested user lookup; `.env` reloaded with override=True undoing ACL; kept-alive Ladybug engines locking shared datasets across users; the `cognee-community-observability-respan` adapter missing from PyPI; docs still recommending the removed `temporal_cognify`. Say if you want Jared to write it.
