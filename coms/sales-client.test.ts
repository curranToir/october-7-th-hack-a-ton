import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defaultPreferences,
  evidenceUrl,
  presentWorkspace,
  readPreferences,
  SalesApiError,
  salesClient,
} from "./sales-client.ts";
import type { SalesSnapshot } from "./types.ts";

const record = {
  id: "test-id",
  workspace_id: "toir" as const,
  created_at: "2026-10-07T12:00:00Z",
  updated_at: "2026-10-07T12:00:00Z",
};
function snapshot(): SalesSnapshot {
  return {
    user: { email: "curran@toirinc.com", name: "Curran", workspace_id: "toir" },
    sessions: [
      {
        ...record,
        title: "Research",
        owner: "curran@toirinc.com",
        automation: false,
        deleted: false,
      },
    ],
    messages: [
      {
        ...record,
        id: "msg-1",
        session_id: record.id,
        role: "user",
        content: "Research Acme and add to my CRM",
        task_ids: [],
        job_id: null,
        request_key: "request-1",
      },
    ],
    tasks: [],
    jobs: [],
    automation: {
      ...record,
      enabled: false,
      owner: "curran@toirinc.com",
      request: "Research US companies",
      geography: "US",
      employee_min: 20,
      employee_max: 1000,
      fit_threshold: 70,
      daily_enrichments: 25,
      daily_discoveries: 10,
      timezone: "America/Los_Angeles",
    },
    capabilities: {
      ready: false,
      reasons: ["Brain not ready"],
      postgres: true,
      research: true,
      contacts: true,
      crm: true,
      brain: false,
      auth: true,
    },
  };
}
test("browser preferences cannot turn off approval, history, or override server identity", () => {
  const preferences = readPreferences(
    JSON.stringify({
      theme: "dark",
      requireApproval: false,
      saveHistory: false,
      profile: { email: "attacker@example.com" },
    }),
  );
  assert.equal(preferences.theme, "dark");
  assert.equal(preferences.requireApproval, true);
  assert.equal(preferences.saveHistory, true);
  const state = presentWorkspace(snapshot(), preferences);
  assert.equal(state.profile.email, "curran@toirinc.com");
  assert.equal(
    state.tasks.length,
    0,
    "No fictional demo approvals should appear",
  );
  assert.equal(state.connections[0].connected, true);
});
test("session mapping joins messages in chronology and hides deleted sessions without dropping approval records", () => {
  const data = snapshot();
  data.sessions.push({ ...data.sessions[0], id: "deleted", deleted: true });
  data.messages.unshift({
    ...data.messages[0],
    id: "msg-2",
    created_at: "2026-10-07T13:00:00Z",
    content: "Later message",
  });
  const state = presentWorkspace(data, defaultPreferences);
  assert.equal(state.sessions.length, 1);
  assert.deepEqual(
    state.sessions[0].messages.map((m) => m.id),
    ["msg-1", "msg-2"],
  );
  assert.equal(state.capabilities.ready, false);
  assert.deepEqual(state.capabilities.reasons, ["Brain not ready"]);
});
test("executable evidence URLs are never rendered as links", () => {
  assert.equal(evidenceUrl("javascript:alert(1)"), undefined);
  assert.equal(evidenceUrl("data:text/html,hello"), undefined);
  assert.equal(
    evidenceUrl("https://www.linkedin.com/in/example"),
    "https://www.linkedin.com/in/example",
  );
  assert.deepEqual(readPreferences("invalid"), defaultPreferences);
  assert.deepEqual(readPreferences("null"), defaultPreferences);
});
test("versioned edits and decisions use same-origin credentials and idempotent request keys", async (t) => {
  const calls: { path: string; init: RequestInit }[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (path: string, init: RequestInit) => {
      calls.push({ path, init });
      return new Response(JSON.stringify({ id: "p1", version: 3 }), {
        status: 200,
      });
    },
  );
  await salesClient.editProposal("p1", 2, ["contact-1"], ["op-2"], {
    "op-1": ["description"],
  });
  await salesClient.decide("p1", 3, "approved", "decision-idempotency-key");
  assert.equal(calls[0].path, "/api/sales/tasks/p1");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
    version: 2,
    excluded_contact_ids: ["contact-1"],
    excluded_operation_ids: ["op-2"],
    excluded_fields: { "op-1": ["description"] },
  });
  assert.equal(calls[1].init.credentials, "same-origin");
  assert.equal(calls[1].init.cache, "no-store");
  assert.equal(
    (calls[1].init.headers as Record<string, string>)["Idempotency-Key"],
    "decision-idempotency-key",
  );
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), {
    version: 3,
    decision: "approved",
  });
});
test("401 and version conflicts fail without optimistic success", async (t) => {
  let status = 401;
  t.mock.method(
    globalThis,
    "fetch",
    async () => new Response("{}", { status }),
  );
  await assert.rejects(
    salesClient.workspace(),
    (error: unknown) => error instanceof SalesApiError && error.status === 401,
  );
  status = 409;
  await assert.rejects(
    salesClient.decide("p1", 1, "approved", "key"),
    (error: unknown) =>
      error instanceof SalesApiError &&
      error.message.includes("latest version"),
  );
});
test("chat sends only content and a stable request key, never a browser-supplied actor", async (t) => {
  let captured: RequestInit = {};
  t.mock.method(
    globalThis,
    "fetch",
    async (_path: string, init: RequestInit) => {
      captured = init;
      return new Response("{}", { status: 202 });
    },
  );
  await salesClient.send(
    "session/id",
    "Research example.com",
    "same-key-on-retry",
  );
  assert.deepEqual(JSON.parse(String(captured.body)), {
    content: "Research example.com",
  });
  assert.equal(
    (captured.headers as Record<string, string>)["Idempotency-Key"],
    "same-key-on-retry",
  );
});
