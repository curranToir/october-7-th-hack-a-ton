import type {
  AuthSession,
  AutomationUpdate,
  Decision,
  LiveWorkspace,
  Preferences,
  SalesJob,
  SalesMessage,
  SalesProposal,
  SalesSession,
  SalesSnapshot,
} from "./types.ts";

/** Only presentation preferences live in the browser. No demo state is imported. */
export const defaultPreferences: Preferences = {
  theme: "system",
  compactSidebar: false,
  timeZone: "America/Los_Angeles",
  defaultAgent: "TOIR research",
  autoAdvance: true,
  requireApproval: true,
  notifyTasks: false,
  notifyCompleted: false,
  sound: false,
  saveHistory: true,
};
export function readPreferences(raw: string | null): Preferences {
  try {
    const value = JSON.parse(raw ?? "{}");
    if (!value || typeof value !== "object") return { ...defaultPreferences };
    return {
      ...defaultPreferences,
      theme: ["system", "light", "dark"].includes(value.theme)
        ? value.theme
        : "system",
      compactSidebar: value.compactSidebar === true,
      autoAdvance: value.autoAdvance !== false,
    };
  } catch {
    return { ...defaultPreferences };
  }
}
export function presentWorkspace(
  data: SalesSnapshot,
  preferences: Preferences,
): LiveWorkspace {
  return {
    version: 1,
    profile: {
      name: data.user.name || data.user.email,
      email: data.user.email,
      role: "Sales member",
    },
    preferences: { ...preferences, requireApproval: true, saveHistory: true },
    workspace: {
      name: "TOIR sales",
      description: "Company research and approved CRM updates",
    },
    sessions: data.sessions
      .filter((s) => !s.deleted)
      .map((s) => ({
        id: s.id,
        title: s.title,
        agent: s.automation ? "Continuous prospecting" : "TOIR research",
        createdAt: s.created_at,
        messages: data.messages
          .filter((m) => m.session_id === s.id)
          .sort((a, b) => a.created_at.localeCompare(b.created_at))
          .map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            createdAt: m.created_at,
            taskIds: m.task_ids,
          })),
      }))
      .sort((a, b) => {
        const latest = (s: typeof a) =>
          s.messages.at(-1)?.createdAt ?? s.createdAt;
        return latest(b).localeCompare(latest(a));
      }),
    // Compatibility projection for the existing sidebar. Proposals remain canonical.
    tasks: data.tasks.map((p) => ({
      id: p.id,
      sessionId: p.session_id,
      agent: "Contact research",
      title: `Review ${p.company.name}`,
      subject: p.company.name,
      subtitle: p.company.domain,
      finding: p.company.description,
      source: "Research evidence",
      sourceDetail: "",
      steps: [],
      scope: "Approved CRM changes",
      restriction: "No outreach",
      approveLabel: "Approve CRM changes",
      status: p.status,
      createdAt: p.created_at,
      decidedAt: p.decided_at ?? undefined,
    })),
    connections: [
      {
        id: "hubspot",
        name: "HubSpot",
        description: "Shared Toir connection · curran@toirinc.com",
        connected: data.capabilities.crm,
        initials: "HS",
      },
    ],
    proposals: data.tasks,
    jobs: data.jobs,
    automation: data.automation,
    capabilities: data.capabilities,
  };
}
export class SalesApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "SalesApiError";
    this.status = status;
  }
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* Gateway error. */
    }
    throw new SalesApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : response.status === 401
          ? "Sign in to your sales workspace."
          : response.status === 409
            ? "This task changed. Review the latest version before deciding."
            : `The request could not be completed (${response.status}). Please try again.`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
const json = (method: string, body?: unknown, key?: string): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  ...(key ? { headers: { "Idempotency-Key": key } } : {}),
});
export const salesClient = {
  workspace: () => request<SalesSnapshot>("/api/sales/workspace"),
  createSession: () =>
    request<SalesSession>("/api/sales/sessions", json("POST", {})),
  renameSession: (id: string, title: string) =>
    request<SalesSession>(
      `/api/sales/sessions/${encodeURIComponent(id)}`,
      json("PATCH", { title }),
    ),
  deleteSession: (id: string) =>
    request<void>(
      `/api/sales/sessions/${encodeURIComponent(id)}`,
      json("DELETE"),
    ),
  send: (id: string, content: string, key: string) =>
    request<SalesMessage>(
      `/api/sales/sessions/${encodeURIComponent(id)}/messages`,
      json("POST", { content }, key),
    ),
  editProposal: (
    id: string,
    version: number,
    contacts: string[],
    operations: string[],
    fields: Record<string, string[]>,
  ) =>
    request<SalesProposal>(
      `/api/sales/tasks/${encodeURIComponent(id)}`,
      json("PATCH", {
        version,
        excluded_contact_ids: contacts,
        excluded_operation_ids: operations,
        excluded_fields: fields,
      }),
    ),
  decide: (id: string, version: number, decision: Decision, key: string) =>
    request<SalesProposal>(
      `/api/sales/tasks/${encodeURIComponent(id)}/decisions`,
      json("POST", { version, decision }, key),
    ),
  retry: (id: string) =>
    request<SalesProposal>(
      `/api/sales/tasks/${encodeURIComponent(id)}/retries`,
      json("POST"),
    ),
  automation: (update: Partial<AutomationUpdate>) =>
    request<SalesSnapshot["automation"]>(
      "/api/sales/automation",
      json("PATCH", update),
    ),
  cancelJob: (id: string) =>
    request<SalesJob>(
      `/api/sales/jobs/${encodeURIComponent(id)}/cancellation`,
      json("POST"),
    ),
  retryJob: (id: string) =>
    request<SalesJob>(
      `/api/sales/jobs/${encodeURIComponent(id)}/retries`,
      json("POST"),
    ),
  authSessions: () => request<AuthSession[]>("/api/auth/sessions"),
  revokeSession: (id: string) =>
    request<void>(
      `/api/auth/sessions/${encodeURIComponent(id)}`,
      json("DELETE"),
    ),
  revokeAllSessions: () => request<void>("/api/auth/sessions", json("DELETE")),
  logout: () => request<void>("/api/auth/logout", json("POST")),
};

/** Rendering untrusted evidence never permits executable URL schemes. */
export function evidenceUrl(
  value: string | null | undefined,
): string | undefined {
  try {
    const url = new URL(value ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
