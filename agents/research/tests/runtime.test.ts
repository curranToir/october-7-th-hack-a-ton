import { test, expect } from "bun:test";
import { createHash } from "node:crypto";
import { createApp } from "../src/server/app";
import { Budget, ResearchError } from "../src/research/budget";
import { Sources } from "../src/research/sources";
import {
  EMPTY_REPORT,
  validTask,
  type TaskRequest,
} from "../src/research/contracts";
import { ExaTools } from "../src/tools/scalekit";
import { createRestrictedSession } from "../src/harness/omp";
const request = (): TaskRequest => ({
  version: 1,
  task_id: "task-1",
  run_id: "run-1",
  brief: {
    request: "Find newly funded US technology companies",
    geography: "US",
    employee_min: 20,
    employee_max: 1000,
    target_count: 10,
    leadership_days: 180,
    signal_days: 90,
    deadline_seconds: 600,
  },
  deadline_at: new Date(Date.now() + 600000).toISOString(),
  queries: ["new funding"],
  focus: "US companies",
  prior_report: structuredClone(EMPTY_REPORT),
  usage: {},
});
const post = (task: TaskRequest) =>
  new Request("http://agent/v1/tasks", {
    method: "POST",
    body: JSON.stringify(task),
  });
const get = () => new Request("http://agent/v1/tasks/task-1");
const waitUntil = async (work: () => Promise<boolean>) => {
  for (let i = 0; i < 100; i++) {
    if (await work()) return;
    await Bun.sleep(5);
  }
  throw new Error("Timed out");
};
test("validates task shape and supports coordinator usage counters", () => {
  const t = request();
  expect(validTask(t)).toBe(true);
  t.brief.employee_max = 1;
  expect(validTask(t)).toBe(false);
});
test("missing credentials report names, keep ready healthy and reject tasks", async () => {
  const app = createApp({
    missing: () => ["SCALEKIT_CLIENT_SECRET"],
    harness: async () => EMPTY_REPORT,
  });
  expect((await app.fetch(new Request("http://a/ready"))).status).toBe(200);
  expect((await app.fetch(post(request()))).status).toBe(503);
});
test("idempotency hashes canonical payload and prevents concurrent tasks", async () => {
  let finish!: () => void;
  const app = createApp({
    harness: async () => {
      await new Promise<void>((r) => (finish = r));
      return EMPTY_REPORT;
    },
  });
  const t = request();
  expect((await app.fetch(post(t))).status).toBe(202);
  expect((await app.fetch(post(t))).status).toBe(200);
  expect((await app.fetch(post({ ...t, focus: "different" }))).status).toBe(
    409,
  );
  expect((await app.fetch(post({ ...t, task_id: "task-2" }))).status).toBe(409);
  finish();
  await waitUntil(
    async () => (await (await app.fetch(get())).json()).status === "completed",
  );
  await app.shutdown();
});
test("cancel aborts execution and preserves retrieved evidence", async () => {
  const app = createApp({
    harness: async (_task, ctx) => {
      ctx.sources.add({
        url: "https://example.com/news",
        text: "The company hired a new CTO.",
        title: "News",
      });
      ctx.progress("Reading");
      await new Promise<void>((r) =>
        ctx.signal.addEventListener("abort", () => r(), { once: true }),
      );
      throw new ResearchError("cancelled", "Research was cancelled.");
    },
  });
  await app.fetch(post(request()));
  await app.fetch(
    new Request("http://a/v1/tasks/task-1/cancellation", { method: "POST" }),
  );
  await waitUntil(
    async () => (await (await app.fetch(get())).json()).status === "cancelled",
  );
  const status = await (await app.fetch(get())).json();
  expect(status.sources).toHaveLength(1);
  await app.shutdown();
});
test("lost sessions return 404 and SDK exceptions never leak values", async () => {
  const app = createApp({
    harness: async () => {
      throw new Error("secret-do-not-print");
    },
  });
  expect((await app.fetch(get())).status).toBe(404);
  await app.fetch(post(request()));
  await waitUntil(
    async () => (await (await app.fetch(get())).json()).status === "failed",
  );
  expect(await (await app.fetch(get())).text()).not.toContain(
    "secret-do-not-print",
  );
  await app.shutdown();
});
test("Scalekit raw response creates immutable source IDs; generated summary ignored", async () => {
  const calls: any[] = [];
  const sources = new Sources();
  const budget = new Budget(
    {},
    new Date(Date.now() + 60000).toISOString(),
    new AbortController().signal,
  );
  const exa = new ExaTools(
    {
      execute: async (name, input) => {
        calls.push({ name, input });
        return {
          results: [
            {
              url: "https://example.com/news",
              title: "News",
              text: "The company appointed Jane Chief Technology Officer.",
              summary: "Made up text",
            },
          ],
        };
      },
    },
    sources,
    budget,
    () => {},
  );
  await exa.search("new CTO");
  expect(calls[0].name).toBe("exa_search");
  expect(calls[0].input.include_summary).toBe(false);
  expect(sources.list()[0].text).not.toContain("Made up");
  expect(sources.list()[0].id).toBe(
    `src_${createHash("sha256").update("https://example.com/news").digest("hex").slice(0, 16)}`,
  );
});
test("rejects invented citation even when model supplies fake source text", () => {
  const sources = new Sources();
  const source = sources.add({
    url: "https://example.com",
    text: "A legitimate company announcement from Example.",
  })!;
  const report = sources.finalize({
    ...EMPTY_REPORT,
    sources: [{ ...source, text: "This is invented funding information" }],
    competitors: [
      {
        company: "Example",
        kind: "pricing",
        claim: "This is invented funding information",
        positioning_hypothesis: "Explore lower costs.",
        citations: [
          {
            source_id: source.id,
            quote: "This is invented funding information",
          },
        ],
      },
    ],
  });
  expect(report.competitors).toHaveLength(0);
  expect(report.sources[0].text).toContain("legitimate");
});
test("shared budgets stop subsequent passes and private URLs are blocked", async () => {
  const budget = new Budget(
    { searches: 30 },
    new Date(Date.now() + 60000).toISOString(),
    new AbortController().signal,
  );
  const exa = new ExaTools(
    {
      execute: async () => {
        throw new Error("must not call");
      },
    },
    new Sources(),
    budget,
    () => {},
  );
  await expect(exa.search("one more")).rejects.toThrow("budget");
  expect(() => exa.read(["http://169.254.169.254/latest/meta-data"])).toThrow(
    "public",
  );
});
test("real OMP SDK constructs restricted session without calling a model", async () => {
  const task = request();
  const signal = new AbortController().signal;
  const runtime = await createRestrictedSession(
    task,
    {
      budget: new Budget({}, task.deadline_at, signal),
      sources: new Sources(),
      signal,
      progress: () => {},
    },
    {
      execute: async () => {
        throw new Error("Unexpected tool call");
      },
    },
    "fake-test-key",
  );
  expect(runtime.session.agent.state.model.provider).toBe("respan");
  expect(runtime.session.agent.state.model.api).toBe("openai-completions");
  expect(runtime.session.agent.state.tools.map((t) => t.name).sort()).toEqual([
    "exa_crawl",
    "exa_find_similar",
    "exa_search",
  ]);
  await runtime.dispose();
}, 30000);
test("real OMP loop calls only Respan and custom Scalekit tools with a fake gateway", async () => {
  const originalFetch = globalThis.fetch;
  const outbound: string[] = [];
  let calls = 0;
  const url = "https://example.com/news";
  const quote = "Example appointed Jane as its new technology leader.";
  const sourceID = `src_${createHash("sha256").update(url).digest("hex").slice(0, 16)}`;
  const final = {
    ...EMPTY_REPORT,
    competitors: [
      {
        company: "Example",
        kind: "marketing",
        claim: quote,
        positioning_hypothesis: "Investigate a focused AI integration offer.",
        citations: [{ source_id: sourceID, quote }],
      },
    ],
  };
  const fakeFetch = async (input: any, init: any) => {
    const endpoint = String(input instanceof Request ? input.url : input);
    outbound.push(endpoint);
    expect(endpoint).toBe("https://api.respan.ai/api/chat/completions");
    const body = JSON.parse(init.body);
    expect(body.model).toBe("gpt-5.4");
    expect(body.tools.map((t: any) => t.function.name).sort()).toEqual([
      "exa_crawl",
      "exa_find_similar",
      "exa_search",
    ]);
    const first = calls++ === 0;
    const delta = first
      ? {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: "call_exa_1",
              type: "function",
              function: {
                name: "exa_search",
                arguments: JSON.stringify({
                  query: "Example new technology leader",
                }),
              },
            },
          ],
        }
      : { role: "assistant", content: JSON.stringify(final) };
    const chunk = (choices: any[], extra: object = {}) => ({
      id: "chat-test",
      object: "chat.completion.chunk",
      created: 1,
      model: "gpt-5.4",
      choices,
      ...extra,
    });
    const events = [
      chunk([{ index: 0, delta, finish_reason: null }]),
      chunk([
        { index: 0, delta: {}, finish_reason: first ? "tool_calls" : "stop" },
      ]),
      chunk([], {
        usage: { prompt_tokens: 100, completion_tokens: 30, total_tokens: 130 },
      }),
    ];
    return new Response(
      events.map((x) => `data: ${JSON.stringify(x)}\n\n`).join("") +
        "data: [DONE]\n\n",
      { headers: { "Content-Type": "text/event-stream" } },
    );
  };
  globalThis.fetch = Object.assign(fakeFetch, {
    preconnect: originalFetch.preconnect,
  }) as typeof fetch;
  const task = request(),
    signal = new AbortController().signal,
    sources = new Sources(),
    budget = new Budget({}, task.deadline_at, signal);
  try {
    const { ompHarness } = await import("../src/harness/omp");
    const report = await ompHarness(
      {
        execute: async (name, input) => {
          expect(name).toBe("exa_search");
          expect(input.include_summary).toBe(false);
          return {
            results: [{ url, title: "Leadership announcement", text: quote }],
          };
        },
      },
      "fake-test-key",
    )(task, { budget, sources, signal, progress: () => {} });
    expect(outbound).toHaveLength(2);
    expect(report.competitors).toHaveLength(1);
    expect(report.sources).toHaveLength(1);
    expect(budget.usage.model_turns).toBe(2);
    expect(budget.usage.input_tokens).toBe(200);
  } finally {
    globalThis.fetch = originalFetch;
  }
}, 30000);

test("report defaults match Python and whitespace cannot act as evidence", () => {
  const sources = new Sources();
  const source = sources.add({url: "https://example.com/news", text: "Verified company announcement.", publishedDate: "2026-02-30"})!;
  expect(source.published_at).toBeNull();
  const partial = sources.finalize({leads: []});
  expect(partial.competitors).toEqual([]);
  expect(partial.gaps).toEqual([]);
  expect(partial.summary).toBe("");
  const forged = sources.finalize({competitors: [{company: "Example", kind: "pricing", claim: "A fabricated pricing claim.", positioning_hypothesis: "Investigate pricing", citations: [{source_id: source.id, quote: "            "}]}]});
  expect(forged.competitors).toEqual([]);
});

test("real Respan transport auth failures are sanitized", async () => {
  const originalFetch = globalThis.fetch;
  const fake = async () => new Response(JSON.stringify({error:{message:"Credential fake-test-key must never reach the UI",type:"authentication_error",code:"invalid_api_key"}}), {status:401, headers:{"Content-Type":"application/json"}});
  globalThis.fetch = Object.assign(fake,{preconnect:originalFetch.preconnect}) as typeof fetch;
  try {
    const {ompHarness}=await import("../src/harness/omp");
    const task=request(),signal=new AbortController().signal;
    const app=createApp({harness:ompHarness({execute:async()=>{throw Error("Unexpected search");}},"fake-test-key")});
    await app.fetch(post(task));
    await waitUntil(async()=>(await (await app.fetch(get())).json()).status==='failed');
    const status=await (await app.fetch(get())).json();
    expect(status.error).toContain("Respan");
    expect(status.error).not.toContain("fake-test-key");
    await app.shutdown();
  } finally {globalThis.fetch=originalFetch;}
});

test("W3C coordinator context reaches real OMP model spans without content capture", async () => {
  const {NodeTracerProvider,SimpleSpanProcessor,InMemorySpanExporter}=await import("@opentelemetry/sdk-trace-node");
  const {tracedTask}=await import("../src/telemetry/respan");
  const {ompHarness}=await import("../src/harness/omp");
  const exporter=new InMemorySpanExporter();
  const provider=new NodeTracerProvider({spanProcessors:[new SimpleSpanProcessor(exporter)]});
  provider.register();
  const originalFetch=globalThis.fetch;
  const fake=async()=>new Response(`data: ${JSON.stringify({id:"test",object:"chat.completion.chunk",created:1,model:"gpt-5.4",choices:[{index:0,delta:{role:"assistant",content:JSON.stringify(EMPTY_REPORT)},finish_reason:null}]})}\n\ndata: ${JSON.stringify({id:"test",object:"chat.completion.chunk",created:1,model:"gpt-5.4",choices:[{index:0,delta:{},finish_reason:"stop"}]})}\n\ndata: [DONE]\n\n`,{headers:{"Content-Type":"text/event-stream"}});
  globalThis.fetch=Object.assign(fake,{preconnect:originalFetch.preconnect}) as typeof fetch;
  const traceID="1234567890abcdef1234567890abcdef",parentID="1234567890abcdef";
  try {
    const task=request(),signal=new AbortController().signal;
    await tracedTask({traceparent:`00-${traceID}-${parentID}-01`},task.run_id,task.task_id,()=>ompHarness({execute:async()=>{throw Error("Unexpected search");}},"fake-test-key")(task,{budget:new Budget({},task.deadline_at,signal),sources:new Sources(),signal,progress:()=>{}}));
    await provider.forceFlush();
    const spans=exporter.getFinishedSpans();
    const root=spans.find(s=>s.name==='research.task')!;
    expect(root.parentSpanContext?.spanId).toBe(parentID);
    expect(root.attributes['toir.run_id']).toBe(task.run_id);
    expect(spans.length).toBeGreaterThan(2);
    expect(spans.every(s=>s.spanContext().traceId===traceID)).toBe(true);
    expect(spans.some(s=>s.attributes['gen_ai.request.model']==='gpt-5.4')).toBe(true);
    const allAttributes=JSON.stringify(spans.map(s=>s.attributes));
    expect(allAttributes).not.toContain('fake-test-key');
    expect(allAttributes).not.toContain(task.brief.request);
  } finally {globalThis.fetch=originalFetch;await provider.shutdown();}
},30000);
