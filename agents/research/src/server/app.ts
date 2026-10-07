import { createHash } from "node:crypto";
import {
  canonicalJSON,
  validTask,
  type TaskRequest,
  type TaskStatus,
} from "../research/contracts";
import { Budget, ResearchError, safeError } from "../research/budget";
import { Sources } from "../research/sources";
import type { Harness } from "../harness/omp";
import { tracedTask } from "../telemetry/respan";
export interface Options {
  harness: Harness;
  missing?: () => string[];
  retention?: number;
}
interface Entry {
  hash: string;
  status: TaskStatus;
  abort: AbortController;
  done: Promise<void>;
}
const json = (body: unknown, status = 200) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
export function createApp(options: Options) {
  const tasks = new Map<string, Entry>();
  let active: string | undefined;
  let closing = false;
  const prune = () => {
    for (const [id, task] of tasks) {
      if (tasks.size <= (options.retention ?? 20)) break;
      if (task.status.status !== "running") tasks.delete(id);
    }
  };
  const snapshot = (entry: Entry) => structuredClone(entry.status);
  const execute = async (
    task: TaskRequest,
    entry: Entry,
    headers: Record<string, string>,
  ) => {
    const sources = new Sources(task.prior_report.sources);
    const budget = new Budget(task.usage, task.deadline_at, entry.abort.signal);
    entry.status.usage = budget.usage;
    entry.status.sources = sources.list();
    const timer = setTimeout(
      () => entry.abort.abort("deadline"),
      Math.max(1, budget.deadline - Date.now()),
    );
    const update = (progress: string) => {
      entry.status.progress = progress.slice(0, 500);
      entry.status.sources = sources.list();
    };
    try {
      budget.check();
      const report = await tracedTask(headers, task.run_id, task.task_id, () =>
        options.harness(task, {
          budget,
          sources,
          signal: entry.abort.signal,
          progress: update,
        }),
      );
      budget.check();
      entry.status.report = sources.finalize(report);
      entry.status.status = "completed";
      entry.status.progress = "Research report ready for evidence review";
    } catch (error) {
      entry.status.status =
        entry.abort.signal.aborted && entry.abort.signal.reason !== "deadline"
          ? "cancelled"
          : "failed";
      entry.status.error =
        entry.abort.signal.reason === "deadline"
          ? "Research reached its deadline. Saved evidence is available for an explicit retry."
          : safeError(error);
      entry.status.progress =
        entry.status.status === "cancelled"
          ? "Research cancelled"
          : "Research stopped";
    } finally {
      clearTimeout(timer);
      entry.status.sources = sources.list();
      active = undefined;
      prune();
    }
  };
  async function fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && ["/health", "/ready"].includes(path))
      return json(
        {
          status: closing ? "stopping" : "ok",
          service: "research",
          configured: !options.missing?.().length,
        },
        closing ? 503 : 200,
      );
    if (request.method === "GET" && path === "/v1/capabilities")
      return json({
        version: 1,
        agent: "research",
        configured: !options.missing?.().length,
        missing_credentials: options.missing?.() ?? [],
        capabilities: ["company_research"],
        limits: {
          active_tasks: 1,
          searches: 30,
          pages: 60,
          model_turns: 30,
          deadline_seconds: 600,
        },
      });
    if (request.method === "POST" && path === "/v1/tasks") {
      if (closing) return json({ error: "Research agent is stopping." }, 503);
      if (Number(request.headers.get("content-length") ?? 0) > 2_000_000)
        return json({ error: "Task body is too large." }, 413);
      let task: unknown;
      try {
        const body = await request.text();
        if (body.length > 2_000_000)
          return json({ error: "Task body is too large." }, 413);
        task = JSON.parse(body);
      } catch {
        return json({ error: "Invalid JSON task." }, 400);
      }
      if (!validTask(task))
        return json({ error: "Invalid version 1 research task." }, 422);
      const hash = createHash("sha256")
        .update(canonicalJSON(task))
        .digest("hex");
      const existing = tasks.get(task.task_id);
      if (existing)
        return existing.hash === hash
          ? json(snapshot(existing), 200)
          : json(
              { error: "Task ID already exists with a different payload." },
              409,
            );
      const missing = options.missing?.() ?? [];
      if (missing.length)
        return json(
          {
            error: "Research credentials are not configured.",
            missing_credentials: missing,
          },
          503,
        );
      if (active)
        return json({ error: "Another research task is running." }, 409);
      if (Date.parse(task.deadline_at) <= Date.now())
        return json({ error: "Research deadline has already passed." }, 422);
      const entry: Entry = {
        hash,
        status: {
          version: 1,
          task_id: task.task_id,
          run_id: task.run_id,
          status: "running",
          progress: "Research accepted",
          usage: {
            searches: 0,
            pages: 0,
            model_turns: 0,
            input_tokens: 0,
            output_tokens: 0,
          },
          sources: [],
        },
        abort: new AbortController(),
        done: Promise.resolve(),
      };
      tasks.set(task.task_id, entry);
      active = task.task_id;
      prune();
      const headers = Object.fromEntries(
        ["traceparent", "tracestate"].flatMap((k) =>
          request.headers.get(k) ? [[k, request.headers.get(k)!]] : [],
        ),
      );
      entry.done = execute(task, entry, headers);
      return json(snapshot(entry), 202);
    }
    const match = /^\/v1\/tasks\/([A-Za-z0-9_-]+)(\/cancellation)?$/.exec(path);
    if (match) {
      const task = tasks.get(match[1]);
      if (!task)
        return json(
          {
            error:
              "Research session was not found. Retry explicitly from saved evidence.",
          },
          404,
        );
      if (request.method === "GET" && !match[2]) return json(snapshot(task));
      if (request.method === "POST" && match[2]) {
        if (task.status.status === "running") {
          task.status.progress = "Cancellation requested";
          task.abort.abort("cancelled");
        }
        return json(snapshot(task), 202);
      }
    }
    return json({ error: "Not found" }, 404);
  }
  return {
    fetch,
    shutdown: async () => {
      closing = true;
      for (const task of tasks.values())
        if (task.status.status === "running") task.abort.abort("shutdown");
      await Promise.allSettled([...tasks.values()].map((t) => t.done));
    },
  };
}
