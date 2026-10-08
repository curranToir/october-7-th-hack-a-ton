# Google sign-in and user/session persistence

October 7, 2026. Branch `codex/google-sso`, based on `origin/main` at the user's
request because this repository has no staging branch.

## Login and identity

The browser starts at `GET /api/auth/login`. The coordinator redirects to the
existing Scalekit environment with `provider=google`, `openid profile email`,
PKCE S256, nonce, and a browser-bound single-use state. The callback verifies
RS256 signatures, issuer, audience, expiry, nonce and token hashes before using
any identity claims. No Google API/Drive/Gmail scopes or refresh tokens are requested.

Only verified `curran@toirinc.com` and `jared@neptuneops.com` emails are admitted
by default. `SALES_MEMBER_EMAILS` is the server allowlist. Membership status/role,
issuer, subject and user ID are checked again on every authenticated request.
Changing an identity's email, issuer or subject does not automatically reassign
workspace access. An administrator must reconcile deliberate identity changes.

Scalekit remains the OIDC issuer; Google is the upstream social login provider.
A Scalekit subject is not a Google subject. Protocol reference:
[Scalekit Google routing](https://docs.scalekit.com/authenticate/fsa/implement-login/).

## Database contract

All records use the existing `SalesStore` and `sales_records` table, in the same
transaction/connection boundary as [the database handoff](database-handoff.md).
`DATABASE_URL` selects Jared's Postgres adapter; SQLite remains local-only.
The API, browser and workers do not receive database credentials. No destructive
DDL, replacement tables, research/checkpoint migration, or Spark deployment is
part of this change.

| Kind / key | Persisted data |
|---|---|
| `member` / normalized email | Stable `user_id` UUID, verified email, name, active sales role, workspace, issuer, subject, created/last-login times, `memory_user_id` |
| `identity` / `oidc:` + SHA-256(issuer + newline + subject) | Issuer/subject to user UUID and email binding; separate key namespace from CRM identities |
| `auth_session` / SHA-256(opaque cookie) | Separate public session UUID, user UUID/email, issuer/subject, created/last-seen/expiry times, bounded informational browser user-agent |
| `auth_flow` / SHA-256(state) | Flow-cookie hash, PKCE verifier, nonce and ten-minute expiry |

Provider tokens, raw session cookies and raw state are never stored. The flow's
PKCE verifier is temporary and deleted when its matching callback is consumed.
Expired flows are swept on login; expired sessions on successful login/listing.
These opportunistic sweeps use the existing serialized database transaction.
Auth session activity is recorded at most once per minute, without extending its
absolute eight-hour lifetime. The public session UUID is not a bearer credential.
Browser user-agent is untrusted display metadata; no IP or location is retained.

A successful login replaces that browser's previous local session. Other browsers
retain independent sessions. Revoking sessions preserves users, conversations,
proposals, approvals, CRM journals and memory outbox work. Old sessions that lack
issuer/user binding require one fresh sign-in after rollout; existing matching
members gain their stable user UUID on that sign-in.

## API and profile settings

- `GET /api/me`: public user ID, email, name, workspace, role and verified flag.
- `GET /api/auth/sessions`: only the current user's sign-ins; public ID,
  timestamps, browser label and `current` flag. No hashes, provider tokens or
  subjects are exposed.
- `DELETE /api/auth/sessions/{uuid}`: revoke an owned sign-in; another user's or
  unknown UUID returns 404. Revoking the current sign-in also clears the cookie.
- `DELETE /api/auth/sessions`: revoke all of the current user's sign-ins.
- `POST /api/auth/logout`: revoke this browser's session and clear its cookie,
  including when it is already expired or revoked.

All mutations require the exact configured browser Origin. Session management is
visible under Settings → Profile. Responses are not cached; cookies are HttpOnly,
SameSite=Lax and Secure outside loopback HTTP development. Callback responses also
set `Referrer-Policy: no-referrer`, clear the flow cookie on failure and avoid
reflecting provider descriptions. Uvicorn/httpx callback access logs redact query
parameters in both API and coordinator; ingress access logs must do the same if
enabled. Local sign-out does not sign the user out of Google or terminate their
Scalekit SSO session.

## Cognee / Brain identity

The existing Brain API contract expects the verified email in `as_user` and the
conversation UUID in `session_id`. `memory_user_id` records that email mapping;
provider subjects and browser login IDs are not substituted. Session owners,
requesters and approvers keep their existing email keys, preserving the history.

Sales conversations are shared. Recall still intersects both members' grants for
`toir-pipeline` and `toir-firm` before publishing memory into a conversation.
Signing in creates no Cognee datasets or grants, and never borrows Jared's identity
or the shared HubSpot connector owner's identity. See
[Brain status](brain-api-status.md) for the Spark owner's current ACL/service work.
Adding an email to `SALES_MEMBER_EMAILS` alone does not authorize it for Brain;
`BrainClient` intentionally retains its two-identity allowlist.

## Provider and release setup

Inspected in Chrome's `curran@toirinc.com` profile: Toirinc Dev already has Google
enabled using Scalekit's development credentials. The callback allowlist was
empty on inspection. After user approval, registered both localhost callbacks
and `https://toir-hackathon.taild4c940.ts.net/api/auth/callback`; all three were
visibly present in Scalekit. The tailnet HTTPS endpoint returned HTTP 200 with
certificate verification enabled.
Production Google branding/custom credentials remain a separate provider setup;
the existing development connection needs no new Google Cloud client.

Set `SALES_PUBLIC_URL` to the exact browser origin, then register
`<origin>/api/auth/callback` under Scalekit → Authentication → Redirect URLs.
Default local origin is `http://localhost:8080`; isolated acceptance uses
`http://localhost:8081` because 8080 is already occupied by an existing tunnel.
No wildcard callbacks or user-supplied redirect targets are accepted. Non-loopback
origins require HTTPS; do not put the current HTTP tailnet address into this
setting. The Kubernetes manifest now sets the verified HTTPS tailnet origin for rollout.

Reuse the existing Scalekit credentials in `/company-brain-hackathon/scalekit`.
No new secret group is needed. Do not copy credentials into source or browser
storage. Back up the full Postgres database under the existing owner procedure
before rollout. This branch has not changed or deployed the remote demo.

## Validation

- Full Python suite against SQLite and disposable local Postgres: 374 passed,
  zero skipped, including callback-log regression tests.
- Browser contract tests: 14 passed. Next production build passed on Node 24.
- API and coordinator Docker images built; imports and auth-log setup passed in
  network-isolated containers. Deployment contract checks: 34 passed.
- Callback registration: verified for localhost:8080, localhost:8081 and HTTPS tailnet.
- Live Google sign-in with `curran@toirinc.com`: passed on the isolated local
  server with the branch’s production frontend build. Verified profile, active
  sign-in, reload persistence and sign-out-all returning to the login screen.
- Tailnet `/api/auth/login` still returned 404 on the old deployment. This branch
  and the preceding sales-workflow implementation must be released before
  tailnet login works. No remote rollout or production storage cutover was done.
- No live Spark database writes, Cognee grants or CRM changes were performed.
