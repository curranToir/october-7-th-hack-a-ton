import type {
  Meeting,
  MeetingCreate,
  MeetingImport,
  MeetingTask,
  MeetingWorkspace,
} from "./meeting-types.ts";

export class MeetingApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "MeetingApiError";
    this.status = status;
  }
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/meetings${path}`, {
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
    } catch { /* Gateway response may not be JSON. */ }
    throw new MeetingApiError(response.status,
      typeof detail === "string" ? detail
        : response.status === 401 ? "Sign in again to access your meetings."
          : response.status === 409 ? "This task changed. Review the latest version before deciding."
            : response.status === 422 ? "Check the meeting details and try again."
              : `The meeting request failed (${response.status}). Please try again.`,
    );
  }
  return response.status === 204 ? undefined as T : response.json();
}
const json = (method: string, body?: unknown, key?: string): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  ...(key ? { headers: { "Idempotency-Key": key } } : {}),
});
const taskPath = (id: string) => `/tasks/${encodeURIComponent(id)}`;
export const meetingClient = {
  workspace: (signal?: AbortSignal) => request<MeetingWorkspace>("/workspace", { signal }),
  create: (value: MeetingCreate, key: string) => request<Meeting>("", json("POST", value, key)),
  import: (value: MeetingImport, key: string) => request<Meeting>("/imports", json("POST", value, key)),
  demo: (key: string) => request<Meeting>("/demos", json("POST", {}, key)),
  retryMeeting: (id: string) => request<Meeting>(`/${encodeURIComponent(id)}/retries`, json("POST")),
  edit: (id: string, version: number, title: string, body: string) =>
    request<MeetingTask>(taskPath(id), json("PATCH", { version, title, body })),
  decide: (id: string, version: number, decision: "approve" | "reject") =>
    request<MeetingTask>(`${taskPath(id)}/decisions`, json("POST", { version, decision })),
  retryTask: (id: string) => request<MeetingTask>(`${taskPath(id)}/retries`, json("POST")),
  reconcile: (id: string) => request<MeetingTask>(`${taskPath(id)}/reconciliation`, json("POST")),
};

/** Plain text import: one speaker turn per line; optional [MM:SS] or [HH:MM:SS]. */
export function parseMeetingTranscript(text: string) {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error("Add at least one line of transcript.");
  if (lines.length > 2000 || text.length > 120000)
    throw new Error("Use at most 2,000 transcript lines and 120,000 characters.");
  return lines.map((line, index) => {
    const match = line.match(/^\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s*/);
    let start = 0;
    if (match) {
      const parts = match[1].split(":").map(Number);
      if (parts.slice(1).some(value => value > 59))
        throw new Error(`Check the timestamp on line ${index + 1}.`);
      start = parts.reduce((total, part) => total * 60 + part, 0);
      line = line.slice(match[0].length);
    }
    const speaker = line.match(/^([^:]{1,200}):\s+(.+)$/);
    const content = (speaker ? speaker[2] : line).trim();
    if (!content || content.length > 12000)
      throw new Error(`Line ${index + 1} must contain between 1 and 12,000 characters of speech.`);
    return { id: `import-${index + 1}`, speaker: speaker?.[1].trim() || "Speaker", start, text: content };
  });
}
