import { test, expect } from "bun:test";
import { createApp } from "../src/server/app";
import { Budget } from "../../research/src/research/budget";
import { Sources } from "../../research/src/research/sources";
import { createRestrictedSession } from "../../research/src/harness/omp";
import { CONTACT_SYSTEM, contactHarness } from "../src/harness/omp";
import { finalizeReport } from "../src/research/evidence";
import { EMPTY_REPORT, validTask, type Company, type Contact, type ContactTask } from "../src/research/contracts";

const quote = "Example appointed Jane Smith Chief Technology Officer at Example. Contact Jane Smith at jane@example.com or +1 212 555 1234. https://www.linkedin.com/in/janesmith";
function fixture() {
  const sources = new Sources();
  const source = sources.add({ url: "https://example.com/team", text: quote })!;
  const citation = { source_id: source.id, quote };
  const company: Company = { name: "Example", domain: "example.com", description: "Software company", country: "US",
    employee_count: null, citations: [citation], fit_score: 75, sales_angle: "Hypothesis: explore automation." };
  const contact: Contact = { id: "model-must-not-own-this", name: "Jane Smith", title: "Chief Technology Officer", company_domain: "example.com",
    buying_relevance: "Hypothesis: owns technology evaluation", linkedin_url: "https://www.linkedin.com/in/janesmith", email: "jane@example.com", phone: "+1 212 555 1234",
    citations: [citation], linkedin_citations: [citation], email_citations: [citation], phone_citations: [citation] };
  const task: ContactTask = { version: 1, task_id: "task-1", run_id: "run-1", mode: "enrich", query: "Find Example technology decision makers",
    company, deadline_at: new Date(Date.now() + 600_000).toISOString(), prior_sources: sources.list(), usage: {} };
  return { sources, source, company, contact, task, report: { ...structuredClone(EMPTY_REPORT), company, contacts: [contact] } };
}
const post = (task: unknown) => new Request("http://contacts/v1/tasks", { method: "POST", body: JSON.stringify(task) });
const get = (id = "task-1") => new Request(`http://contacts/v1/tasks/${id}`);
const waitUntil = async (work: () => Promise<boolean>) => {
  for (let i = 0; i < 200; i++) { if (await work()) return; await Bun.sleep(5); }
  throw new Error("Timed out");
};

test("shared contract rejects invalid IDs, unknown fields, bad usage and enrich without a company", () => {
  const { task } = fixture();
  expect(validTask(task)).toBe(true);
  expect(validTask({ ...task, task_id: "../secret" })).toBe(false);
  expect(validTask({ ...task, usage: { pages: -1 } })).toBe(false);
  expect(validTask({ ...task, company: null })).toBe(false);
  expect(validTask({ ...task, deadline_at: "invalid" })).toBe(false);
  expect(validTask({ ...task, tool: "crm-write" })).toBe(false);
  expect(validTask({ ...task, mode: "resolve", company: null })).toBe(true);
});
test("readiness reports missing credential names and health remains available", async () => {
  const app = createApp({ missing: () => ["RESPAN_API_KEY"], harness: async () => EMPTY_REPORT });
  expect((await app.fetch(new Request("http://c/health"))).status).toBe(200);
  expect((await app.fetch(new Request("http://c/ready"))).status).toBe(503);
  const caps = await (await app.fetch(new Request("http://c/v1/capabilities"))).json();
  expect(caps.limits).toMatchObject({ searches: 60, pages: 200, model_turns: 60, deadline_seconds: 600, active_tasks: 1 });
  expect(caps.missing_credentials).toEqual(["RESPAN_API_KEY"]);
  expect(caps.limits.contacts).toBe(5);
  expect((await app.fetch(post(fixture().task))).status).toBe(503);
});
test("canonical idempotency and a single active worker slot", async () => {
  const { task, report } = fixture();
  let finish!: () => void;
  const app = createApp({ harness: async () => { await new Promise<void>((resolve) => { finish = resolve; }); return report; } });
  expect((await app.fetch(post(task))).status).toBe(202);
  expect((await app.fetch(post(Object.fromEntries(Object.entries(task).reverse())))).status).toBe(200);
  expect((await app.fetch(post({ ...task, query: "different" }))).status).toBe(409);
  expect((await app.fetch(post({ ...task, task_id: "task-2" }))).status).toBe(409);
  finish();
  await waitUntil(async () => (await (await app.fetch(get())).json()).status === "completed");
  expect((await (await app.fetch(get())).json()).report.contacts).toHaveLength(1);
  await app.shutdown();
});
test("cancellation, absolute deadlines and shutdown settle stalled provider tasks with saved evidence", async () => {
  for (const reason of ["cancel", "deadline", "shutdown"]) {
    const { task } = fixture();
    task.deadline_at = new Date(Date.now() + (reason === "deadline" ? 30 : 600_000)).toISOString();
    const app = createApp({ harness: async () => new Promise(() => {}) });
    await app.fetch(post(task));
    if (reason === "cancel") await app.fetch(new Request("http://c/v1/tasks/task-1/cancellation", { method: "POST" }));
    if (reason === "shutdown") await app.shutdown();
    await waitUntil(async () => (await (await app.fetch(get())).json()).status !== "running");
    const status = await (await app.fetch(get())).json();
    expect(status.status).toBe(reason === "deadline" ? "failed" : "cancelled");
    expect(status.sources).toHaveLength(1);
    if (reason === "deadline") expect(status.error).toContain("deadline");
    await app.shutdown();
  }
  const budget = new Budget({}, new Date(Date.now() + 3_600_000).toISOString(), new AbortController().signal);
  expect(budget.deadline - Date.now()).toBeLessThanOrEqual(600_000);
});
test("retention is bounded, lost tasks are explicit and failures omit secrets", async () => {
  const { task } = fixture();
  const app = createApp({ retention: 1, harness: async () => { throw Error("private-provider-token"); } });
  for (const task_id of ["task-1", "task-2"]) {
    await app.fetch(post({ ...task, task_id }));
    await waitUntil(async () => (await (await app.fetch(get(task_id))).json()).status === "failed");
  }
  expect((await app.fetch(get())).status).toBe(404);
  expect(await (await app.fetch(get("task-2"))).text()).not.toContain("private-provider-token");
  await app.shutdown();
  expect((await app.fetch(post({ ...task, task_id: "task-3" }))).status).toBe(503);
});
test("enrichment verifies field-specific evidence and replaces model IDs and source text", () => {
  const { task, sources, report, source } = fixture();
  const result = finalizeReport({ ...report, sources: [{ ...source, text: "Forged model evidence" }] }, task, sources);
  expect(result.contacts).toHaveLength(1);
  expect(result.contacts[0].email).toBe("jane@example.com");
  expect(result.contacts[0].linkedin_url).toBe("https://www.linkedin.com/in/janesmith");
  expect(result.contacts[0].id).not.toBe("model-must-not-own-this");
  expect(result.sources[0].text).toBe(quote);
  const forged = structuredClone(report);
  forged.contacts[0].email = "guessed@example.com";
  forged.contacts[0].linkedin_url = "https://www.linkedin.com/in/someone-else";
  forged.contacts[0].phone = "+1 999 888 7777";
  const filtered = finalizeReport(forged, task, sources);
  expect(filtered.contacts[0].email).toBeNull();
  expect(filtered.contacts[0].linkedin_url).toBeNull();
  expect(filtered.contacts[0].phone).toBeNull();
});
test("missing email remains missing and a direct verified LinkedIn source is accepted", () => {
  const { task, sources, report, contact } = fixture();
  const profile = sources.add({ url: "https://linkedin.com/in/janesmith/", text: "Jane Smith is Chief Technology Officer at Example." })!;
  report.contacts[0] = { ...contact, email: null, email_citations: [], linkedin_citations: [{ source_id: profile.id, quote: profile.text }] };
  const result = finalizeReport(report, task, sources);
  expect(result.contacts[0].email).toBeNull();
  expect(result.contacts[0].linkedin_url).toBe("https://www.linkedin.com/in/janesmith");
  expect(result.gaps.join(" ")).toContain("Missing verified business email");
});
test("rejects mismatched company, unsupported title, invented quotes and duplicate people", () => {
  const { task, sources, report, contact } = fixture();
  for (const change of [{ company_domain: "different.com" }, { title: "CEO" }, { citations: [{ source_id: "fake", quote: "Jane Smith is the CEO of Example" }] }]) {
    expect(finalizeReport({ ...report, contacts: [{ ...contact, ...change }] }, task, sources).contacts).toHaveLength(0);
  }
  expect(finalizeReport({ ...report, contacts: [contact, contact] }, task, sources).contacts).toHaveLength(1);
  expect(() => finalizeReport({ ...report, contacts: Array(6).fill(contact) }, task, sources)).toThrow("contract");
  expect(() => finalizeReport(report, { ...task, prior_sources: [] }, new Sources())).toThrow("verified identity");
});
test("resolve drops contacts and makes multiple supported identities explicit", () => {
  const { task, sources, report, company } = fixture();
  const second = sources.add({ url: "https://example.net/about", text: "Example is a manufacturing company in California." })!;
  const candidate = { ...company, domain: "example.net", citations: [{ source_id: second.id, quote: second.text }] };
  const result = finalizeReport({ ...report, candidates: [candidate] }, { ...task, mode: "resolve", company: null }, sources);
  expect(result.company).toBeNull();
  expect(result.candidates).toHaveLength(2);
  expect(result.contacts).toHaveLength(0);
});
test("new official evidence can confirm discovery identity but cannot change the selected company", () => {
  const { task, sources, report, company } = fixture();
  const announcement = sources.add({ url: "https://news.example.net/article", text: "Example has announced a new technology program." })!;
  const selected = { ...company, citations: [{ source_id: announcement.id, quote: announcement.text }] };
  const result = finalizeReport(report, { ...task, company: selected }, sources);
  expect(result.company?.citations).toEqual(company.citations);
  expect(result.company?.fit_score).toBe(selected.fit_score);
  expect(() => finalizeReport({ ...report, company: { ...company, name: "Different" } }, { ...task, company: selected }, sources)).toThrow("verified identity");
});
test("former roles and another person's email on the same source cannot establish contact fields", () => {
  const { task, sources, report, contact } = fixture();
  const former = sources.add({ url: "https://example.com/archive", text: "Jane Smith was formerly the former Chief Technology Officer at Example." })!;
  expect(finalizeReport({ ...report, contacts: [{ ...contact, citations: [{ source_id: former.id, quote: former.text }] }] }, task, sources).contacts).toHaveLength(0);
  const roster = sources.add({ url: "https://example.com/contact", text: "Jane Smith is our CTO. John Jones can be reached at john@example.com." })!;
  const result = finalizeReport({ ...report, contacts: [{ ...contact, email: "john@example.com", email_citations: [{ source_id: roster.id, quote: "John Jones can be reached at john@example.com." }] }] }, task, sources);
  expect(result.contacts[0].email).toBeNull();
});
test("actual OMP SDK constructs contact-only prompt with exactly the three Exa tools", async () => {
  const { task, sources } = fixture();
  const signal = new AbortController().signal;
  const runtime = await createRestrictedSession(task, { budget: new Budget({}, task.deadline_at, signal), sources, signal, progress: () => {} },
    { execute: async () => { throw Error("No paid tool calls allowed"); } }, "fake-test-key", "gpt-5-mini",
    { systemPrompt: CONTACT_SYSTEM, agentName: "toir-contacts" });
  try {
    expect(runtime.session.agent.state.model.provider).toBe("respan");
    expect(runtime.session.agent.state.tools.map((tool) => tool.name).sort()).toEqual(["exa_crawl", "exa_find_similar", "exa_search"]);
    expect(String(runtime.session.agent.state.systemPrompt)).toContain("contact-research execution agent");
  } finally { await runtime.dispose(); }
}, 30_000);
test("actual OMP loop uses the restricted Respan gateway and shared budget with a fake response", async () => {
  const { task, sources, report } = fixture();
  const original = globalThis.fetch;
  let calls = 0;
  const payloads: unknown[] = [];
  const fake = async (input: any, init: any) => {
    expect(String(input instanceof Request ? input.url : input)).toBe("https://api.respan.ai/api/chat/completions");
    const body = JSON.parse(init.body);
    payloads.push(body);
    expect(body.tools.map((tool: any) => tool.function.name).sort()).toEqual(["exa_crawl", "exa_find_similar", "exa_search"]);
    expect(JSON.stringify(body.messages)).toContain("contact-research execution agent");
    const first = calls++ === 0;
    const delta = first ? { role: "assistant", tool_calls: [{ index: 0, id: "call_exa", type: "function", function: { name: "exa_search", arguments: JSON.stringify({ query: "Example Jane Smith current CTO" }) } }] }
      : { role: "assistant", content: JSON.stringify(report) };
    const chunk = (choices: unknown[], extra = {}) => ({ id: "test", object: "chat.completion.chunk", created: 1, model: "gpt-5-mini", choices, ...extra });
    return new Response([
      chunk([{ index: 0, delta, finish_reason: null }]),
      chunk([{ index: 0, delta: {}, finish_reason: first ? "tool_calls" : "stop" }]),
      chunk([], { usage: { prompt_tokens: 100, completion_tokens: 30, total_tokens: 130 } }),
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n\n", { headers: { "Content-Type": "text/event-stream" } });
  };
  globalThis.fetch = Object.assign(fake, { preconnect: original.preconnect }) as typeof fetch;
  const signal = new AbortController().signal, budget = new Budget({}, task.deadline_at, signal);
  try {
    const output = await contactHarness({ execute: async (name, input) => {
      expect(name).toBe("exa_search"); expect(input.include_summary).toBe(false);
      return { results: [{ url: "https://example.com/team", text: quote }] };
    } }, "fake-test-key")(task, { budget, sources, signal, progress: () => {} });
    expect(finalizeReport(output, task, sources).contacts).toHaveLength(1);
    expect(calls).toBe(2);
    for (const payload of payloads) {
      expect(payload).toMatchObject({model: "gpt-5-mini", max_completion_tokens: 16384, reasoning_effort: "low"});
      expect(payload).not.toHaveProperty("temperature");
      expect(payload).not.toHaveProperty("max_tokens");
    }
    expect(budget.usage.model_turns).toBe(2);
    expect(budget.usage.input_tokens).toBe(200);
    expect(budget.usage.searches).toBe(1);
  } finally { globalThis.fetch = original; }
}, 30_000);
