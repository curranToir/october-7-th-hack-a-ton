import { createHash } from "node:crypto";
import { canonicalJSON } from "../../../research/src/research/contracts";
import { Budget, ResearchError, safeError } from "../../../research/src/research/budget";
import { Sources } from "../../../research/src/research/sources";
import { WORKER_LIMITS } from "../../../research/src/research/limits";
import { tracedTask } from "../../../research/src/telemetry/respan";
import type { ContactHarness } from "../harness/omp";
import { validTask, type ContactStatus, type ContactTask } from "../research/contracts";
import { finalizeReport } from "../research/evidence";

interface Entry { hash: string; status: ContactStatus; abort: AbortController; sources: Sources; done: Promise<void> }
interface Options { harness: ContactHarness; missing?: () => string[]; retention?: number }
const json = (body: unknown, status = 200) => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
export function createApp(options: Options) {
  const tasks = new Map<string, Entry>();
  const retention = Math.max(1, Math.min(100, options.retention ?? 20));
  let active: string | undefined, closing = false;
  const prune = () => {
    for (const [id, task] of tasks) {
      if (tasks.size <= retention) break;
      if (task.status.status !== "running") tasks.delete(id);
    }
  };
  const snapshot = (entry: Entry) => structuredClone({ ...entry.status, sources: entry.sources.list() });
  async function execute(task: ContactTask, entry: Entry, headers: Record<string, string>) {
    const budget = new Budget(task.usage, task.deadline_at, entry.abort.signal);
    entry.status.usage = budget.usage;
    const timer = setTimeout(() => entry.abort.abort("deadline"), Math.max(1, budget.deadline - Date.now()));
    let onAbort: (() => void) | undefined;
    try {
      budget.check();
      // The deadline also settles status when a provider stalls. The shared harness
      // aborts its session and checks the signal before every future outbound call.
      const report = await Promise.race([
        tracedTask(headers, task.run_id, task.task_id, () => options.harness(task, {
          budget, sources: entry.sources, signal: entry.abort.signal,
          progress: (progress) => { if (!entry.abort.signal.aborted) entry.status.progress = progress.slice(0, 500); },
        }), "contacts"),
        new Promise<never>((_resolve, reject) => {
          onAbort = () => reject(new ResearchError("cancelled", "Contact research was cancelled."));
          entry.abort.signal.addEventListener("abort", onAbort, { once: true });
          if (entry.abort.signal.aborted) onAbort();
        }),
      ]);
      budget.check();
      entry.status.report = finalizeReport(report, task, entry.sources);
      entry.status.status = "completed";
      entry.status.progress = task.mode === "resolve" ? "Company identity research completed" : "Contact research ready for evidence review";
    } catch (error) {
      entry.status.status = entry.abort.signal.aborted && entry.abort.signal.reason !== "deadline" ? "cancelled" : "failed";
      entry.status.error = entry.abort.signal.reason === "deadline"
        ? "Contact research reached its deadline. Saved evidence is available for an explicit retry."
        : safeError(error);
      entry.status.progress = entry.status.status === "cancelled" ? "Contact research cancelled" : "Contact research stopped";
    } finally {
      clearTimeout(timer);
      if (onAbort) entry.abort.signal.removeEventListener("abort", onAbort);
      active = undefined;
      prune();
    }
  }
  async function fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && ["/health", "/healthz", "/ready", "/readyz"].includes(path)) {
      const missing = options.missing?.() ?? [];
      const ready = !closing && missing.length === 0;
      return json({ status: closing ? "stopping" : "ok", service: "contacts", configured: missing.length === 0, ready },
        path.startsWith("/ready") && !ready ? 503 : 200);
    }
    if (request.method === "GET" && path === "/v1/capabilities") {
      const missing = options.missing?.() ?? [];
      return json({ version: 1, agent: "contacts", configured: missing.length === 0, ready: !closing && !missing.length,
        draining: closing, active_tasks: active ? 1 : 0, missing_credentials: missing,
        capabilities: ["company_resolution", "contact_research"],
        limits: { ...WORKER_LIMITS, contacts: 5, retained_tasks: retention } });
    }
    if (request.method === "POST" && path === "/v1/tasks") {
      if (closing) return json({ error: "Contact agent is stopping." }, 503);
      if (Number(request.headers.get("content-length") ?? 0) > 2_000_000) return json({ error: "Task body is too large." }, 413);
      let task: unknown;
      try {
        const body = await request.text();
        if (Buffer.byteLength(body) > 2_000_000) return json({ error: "Task body is too large." }, 413);
        task = JSON.parse(body);
      } catch { return json({ error: "Invalid JSON task." }, 400); }
      if (!validTask(task)) return json({ error: "Invalid version 1 contact task." }, 422);
      const hash = createHash("sha256").update(canonicalJSON(task)).digest("hex");
      const existing = tasks.get(task.task_id);
      if (existing) return existing.hash === hash ? json(snapshot(existing)) : json({ error: "Task ID already exists with a different payload." }, 409);
      const missing = options.missing?.() ?? [];
      if (missing.length) return json({ error: "Contact research credentials are not configured.", missing_credentials: missing }, 503);
      if (active) return json({ error: "Another contact task is running." }, 409);
      if (Date.parse(task.deadline_at) <= Date.now()) return json({ error: "Contact deadline has already passed." }, 422);
      const entry: Entry = { hash, abort: new AbortController(), sources: new Sources(task.prior_sources), done: Promise.resolve(),
        status: { version: 1, task_id: task.task_id, run_id: task.run_id, status: "running", progress: "Contact task accepted",
          usage: { searches: 0, pages: 0, model_turns: 0, input_tokens: 0, output_tokens: 0 }, sources: [] } };
      tasks.set(task.task_id, entry);
      active = task.task_id;
      prune();
      const headers = Object.fromEntries(["traceparent", "tracestate"].flatMap((key) => request.headers.get(key) ? [[key, request.headers.get(key)!]] : []));
      entry.done = execute(task, entry, headers);
      return json(snapshot(entry), 202);
    }
    const match = /^\/v1\/tasks\/([A-Za-z0-9_-]+)(\/cancellation)?$/.exec(path);
    if (match) {
      const entry = tasks.get(match[1]);
      if (!entry) return json({ error: "Contact session was not found. Retry explicitly from saved evidence." }, 404);
      if (request.method === "GET" && !match[2]) return json(snapshot(entry));
      if (request.method === "POST" && match[2]) {
        if (entry.status.status === "running") { entry.status.progress = "Cancellation requested"; entry.abort.abort("cancelled"); }
        return json(snapshot(entry), 202);
      }
    }
    return json({ error: "Not found" }, 404);
  }
  return { fetch, shutdown: async () => {
    closing = true;
    for (const task of tasks.values()) if (task.status.status === "running") task.abort.abort("shutdown");
    await Promise.allSettled([...tasks.values()].map((task) => task.done));
  } };
}
