import { expect, test } from "bun:test";
import { Code, ConnectError } from "@connectrpc/connect";
import { ScalekitServerException } from "@scalekit-sdk/node";
import { Budget } from "../src/research/budget";
import { Sources } from "../src/research/sources";
import { ExaTools } from "../src/tools/scalekit";

function fixture(error: unknown, recover = false) {
  let calls = 0;
  const budget = new Budget(
    {},
    new Date(Date.now() + 60_000).toISOString(),
    new AbortController().signal,
  );
  const tools = new ExaTools(
    {
      execute: async () => {
        calls++;
        if (calls === 1 || !recover) throw error;
        return { results: [] };
      },
    },
    new Sources(),
    budget,
    () => {},
  );
  return { tools, budget, calls: () => calls };
}

for (const status of [Code.Unauthenticated, Code.PermissionDenied]) {
  test(`SDK authentication getter ${status} fails immediately without leaking details`, async () => {
    const error = new ScalekitServerException(
      new ConnectError("provider-private-error", status),
    );
    const f = fixture(error);
    await expect(f.tools.search("company news")).rejects.toThrow(
      "authentication or the Exa connected account requires attention",
    );
    expect(f.calls()).toBe(1);
  });
}

for (const status of [Code.Unavailable, Code.DeadlineExceeded]) {
  test(`SDK transient getter ${status} retries within the cumulative budget`, async () => {
    const f = fixture(
      new ScalekitServerException(
        new ConnectError("provider-private-error", status),
      ),
      true,
    );
    const result = await f.tools.search("company news");
    expect(result).toMatchObject({ result_count: 0 });
    expect(f.calls()).toBe(2);
    expect(f.budget.usage.searches).toBe(2);
    expect(f.budget.usage.pages).toBe(10);
  });
}

test("SDK HTTP rate limit keeps its gRPC classification when statusText maps to 500", async () => {
  const error = new ScalekitServerException({
    status: 429,
    statusText: "Too Many Requests",
    data: { message: "provider-private-error" },
  } as ConstructorParameters<typeof ScalekitServerException>[0]);
  expect(error.httpStatus).toBe(500);
  expect(error.grpcStatus).toBe(Code.ResourceExhausted);
  const f = fixture(error);
  await expect(f.tools.search("company news")).rejects.toThrow(
    "rate limit exhausted",
  );
  expect(f.calls()).toBe(3);
  expect(f.budget.usage.searches).toBe(3);
});

test("httpStatus-only errors are classified without public message details", async () => {
  const f = fixture({ httpStatus: 403, message: "provider-private-error" });
  await expect(f.tools.search("company news")).rejects.toThrow(
    "authentication or the Exa connected account requires attention",
  );
  expect(f.calls()).toBe(1);
});
