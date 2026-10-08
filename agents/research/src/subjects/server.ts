import { createHash } from "node:crypto";
import { canonicalJSON, type Usage } from "../research/contracts";
import { Budget, safeError } from "../research/budget";
import { Sources } from "../research/sources";
import { tracedTask } from "../telemetry/respan";
import {
  finalizeSubject,
  validSubjectRequest,
  type SubjectRequest,
  type SubjectResult,
} from "./contracts";
import type { SubjectHarness } from "./harness";

type Status = Partial<SubjectResult> & {
  task_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  progress: string;
  usage: Usage;
  error?: string;
};
interface Entry {
  hash: string;
  status: Status;
  abort: AbortController;
  done: Promise<void>;
}
interface Options {
  harness?: SubjectHarness;
  missing: () => string[];
  closing: () => boolean;
  claim: (id: string) => boolean;
  release: (id: string) => void;
  retention: number;
}
const json = (body: unknown, status = 200) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

export function createSubjectServer(options: Options) {
  const tasks = new Map<string, Entry>();
  const snapshot = (entry: Entry) => structuredClone(entry.status);
  const prune = () => {
    for (const [id, entry] of tasks) {
      if (tasks.size <= options.retention) break;
      if (entry.status.status !== "running") tasks.delete(id);
    }
  };
  async function execute(
    task: SubjectRequest,
    entry: Entry,
    headers: Record<string, string>,
  ) {
    const sources = new Sources();
    const budget = new Budget(
      {},
      new Date(Date.now() + 180_000).toISOString(),
      entry.abort.signal,
      { searches: 6, pages: 12, model_turns: 8 },
    );
    entry.status.usage = budget.usage;
    const timer = setTimeout(() => entry.abort.abort("deadline"), 180_000);
    try {
      const result = await tracedTask(headers, task.task_id, task.task_id, () =>
        options.harness!(task, {
          sources,
          budget,
          signal: entry.abort.signal,
          progress: (value) => {
            entry.status.progress = value.slice(0, 500);
          },
        }),
      );
      budget.check();
      Object.assign(entry.status, finalizeSubject(result, sources), {
        status: "completed",
        progress: "Subject research completed",
      });
    } catch (error) {
      entry.status.status =
        entry.abort.signal.aborted && entry.abort.signal.reason !== "deadline"
          ? "cancelled"
          : "failed";
      entry.status.error =
        entry.abort.signal.reason === "deadline"
          ? "Subject research reached its time limit; retry explicitly."
          : safeError(error);
      entry.status.progress = "Subject research stopped";
    } finally {
      clearTimeout(timer);
      entry.status.sources = sources
        .list()
        .map(({ id, title, url, retrieved_at }) => ({
          id,
          title,
          url,
          retrieved_at,
        }));
      options.release(`subject:${task.task_id}`);
      prune();
    }
  }
  async function fetch(request: Request): Promise<Response | null> {
    const path = new URL(request.url).pathname;
    if (request.method === "POST" && path === "/v1/subjects") {
      if (options.closing())
        return json({ error: "Research agent is stopping." }, 503);
      if (Number(request.headers.get("content-length") ?? 0) > 16_000)
        return json({ error: "Subject request is too large." }, 413);
      let task: unknown;
      try {
        const body = await request.text();
        if (body.length > 16_000)
          return json({ error: "Subject request is too large." }, 413);
        task = JSON.parse(body);
      } catch {
        return json({ error: "Invalid JSON subject." }, 400);
      }
      if (
        !validSubjectRequest(task) ||
        !task.subject.evidence.quote
          .toLowerCase()
          .includes(task.subject.name.toLowerCase())
      )
        return json({ error: "Invalid or unmentioned research subject." }, 422);
      const hash = createHash("sha256")
        .update(canonicalJSON(task))
        .digest("hex");
      const previous = tasks.get(task.task_id);
      if (previous)
        return previous.hash === hash
          ? json(snapshot(previous))
          : json(
              { error: "Subject task ID already exists with different input." },
              409,
            );
      if (!options.harness || options.missing().length)
        return json({ error: "Subject research is not configured." }, 503);
      if (!options.claim(`subject:${task.task_id}`))
        return json({ error: "Another research task is running." }, 409);
      const entry: Entry = {
        hash,
        abort: new AbortController(),
        done: Promise.resolve(),
        status: {
          task_id: task.task_id,
          status: "running",
          progress: "Subject research accepted",
          facts: [],
          sources: [],
          gaps: [],
          summary: "",
          usage: {
            searches: 0,
            pages: 0,
            model_turns: 0,
            input_tokens: 0,
            output_tokens: 0,
          },
        },
      };
      tasks.set(task.task_id, entry);
      prune();
      const headers = Object.fromEntries(
        ["traceparent", "tracestate"].flatMap((key) =>
          request.headers.get(key) ? [[key, request.headers.get(key)!]] : [],
        ),
      );
      entry.done = execute(task, entry, headers);
      return json(snapshot(entry), 202);
    }
    const match = /^\/v1\/subjects\/([0-9a-fA-F-]{36})(\/cancellation)?$/.exec(
      path,
    );
    if (!match) return null;
    const entry = tasks.get(match[1]);
    if (!entry)
      return json(
        { error: "Subject research session was lost; retry explicitly." },
        404,
      );
    if (request.method === "GET" && !match[2]) return json(snapshot(entry));
    if (request.method === "POST" && match[2]) {
      if (entry.status.status === "running") entry.abort.abort("cancelled");
      return json(snapshot(entry), 202);
    }
    return null;
  }
  return {
    fetch,
    shutdown: async () => {
      for (const entry of tasks.values())
        if (entry.status.status === "running") entry.abort.abort("shutdown");
      await Promise.allSettled([...tasks.values()].map((entry) => entry.done));
    },
  };
}
