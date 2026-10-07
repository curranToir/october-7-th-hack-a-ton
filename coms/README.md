# Frontend data and database handoff

`/coms` is the root-level boundary for the Company Brain frontend's user and workspace data. It is deliberately a browser-local demo until the backend engineer connects authentication, the database, and agent execution. There are no network calls, OAuth flows, CRM writes, paid actions, or real agent replies in this implementation.

## Files

| File                | Responsibility                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------------- |
| `types.ts`          | User profile, preferences, workspace, sessions/messages, approval tasks, and connection contracts |
| `seed.ts`           | Fictional example sessions, agent findings, and connections                                       |
| `workspace.ts`      | Session creation, local message response, and idempotent approval/denial transitions              |
| `storage.ts`        | The only browser storage access, schema validation, persistence, and JSON export                  |
| `workspace.test.ts` | Decision integrity, reload persistence, privacy, and corrupt/unavailable-storage recovery         |

The UI subscribes through `apps/web/components/use-workspace.ts`. Components use `update(current => next)`; they never access localStorage directly. The browser key is `company-brain.workspace.v1`. This is one demo workspace per browser/origin, not per authenticated user. Concurrent tabs are not synchronized. Do not use the snapshot adapter as a multi-user backend write API.

## What works now

- Light, dark, and live OS-following system themes; compact sidebar and time-zone preferences.
- Create/search/rename/delete sessions, local chat messages, and explicit demo responses.
- Pending approval queue with evidence, proposed steps and scope; approve/deny, automatic or manual advancement, and decision history. Decisions never execute the proposed action.
- Profile/workspace editing; example connection state; notification and approval preferences.
- Data export, clear conversations, and reset demo with confirmation.
- Disabling saved history immediately removes conversations from the stored snapshot; in-memory chats remain until reload. Profile, preferences and task decisions are retained. Clearing conversations keeps approval records, and links to deleted sessions are disabled.
- Storage failures retain in-memory changes and display a warning.

Notifications, account authentication, invitations, billing, integrations and real agent replies are explicitly unavailable/demo-only in the UI. Approval and notification toggles are stored preferences; they do not start automation or dispatch notifications. The language is English until translations are supplied.

## Backend integration work

1. Add authenticated user/workspace IDs and authorization on every read and write. Replace the `workspaceClient` methods with an asynchronous API adapter and update the hook to handle loading/errors; remove demo seeding for authenticated workspaces.
2. Persist `profile`, `preferences`, and `workspace` with scoped PATCH operations. Preferences are a server-backed per-user record; approval policy must also be enforced on the server.
3. Add paginated session and message APIs, rename/delete endpoints, streamed agent replies, and cancellation/error states. Replace the explicit mock response in `sendMessage`.
4. Read pending tasks from agent proposals. Decide each task using an atomic, idempotent server endpoint with actor, timestamp, decision and proposal version. Enforce `pending -> approved|denied` once; add separate execution states (`queued`, `running`, `succeeded`, `failed`) rather than treating an approval as a completed CRM action. Revalidate the exact proposed scope before execution.
5. Replace example evidence text with authorized source records and source URLs. Keep deletion/retention behavior consistent for evidence and conversations.
6. Replace connection simulation with OAuth handled by the server. Store tokens only server-side; never in these browser contracts. Agents must check connection authorization before executing.
7. Implement notifications, team membership/invitations, billing, exports and deletion with backend services. Honor privacy settings and define retention behavior before launch.
8. Migrate any desired demo data explicitly; never silently turn example approvals into real actions. Add cross-tab/live server updates and conflict handling.

Suggested resource boundaries: `/me`, `/me/preferences`, `/workspaces/:id`, `/sessions`, `/sessions/:id/messages`, `/tasks`, `/tasks/:id/decisions`, `/connections`. These are a handoff proposal, not routes already implemented.

## Validation

From the repository root (Node 24):

```sh
npm run test:web:coms
npm run typecheck:web
npm run build:web
npm run dev:web
```

The web Dockerfile copies `/coms` into the build context. The frontend adds no runtime dependencies. Hash routes preserve the selected view on reload, e.g. `/#tasks` and `/#settings/general`.
