import { expect, test } from "bun:test";
import { Budget } from "../src/research/budget";
import { WORKER_LIMITS } from "../src/research/limits";
import { Sources } from "../src/research/sources";
import { ExaTools } from "../src/tools/scalekit";
import { createApp as researchApp } from "../src/server/app";
import { EMPTY_REPORT } from "../src/research/contracts";

const budget = (prior = {}) => new Budget(prior, new Date(Date.now() + 3_600_000).toISOString(), new AbortController().signal);

test("larger budgets carry prior pass use and reject only beyond each exact ceiling", () => {
  const carried = budget({ searches: 30, pages: 60, model_turns: 30 });
  expect(carried.remaining()).toEqual({ searches: 30, pages: 140, model_turns: 30 });
  expect(carried.deadline - Date.now()).toBeLessThanOrEqual(600_000);
  for (const kind of ["searches", "pages", "model_turns"] as const) {
    carried.consume(kind, carried.remaining()[kind]);
    expect(carried.usage[kind]).toBe(WORKER_LIMITS[kind]);
    expect(() => carried.consume(kind)).toThrow("budget");
    expect(carried.usage[kind]).toBe(WORKER_LIMITS[kind]);
  }
  expect(carried.remaining()).toEqual({ searches: 0, pages: 0, model_turns: 0 });
});

test("final search reserves only the remaining two pages and no extra provider request", async () => {
  const carried = budget({ searches: 59, pages: 198 });
  const calls: Record<string, unknown>[] = [];
  const tools = new ExaTools({ execute: async (_name, input) => { calls.push(input); return { results: [] }; } }, new Sources(), carried, () => {});
  await tools.search("company evidence");
  expect(calls).toHaveLength(1);
  expect(calls[0].num_results).toBe(2);
  expect(carried.usage).toMatchObject({ searches: 60, pages: 200 });
  expect(() => tools.search("another company")).toThrow("pages budget");
  expect(calls).toHaveLength(1);
});

test("research capabilities advertise the enforced cumulative ceilings", async () => {
  const research = researchApp({ harness: async () => EMPTY_REPORT });
  for (const app of [research]) {
    const response = await app.fetch(new Request("http://worker/v1/capabilities"));
    expect(response.status).toBe(200);
    expect((await response.json()).limits).toMatchObject(WORKER_LIMITS);
    await app.shutdown();
  }
});

test("source excerpts preserve prior verified quotes without shortening trusted host evidence", async () => {
  const { sourceExcerpts } = await import("../src/harness/model-context");
  const sources = new Sources();
  const quote = "The verified company-wide headcount is 123 employees.";
  const source = sources.add({ url: "https://example.com/company", text: "Background. ".repeat(500) + quote })!;
  const projected = sourceExcerpts([source], { identity_citations: [{ source_id: source.id, quote }] });
  expect(projected[0].text).toContain(quote);
  expect(projected[0].text.length).toBeLessThan(source.text.length);
  expect(sources.list()[0].text).toBe(source.text);
  expect(sourceExcerpts([source])[0].text).toContain("123 employees");
  expect(sourceExcerpts([source])[0].text.length).toBeLessThanOrEqual(4000);
});

test("large context forces a final report before the hard input ceiling", async () => {
  const { boundedContext } = await import("../src/harness/model-context");
  const context = { systemPrompt: ["Research and return JSON."], messages: [{ role: "user" as const, content: "x ".repeat(225_000), timestamp: 1 }] };
  const result = boundedContext(context);
  expect(result.finalizing).toBe(true);
  expect(result.context.tools).toEqual([]);
  expect(result.context.systemPrompt?.join("\n")).toContain("final JSON report now");
  expect(() => boundedContext({ ...context, messages: [{ ...context.messages[0], content: "x ".repeat(275_000) }] })).toThrow("input context budget");
});
