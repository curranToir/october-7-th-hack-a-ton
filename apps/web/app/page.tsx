"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  decideTask,
  newSession,
  sendMessage,
} from "../../../coms/workspace.ts";
import type { SettingPage } from "../../../coms/types.ts";
import { useWorkspace } from "../components/use-workspace";
import { Sidebar } from "../components/sidebar";
import { Chat } from "../components/chat";
import { Tasks } from "../components/tasks";
import { Settings, settingsPages } from "../components/settings";
import { Icon } from "../components/icon";
import { Dialog } from "../components/dialog";

type Route = { view: "chat" | "tasks" | "settings"; id: string };
function readRoute(): Route {
  const [view, encodedId = ""] = window.location.hash.slice(1).split("/");
  let id = "";
  try {
    id = decodeURIComponent(encodedId);
  } catch {
    /* Ignore malformed bookmarks. */
  }
  if (view === "settings")
    return {
      view,
      id: settingsPages.some((item) => item.id === id) ? id : "general",
    };
  if (view === "tasks") return { view, id: "" };
  return { view: "chat", id };
}

export default function Home() {
  const { state, update, warning, clearWarning } = useWorkspace();
  const [route, setRoute] = useState<Route>({ view: "chat", id: "" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<"help" | "rename" | "delete" | null>(
    null,
  );
  const [notification, setNotification] = useState("");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const content = useRef<HTMLElement>(null);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const toast = useCallback((message: string) => {
    setNotification(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setNotification(""), 4200);
  }, []);
  useEffect(() => {
    const read = () => setRoute(readRoute());
    read();
    window.addEventListener("hashchange", read);
    return () => {
      window.removeEventListener("hashchange", read);
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);
  const navigate = useCallback((view: Route["view"], id = "") => {
    window.location.hash = `${view}${id ? `/${encodeURIComponent(id)}` : ""}`;
    setRoute({ view, id });
    setSidebarOpen(false);
  }, []);
  const startChat = useCallback(() => {
    if (!state) return;
    const existing = state.sessions.find((session) => !session.messages.length);
    if (existing) {
      navigate("chat", existing.id);
      return;
    }
    const session = newSession(state.preferences.defaultAgent);
    update((current) => ({
      ...current,
      sessions: [session, ...current.sessions],
    }));
    navigate("chat", session.id);
  }, [state, update, navigate]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k" &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        startChat();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [startChat]);
  useEffect(() => {
    if (content.current) content.current.inert = sidebarOpen;
  }, [sidebarOpen]);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 701px)");
    const close = () => {
      if (desktop.matches) setSidebarOpen(false);
    };
    desktop.addEventListener("change", close);
    return () => desktop.removeEventListener("change", close);
  }, []);
  if (!state)
    return (
      <div className="loading-workspace" role="status">
        <Icon name="brain" size={34} />
        <span>Opening your workspace…</span>
      </div>
    );
  const session =
    state.sessions.find((item) => item.id === route.id) ?? state.sessions[0];
  const settingsPage = (
    settingsPages.some((item) => item.id === route.id) ? route.id : "general"
  ) as SettingPage;
  const pending = state.tasks.filter(
    (task) => task.status === "pending",
  ).length;
  return (
    <div
      className={`app-shell ${state.preferences.compactSidebar ? "compact-layout" : ""}`}
    >
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          content.current?.focus();
        }}
      >
        Skip to content
      </a>
      <Sidebar
        state={state}
        view={route.view}
        sessionId={session?.id ?? ""}
        onChat={(id) => navigate("chat", id)}
        onTasks={() => navigate("tasks")}
        onSettings={(page = "general") => navigate("settings", page)}
        onNew={startChat}
        onHelp={() => {
          setSidebarOpen(false);
          setDialog("help");
        }}
        open={sidebarOpen}
        onClose={closeSidebar}
        search={search}
        setSearch={setSearch}
        searching={searching}
        setSearching={(value) => {
          setSearching(value);
          if (!value) setSearch("");
        }}
      />
      <main
        ref={content}
        className={`main-workspace view-${route.view}`}
        id="main-content"
        tabIndex={-1}
      >
        <header className="workspace-header">
          <button
            className="icon-button sidebar-toggle"
            aria-label="Open navigation"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
          >
            <Icon name="panel" />
          </button>
          <div className="header-title">
            <h1>
              {route.view === "tasks"
                ? "Tasks"
                : route.view === "settings"
                  ? "Settings"
                  : (session?.title ?? "New chat")}
            </h1>
            <p>
              {route.view === "tasks"
                ? "Review what your agents found."
                : route.view === "settings"
                  ? "Manage your workspace and agent preferences."
                  : (session?.agent ?? "Company Brain")}
            </p>
          </div>
          <div className="header-actions">
            <span className="demo-label">
              <span />
              Demo workspace
            </span>
            {route.view === "chat" && session && (
              <details className="session-menu" key={session.id}>
                <summary className="icon-button" aria-label="Session options">
                  <Icon name="more" />
                </summary>
                <div className="popover">
                  <button
                    onClick={(event) => {
                      event.currentTarget
                        .closest("details")
                        ?.removeAttribute("open");
                      setDialog("rename");
                    }}
                  >
                    <Icon name="edit" size={16} />
                    Rename session
                  </button>
                  <button
                    className="danger-text"
                    onClick={(event) => {
                      event.currentTarget
                        .closest("details")
                        ?.removeAttribute("open");
                      setDialog("delete");
                    }}
                  >
                    <Icon name="trash" size={16} />
                    Delete session
                  </button>
                </div>
              </details>
            )}
          </div>
        </header>
        {warning && (
          <div className="storage-warning" role="alert">
            <Icon name="info" size={18} />
            <span>{warning}</span>
            <button
              className="icon-button"
              aria-label="Dismiss storage warning"
              onClick={clearWarning}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        )}
        {route.view === "chat" && (
          <Chat
            key={session?.id ?? "empty"}
            state={state}
            session={session}
            onTasks={() => navigate("tasks")}
            onSend={(message) => {
              const target =
                session ?? newSession(state.preferences.defaultAgent);
              update((current) =>
                sendMessage(
                  session
                    ? current
                    : { ...current, sessions: [target, ...current.sessions] },
                  target.id,
                  message,
                ),
              );
              if (!session) navigate("chat", target.id);
            }}
          />
        )}
        {route.view === "tasks" && (
          <Tasks
            state={state}
            onSession={(id) => navigate("chat", id)}
            onDecide={(id, decision) => {
              update((current) => decideTask(current, id, decision));
              toast(
                decision === "approved"
                  ? "Approval recorded. No external action was taken in this demo."
                  : "Task denied. No changes were made to connected tools.",
              );
            }}
          />
        )}
        {route.view === "settings" && (
          <Settings
            key={settingsPage}
            page={settingsPage}
            state={state}
            update={update}
            navigate={(page) => navigate("settings", page)}
            toast={toast}
            onReset={() => navigate("chat", "prospects")}
            storageAvailable={!warning}
          />
        )}
      </main>
      <div className="toast-region" role="status" aria-live="polite">
        {notification && (
          <div className="toast">
            <span className="toast-icon">
              <Icon name="check" size={16} />
            </span>
            <span>{notification}</span>
            <button
              className="icon-button"
              aria-label="Dismiss notification"
              onClick={() => setNotification("")}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        )}
      </div>
      {dialog === "help" && (
        <Dialog
          title="A little help getting started"
          onClose={() => setDialog(null)}
        >
          <div className="help-intro">
            <Icon name="brain" size={32} />
            <p>
              One workspace for conversations with your agents and the decisions
              that need you.
            </p>
          </div>
          <div className="help-steps">
            <p>
              <strong>Start a conversation</strong>Use New chat or press ⌘ K /
              Ctrl K. Your sessions stay in the sidebar.
            </p>
            <p>
              <strong>Review agent findings</strong>Open Tasks to see the
              evidence and proposed action. Approve or deny, then move to the
              next item.
            </p>
            <p>
              <strong>Make it yours</strong>Switch between light, dark and
              system themes in Settings → General.
            </p>
          </div>
          <div className="notice">
            <Icon name="info" />
            <p>
              This is a local demo with {pending} pending{" "}
              {pending === 1 ? "task" : "tasks"}. No real agent or CRM actions
              run. For feedback, share your notes with your workspace team.
            </p>
          </div>
        </Dialog>
      )}
      {dialog === "rename" && session && (
        <Dialog title="Rename session" onClose={() => setDialog(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const title = String(
                new FormData(event.currentTarget).get("title") ?? "",
              ).trim();
              if (!title) return;
              update((current) => ({
                ...current,
                sessions: current.sessions.map((item) =>
                  item.id === session.id ? { ...item, title } : item,
                ),
              }));
              setDialog(null);
              toast("Session renamed.");
            }}
          >
            <label className="form-label">
              Session name
              <input
                name="title"
                defaultValue={session.title}
                required
                maxLength={80}
                autoFocus
              />
            </label>
            <div className="dialog-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() => setDialog(null)}
              >
                Cancel
              </button>
              <button className="button primary" type="submit">
                Save name
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {dialog === "delete" && session && (
        <Dialog title="Delete this session?" onClose={() => setDialog(null)}>
          <p>
            “{session.title}” will be removed from this browser. Related task
            decisions will be kept. This cannot be undone.
          </p>
          <div className="dialog-actions">
            <button
              className="button secondary"
              onClick={() => setDialog(null)}
            >
              Cancel
            </button>
            <button
              className="button danger"
              onClick={() => {
                update((current) => ({
                  ...current,
                  sessions: current.sessions.filter(
                    (item) => item.id !== session.id,
                  ),
                }));
                setDialog(null);
                navigate("chat");
                toast("Session deleted.");
              }}
            >
              Delete session
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
