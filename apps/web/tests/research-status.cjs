/** Render actual components without browser sessions, provider calls, or fixtures in the app. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// Compile the app's TypeScript in memory; no additional test dependency is needed.
for (const extension of [".ts", ".tsx"]) {
  require.extensions[extension] = (module, filename) => {
    const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      fileName: filename,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    });
    module._compile(outputText, filename);
  };
}
const { Chat } = require("../components/chat.tsx");
const { Readiness } = require("../components/settings.tsx");
const session = {
  id: "component-test",
  agent: "TOIR research",
  messages: [{ id: "m1", role: "user", content: "Research this company" }],
};
function renderChat(memoryStatus, capabilities = { ready: false, research_ready: true }) {
  return renderToStaticMarkup(React.createElement(Chat, {
    session,
    state: {
      capabilities,
      proposals: [],
      jobs: [{
        id: "job-test", session_id: session.id, kind: "discovery", status: "completed",
        progress: "Company discovery complete", report: null, propose_crm: false,
        candidates: [], error: null, memory_status: memoryStatus,
      }],
    },
    actions: {},
    onSend: async () => {},
  }));
}

test("completed research does not imply acknowledged ingestion", () => {
  const html = renderChat("pending");
  assert.match(html, /Company discovery complete/);
  assert.match(html, /Research memory: Pending sync/);
  assert.doesNotMatch(html, /Research memory: Synced/);
});

test("memory acknowledgement, blocked delivery and historical unknowns remain distinct", () => {
  for (const [value, label] of [
    ["synced", "Synced"], ["blocked", "Sync blocked"],
    ["not_requested", "Not requested"], [undefined, "Status unavailable"],
  ]) {
    assert.match(renderChat(value), new RegExp(`Research memory: ${label}`));
  }
});

test("explicit research readiness is independent of aggregate automation readiness", () => {
  assert.match(renderChat("pending"), /On-demand research is ready/);
  const unavailable = renderChat("pending", { ready: true, research_ready: false });
  assert.match(unavailable, /On-demand research is waiting on service readiness/);
  assert.doesNotMatch(unavailable, /On-demand research is ready/);
  assert.doesNotMatch(renderChat(undefined, { ready: true }), /On-demand research is ready/);
  const notice = renderToStaticMarkup(React.createElement(Readiness, {
    state: { capabilities: { ready: false, research_ready: true, reasons: ["CRM is unavailable"] } },
  }));
  assert.match(notice, /Continuous dispatch is waiting on readiness/);
  assert.match(notice, /On-demand research is ready/);
  assert.match(notice, /CRM is unavailable/);
});
