import { createDemoWorkspace } from "./seed.ts";
import type { ReadResult, StoragePort, WorkspaceState } from "./types.ts";

export const STORAGE_KEY = "company-brain.workspace.v1";
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const strings = (value: Record<string, unknown>, keys: string[]) =>
  keys.every((key) => typeof value[key] === "string");
const stringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");
const validDate = (value: unknown) =>
  typeof value === "string" && Number.isFinite(Date.parse(value));

function validState(value: unknown): value is WorkspaceState {
  if (
    !record(value) ||
    value.version !== 1 ||
    !record(value.profile) ||
    !record(value.workspace) ||
    !record(value.preferences)
  )
    return false;
  if (
    !strings(value.profile, ["name", "email", "role"]) ||
    !strings(value.workspace, ["name", "description"])
  )
    return false;
  const prefs = value.preferences;
  if (
    !["light", "dark", "system"].includes(String(prefs.theme)) ||
    !strings(prefs, ["timeZone", "defaultAgent"])
  )
    return false;
  if (
    ![
      "compactSidebar",
      "autoAdvance",
      "requireApproval",
      "notifyTasks",
      "notifyCompleted",
      "sound",
      "saveHistory",
    ].every((key) => typeof prefs[key] === "boolean")
  )
    return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: String(prefs.timeZone) });
  } catch {
    return false;
  }
  if (
    !Array.isArray(value.sessions) ||
    !value.sessions.every(
      (session) =>
        record(session) &&
        strings(session, ["id", "title", "agent", "createdAt"]) &&
        Array.isArray(session.messages) &&
        session.messages.every(
          (message) =>
            record(message) &&
            strings(message, ["id", "content", "createdAt"]) &&
            ["user", "assistant"].includes(String(message.role)) &&
            (message.taskIds === undefined || stringArray(message.taskIds)),
        ),
    )
  )
    return false;
  if (
    !Array.isArray(value.tasks) ||
    !value.tasks.every(
      (task) =>
        record(task) &&
        strings(task, [
          "id",
          "sessionId",
          "agent",
          "title",
          "subject",
          "subtitle",
          "finding",
          "source",
          "sourceDetail",
          "scope",
          "restriction",
          "approveLabel",
          "createdAt",
        ]) &&
        stringArray(task.steps) &&
        ["pending", "approved", "denied"].includes(String(task.status)) &&
        (task.decidedAt === undefined || validDate(task.decidedAt)),
    )
  )
    return false;
  return (
    Array.isArray(value.connections) &&
    value.connections.every(
      (item) =>
        record(item) &&
        strings(item, ["id", "name", "description", "initials"]) &&
        typeof item.connected === "boolean",
    )
  );
}

export function readWorkspace(storage: StoragePort): ReadResult {
  try {
    const saved = storage.getItem(STORAGE_KEY);
    if (!saved) return { state: createDemoWorkspace() };
    const parsed: unknown = JSON.parse(saved);
    if (validState(parsed)) return { state: parsed };
    return {
      state: createDemoWorkspace(),
      warning:
        "Saved demo data could not be read. A fresh demo has been opened.",
    };
  } catch {
    return {
      state: createDemoWorkspace(),
      warning:
        "Browser storage is unavailable. Changes will last for this visit only.",
    };
  }
}

export function saveWorkspace(
  storage: StoragePort,
  state: WorkspaceState,
): boolean {
  try {
    // Disabling history also removes previously persisted conversations immediately.
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...state,
        sessions: state.preferences.saveHistory ? state.sessions : [],
      }),
    );
    return true;
  } catch {
    return false;
  }
}

/** The only browser persistence boundary. Replace this object with the future API adapter. */
export const workspaceClient = {
  read(): ReadResult {
    try {
      return readWorkspace(window.localStorage);
    } catch {
      return {
        state: createDemoWorkspace(),
        warning:
          "Browser storage is unavailable. Changes will last for this visit only.",
      };
    }
  },
  save(state: WorkspaceState): boolean {
    try {
      return saveWorkspace(window.localStorage, state);
    } catch {
      return false;
    }
  },
  export(state: WorkspaceState): string {
    return JSON.stringify(state, null, 2);
  },
};
