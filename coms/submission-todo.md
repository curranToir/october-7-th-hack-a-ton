# Submission: what Curran needs to add (Jared, 17:35 PT)

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
4. **Respan trace links.** Replace the placeholder links in "Baseline Run", "Improved Run" and "Links". In the Respan platform go to Observability → Logs, grouping **Traces**, all environments, today. Filter **metadata `run_label` = `before`** (then `after`); the traces appear under the name `workflow`, not `eval.scenario`. Example traces to search by ID:
   - before: `588c3772bf3181b1299986a7150f0a5c`
   - after: `81a60004f0e6f9d32eda2024d4b53a66`

   Use the Share button for public links. Also add one trace of your coordinator/agent run (e.g. the research run sharing trace `5902a7809174a2f5b7bf2967156717ca`).
5. **Action scenarios s12/s13.** They're unscored (skipped) because there's no triage endpoint. Either add the endpoint and we run `eval/run.py --label after --agent-url <url>`, or state in "Evaluation Evidence" that actions are demonstrated live rather than scored.
6. **Architecture.** Add your half below the brain: browser → API → coordinator (LangGraph, Postgres run store on the Spark via `DATABASE_URL`) → workers, and EC2 on the tailnet → brain-api.
7. **Demo (step 5 of the pitch outline).** Describe the agent task you'll show live, e.g. triage: recall the Acme blocker → open the issue in `Toir-FDE-Team/acme-agent-rollout` as the user → the trace in Respan.
8. **Access story (optional but stronger).** Accept the GitHub invites to `globex-clinical-rag` and `toir-playbooks` so the Scalekit-layer difference (repo membership) is real for you.

## Optional

- `cognee-feedback.md`: the organizers ask teams to attach it. We hit: SQLite 3.45.1 segfault on Cognee's nested user lookup; `.env` reloaded with override=True undoing ACL; kept-alive Ladybug engines locking shared datasets across users; the `cognee-community-observability-respan` adapter missing from PyPI; docs still recommending the removed `temporal_cognify`. Say if you want Jared to write it.
