import { expect, test } from "bun:test";
import { createApp } from "../src/server/app";
import { EMPTY_REPORT, type TaskRequest } from "../src/research/contracts";
import { Sources } from "../src/research/sources";
import { Budget } from "../src/research/budget";
import {
  finalizeSubject,
  validSubjectRequest,
  type SubjectRequest,
  type SubjectReport,
} from "../src/subjects/contracts";
import { subjectHarness } from "../src/subjects/harness";

const task = (): SubjectRequest => ({
  version: 1,
  task_id: "1a123456-1234-4123-8123-123456789abc",
  subject: {
    kind: "person",
    name: "Jane Example",
    context: "Acme UK's product director mentioned by the customer.",
    evidence: {
      segment_id: "segment-1",
      quote: "Jane Example at Acme UK recommended the integration.",
    },
  },
});
const post = (value: SubjectRequest) =>
  new Request("http://worker/v1/subjects", {
    method: "POST",
    body: JSON.stringify(value),
  });
const get = () => new Request(`http://worker/v1/subjects/${task().task_id}`);
const empty: SubjectReport = {
  identity_status: "not_found",
  facts: [],
  gaps: ["No verified match."],
};
const waitFor = async (predicate: () => Promise<boolean>) => {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await Bun.sleep(5);
  }
  throw Error("Timed out waiting for subject worker");
};
const sales = (): TaskRequest => ({
  version: 1,
  task_id: "sales-task",
  run_id: "sales-run",
  brief: {
    request: "Find US software businesses",
    geography: "US",
    employee_min: 20,
    employee_max: 1000,
    target_count: 1,
    leadership_days: 180,
    signal_days: 90,
    deadline_seconds: 600,
  },
  deadline_at: new Date(Date.now() + 600_000).toISOString(),
  queries: ["software"],
  focus: "Businesses",
  prior_report: structuredClone(EMPTY_REPORT),
  usage: {},
});
const salesPost = () =>
  new Request("http://worker/v1/tasks", {
    method: "POST",
    body: JSON.stringify(sales()),
  });

test("subject evidence admits people and international businesses without sales filters", () => {
  expect(validSubjectRequest(task())).toBe(true);
  const sources = new Sources();
  const quote =
    "Jane Example is the product director of Acme UK, based in London.";
  const source = sources.add({
    url: "https://example.co.uk/team/jane",
    title: "Team",
    text: quote,
  })!;
  const result = finalizeSubject(
    {
      identity_status: "matched",
      facts: [
        {
          kind: "identity",
          claim: quote,
          citations: [{ source_id: source.id, quote }],
        },
      ],
      gaps: [],
    },
    sources,
  );
  expect(result.identity_status).toBe("matched");
  expect(result.summary).toContain("London");
  expect(result.facts).toHaveLength(1);
  expect(result.sources[0]).not.toHaveProperty("text");
});

test("invented source and quote cannot establish identity or leave stale summary", () => {
  const sources = new Sources();
  sources.add({
    url: "https://example.com",
    text: "This source does not establish the requested identity.",
  });
  const result = finalizeSubject(
    {
      identity_status: "matched",
      facts: [
        {
          kind: "identity",
          claim: "Jane is definitively the correct match.",
          citations: [
            {
              source_id: "invented",
              quote: "Jane is definitively the correct match.",
            },
          ],
        },
      ],
      gaps: [],
    },
    sources,
  );
  expect(result.identity_status).toBe("ambiguous");
  expect(result.facts).toEqual([]);
  expect(result.summary).not.toContain("definitively");
  expect(() =>
    finalizeSubject({ ...empty, sources: [{ text: "forged" }] }, sources),
  ).toThrow("invalid");
});

test("subjects share admission with sales jobs and replay only identical input", async () => {
  let finish!: () => void;
  let executions = 0;
  const app = createApp({
    harness: async () => EMPTY_REPORT,
    subjectHarness: async () => {
      executions++;
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return empty;
    },
  });
  const request = task();
  expect((await app.fetch(post(request))).status).toBe(202);
  expect((await app.fetch(post(request))).status).toBe(200);
  expect(
    (
      await app.fetch(
        post({
          ...request,
          subject: { ...request.subject, context: "Changed identity cue" },
        }),
      )
    ).status,
  ).toBe(409);
  expect((await app.fetch(salesPost())).status).toBe(409);
  expect(executions).toBe(1);
  finish();
  await waitFor(
    async () => (await (await app.fetch(get())).json()).status === "completed",
  );
  expect((await app.fetch(salesPost())).status).toBe(202);
  await app.shutdown();
});

test("running sales job defers subject admission and cancellation releases its slot", async () => {
  let finish!: () => void;
  const app = createApp({
    harness: async () => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return EMPTY_REPORT;
    },
    subjectHarness: async (_task, context) => {
      context.sources.add({
        url: "https://example.com/team",
        text: "A professional source was retrieved before cancellation.",
      });
      await new Promise<void>((resolve) =>
        context.signal.addEventListener("abort", () => resolve(), {
          once: true,
        }),
      );
      context.budget.check();
      return empty;
    },
  });
  await app.fetch(salesPost());
  expect((await app.fetch(post(task()))).status).toBe(409);
  finish();
  await waitFor(
    async () =>
      (
        await (
          await app.fetch(new Request("http://worker/v1/tasks/sales-task"))
        ).json()
      ).status === "completed",
  );
  await app.fetch(post(task()));
  await app.fetch(
    new Request(`http://worker/v1/subjects/${task().task_id}/cancellation`, {
      method: "POST",
    }),
  );
  await waitFor(
    async () => (await (await app.fetch(get())).json()).status === "cancelled",
  );
  const state = await (await app.fetch(get())).json();
  expect(state.sources).toHaveLength(1);
  expect(state.sources[0]).not.toHaveProperty("text");
  await app.shutdown();
});

test("unknown subject sessions, unmentioned names and unconfigured workers fail explicitly", async () => {
  const app = createApp({ harness: async () => EMPTY_REPORT });
  expect((await app.fetch(get())).status).toBe(404);
  expect((await app.fetch(post(task()))).status).toBe(503);
  expect(
    (
      await app.fetch(
        post({
          ...task(),
          subject: { ...task().subject, name: "Unmentioned" },
        }),
      )
    ).status,
  ).toBe(422);
  await app.shutdown();
});

test("subject budget prevents seventh search before calling provider", () => {
  const budget = new Budget(
    {},
    new Date(Date.now() + 180_000).toISOString(),
    new AbortController().signal,
    { searches: 6, pages: 12, model_turns: 8 },
  );
  budget.consume("searches", 6);
  expect(() => budget.consume("searches")).toThrow("budget");
});

test("real subject OMP harness searches Exa with original professional identity context", async () => {
  const originalFetch = globalThis.fetch;
  const sources = new Sources();
  const quote = "Jane Example is the product director of Acme UK in London.";
  const source = sources.add({
    url: "https://example.co.uk/team",
    title: "Acme team",
    text: quote,
  })!;
  const result = {
    identity_status: "matched",
    facts: [
      {
        kind: "identity",
        claim: quote,
        citations: [{ source_id: source.id, quote }],
      },
    ],
    gaps: [],
  };
  let calls = 0;
  let searches = 0;
  const fake = async (input: any, init: any) => {
    expect(String(input instanceof Request ? input.url : input)).toBe(
      "https://api.respan.ai/api/chat/completions",
    );
    const body = JSON.parse(init.body);
    expect(JSON.stringify(body)).toContain("Acme UK's product director");
    expect(JSON.stringify(body)).toContain(
      "no buying signal or sales qualification",
    );
    const first = calls++ === 0;
    const delta = first
      ? {
          role: "assistant",
          tool_calls: [
            {
              index: 0,
              id: "search-1",
              type: "function",
              function: {
                name: "exa_search",
                arguments: JSON.stringify({
                  query: "Jane Example Acme UK product director",
                }),
              },
            },
          ],
        }
      : { role: "assistant", content: JSON.stringify(result) };
    const chunk = (delta: object, finish: string | null) => ({
      id: "test",
      object: "chat.completion.chunk",
      created: 1,
      model: "gpt-5.4",
      choices: [{ index: 0, delta, finish_reason: finish }],
    });
    return new Response(
      [chunk(delta, null), chunk({}, first ? "tool_calls" : "stop")]
        .map((value) => `data: ${JSON.stringify(value)}\n\n`)
        .join("") + "data: [DONE]\n\n",
      { headers: { "Content-Type": "text/event-stream" } },
    );
  };
  globalThis.fetch = Object.assign(fake, {
    preconnect: originalFetch.preconnect,
  }) as typeof fetch;
  try {
    const abort = new AbortController();
    const report = await subjectHarness(
      {
        execute: async (name) => {
          expect(name).toBe("exa_search");
          searches++;
          return {
            results: [{ url: source.url, title: source.title, text: quote }],
          };
        },
      },
      "test-subject-key",
    )(task(), {
      sources,
      signal: abort.signal,
      progress: () => {},
      budget: new Budget(
        {},
        new Date(Date.now() + 180_000).toISOString(),
        abort.signal,
        { searches: 6, pages: 12, model_turns: 8 },
      ),
    });
    expect(searches).toBe(1);
    expect(finalizeSubject(report, sources).facts).toHaveLength(1);
  } finally {
    globalThis.fetch = originalFetch;
  }
}, 30_000);
