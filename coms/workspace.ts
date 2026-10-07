import type { Decision, Session, WorkspaceState } from "./types.ts";

export function newSession(agent: string): Session {
  return {
    id: crypto.randomUUID(),
    title: "New chat",
    agent: agent === "Auto" ? "Company Brain" : agent,
    createdAt: new Date().toISOString(),
    messages: [],
  };
}

/** Idempotent locally. The future API must enforce this atomically on the server. */
export function decideTask(
  state: WorkspaceState,
  id: string,
  decision: Decision,
): WorkspaceState {
  const task = state.tasks.find((item) => item.id === id);
  if (!task || task.status !== "pending") return state;
  return {
    ...state,
    tasks: state.tasks.map((item) =>
      item.id === id
        ? { ...item, status: decision, decidedAt: new Date().toISOString() }
        : item,
    ),
  };
}

/** Demo-only response. Never calls a model, CRM, or external action endpoint. */
export function sendMessage(
  state: WorkspaceState,
  sessionId: string,
  content: string,
): WorkspaceState {
  if (!content.trim()) return state;
  return {
    ...state,
    sessions: state.sessions.map((session) => {
      if (session.id !== sessionId) return session;
      const now = new Date().toISOString();
      return {
        ...session,
        title: session.messages.length
          ? session.title
          : content.trim().slice(0, 48),
        messages: [
          ...session.messages,
          {
            id: crypto.randomUUID(),
            role: "user" as const,
            content: content.trim(),
            createdAt: now,
          },
          {
            id: crypto.randomUUID(),
            role: "assistant" as const,
            content:
              "Your message is saved in this demo conversation. Live agent responses aren’t available yet. You can explore the example sessions or review the tasks in your queue.",
            createdAt: now,
          },
        ],
      };
    }),
  };
}
