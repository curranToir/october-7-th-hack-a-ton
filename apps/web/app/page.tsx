"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SettingPage } from "../../../coms/types.ts";
import { useWorkspace } from "../components/use-workspace";
import { Sidebar } from "../components/sidebar";
import { Chat } from "../components/chat";
import { Tasks } from "../components/tasks";
import { Settings, settingsPages } from "../components/settings";
import { Icon } from "../components/icon";
import { Logo } from "../components/logo";
import { Dialog } from "../components/dialog";
import { Meetings } from "../components/meetings";
import { useMeetings } from "../components/use-meetings";
import { EngineeringWorkspace } from "../components/engineering-workspace";

type Route = { view: "chat" | "tasks" | "meetings" | "settings"; id: string };
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
  if (view === "tasks") return { view, id: id === "meetings" ? id : "" };
  if (view === "meetings") return { view, id };
  return { view: "chat", id };
}
export default function Home() {
  const actions = useWorkspace();
  const { state, restrictedUser, warning, clearWarning, unauthorized, loading } = actions;
  const meetingActions = useMeetings(!!state && !unauthorized);
  const [route, setRoute] = useState<Route>({ view: "chat", id: "" });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<"help" | "rename" | "delete" | null>(
    null,
  );
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const creating = useRef(false);
  const content = useRef<HTMLElement>(null);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const navigate = useCallback((view: Route["view"], id = "") => {
    window.location.hash = `${view}${id ? `/${encodeURIComponent(id)}` : ""}`;
    setRoute({ view, id });
    setSidebarOpen(false);
  }, []);
  const startChat = useCallback(async () => {
    if (!state || creating.current) return;
    const existing = state.sessions.find(
      (s) => !s.messages.length && s.agent !== "Continuous prospecting",
    );
    if (existing) {
      navigate("chat", existing.id);
      return;
    }
    creating.current = true;
    try {
      const created = await actions.createSession();
      navigate("chat", created.id);
    } catch {
      /* The workspace reports API failures. */
    } finally {
      creating.current = false;
    }
  }, [state, actions, navigate]);
  useEffect(() => {
    const read = () => setRoute(readRoute());
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k" &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        void startChat();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [startChat]);
  useEffect(() => {
    if (content.current) content.current.inert = sidebarOpen;
  }, [sidebarOpen]);
  useEffect(() => {
    content.current?.scrollTo({ top: 0 });
  }, [route.view, route.id]);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 701px)");
    const close = () => {
      if (desktop.matches) setSidebarOpen(false);
    };
    desktop.addEventListener("change", close);
    return () => desktop.removeEventListener("change", close);
  }, []);
  if (unauthorized)
    return (
      <main className="loading-workspace sign-in-panel">
        <Logo size={48} alt="TOIR" />
        <h1>Your Toir workspace</h1>
        <p>
          Sign in with your work account. Your department determines which
          workspace and memories you can access.
        </p>
        <a className="button primary" href="/api/auth/login">
          Sign in with Google <Icon name="arrow" size={17} />
        </a>
        <small>Access is limited to authorized Toir members.</small>
      </main>
    );
  if (restrictedUser)
    return (
      <EngineeringWorkspace
        user={restrictedUser}
        warning={warning}
        onRefresh={() => actions.refresh(true)}
        onSignOut={actions.logout}
      />
    );
  if (!state)
    return (
      <main className="loading-workspace" role="status">
        <Logo size={42} alt="TOIR" />
        <span>
          {loading
            ? "Opening your workspace…"
            : "The sales workspace could not be loaded."}
        </span>
        {!loading && (
          <>
            <p className="sales-error">{warning}</p>
            <button
              className="button secondary"
              onClick={() => void actions.refresh()}
            >
              Try again
            </button>
          </>
        )}
      </main>
    );
  const session =
    state.sessions.find((item) => item.id === route.id) ?? state.sessions[0];
  const settingsPage = (
    settingsPages.some((item) => item.id === route.id) ? route.id : "general"
  ) as SettingPage;
  const openDialog = (value: typeof dialog) => {
    setDialogError("");
    setDialog(value);
  };
  const dialogAction = async (action: () => Promise<unknown>) => {
    setDialogBusy(true);
    setDialogError("");
    try {
      await action();
      setDialog(null);
    } catch (error) {
      setDialogError(
        error instanceof Error ? error.message : "Request failed.",
      );
    } finally {
      setDialogBusy(false);
    }
  };
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
        onMeetings={() => navigate("meetings")}
        meetingPending={meetingActions.state?.tasks.filter(task => task.status === "pending").length ?? 0}
        onSettings={(page = "general") => navigate("settings", page)}
        onNew={() => void startChat()}
        onHelp={() => {
          setSidebarOpen(false);
          openDialog("help");
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
                : route.view === "meetings"
                  ? "Meetings"
                : route.view === "settings"
                  ? "Settings"
                  : (session?.title ?? "New chat")}
            </h1>
            <p>
              {route.view === "tasks"
                ? "Review what your agents found."
                : route.view === "meetings"
                  ? "Customer conversations, notes, and follow-through."
                : route.view === "settings"
                  ? "Manage your workspace and agent preferences."
                  : (session?.agent ?? "TOIR research")}
            </p>
          </div>
          <div className="header-actions">
            <button
              className="automation-indicator"
              onClick={() => navigate("settings", "agents")}
              title="Open automation settings"
            >
              <span
                className={
                  state.automation.enabled && state.capabilities.ready
                    ? "running"
                    : ""
                }
              />
              {state.automation.enabled
                ? state.capabilities.ready
                  ? "Prospecting active"
                  : "Awaiting readiness"
                : "Prospecting paused"}
            </button>
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
                      openDialog("rename");
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
                      openDialog("delete");
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
              aria-label="Dismiss warning"
              onClick={clearWarning}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        )}
        {route.view === "chat" && (
          <Chat
            state={state}
            session={session}
            actions={actions}
            onSend={async (message) => {
              const target = session ?? (await actions.createSession());
              if (!session) navigate("chat", target.id);
              await actions.send(target.id, message);
            }}
          />
        )}
        {route.view === "tasks" && (
          <Tasks
            key={route.id}
            state={state}
            actions={actions}
            onSession={(id) => navigate("chat", id)}
            meetingActions={meetingActions}
            initialTab={route.id === "meetings" ? "meetings" : "pending"}
            onMeeting={(id) => navigate("meetings", id)}
          />
        )}
        {route.view === "meetings" && (
          <Meetings actions={meetingActions} selectedId={route.id}
            onSelect={(id) => navigate("meetings", id)} onTasks={() => navigate("tasks", "meetings")} />
        )}
        {route.view === "settings" && (
          <Settings
            key={settingsPage}
            page={settingsPage}
            state={state}
            actions={actions}
            navigate={(page) => navigate("settings", page)}
          />
        )}
      </main>
      {dialog === "help" && (
        <Dialog
          title="A little help getting started"
          onClose={() => setDialog(null)}
        >
          <div className="help-intro">
            <Logo size={38} alt="TOIR" />
            <p>
              One workspace for company research and the decisions that need
              you.
            </p>
          </div>
          <div className="help-steps">
            <p>
              <strong>Start a conversation</strong>Use New chat or press ⌘ K /
              Ctrl K. Ask for company research, or ask to research a company and
              add it to your CRM.
            </p>
            <p>
              <strong>Review findings</strong>Open Tasks or the original session
              to inspect contacts, citations, and exact CRM changes. Save
              exclusions as a new proposal version before approving.
            </p>
            <p>
              <strong>Follow execution</strong>An approval is recorded before
              the CRM work runs. Execution status and retries remain in the
              session and task history.
            </p>
            <p>
              <strong>Bring an agent to your call</strong>Open Meetings to add
              a Zoom call or try the customer issue demo. Review notes and
              mentioned companies, then approve an engineering issue from
              Tasks → Meeting issues.
            </p>
            <p>
              <strong>Configure continuous work</strong>Settings → Agents &
              automation controls targeting, quotas, and pause state. Chat
              requests take the next available worker slot.
            </p>
          </div>
        </Dialog>
      )}
      {dialog === "rename" && session && (
        <Dialog
          title="Rename session"
          onClose={() => {
            if (!dialogBusy) setDialog(null);
          }}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const title = String(
                new FormData(event.currentTarget).get("title") ?? "",
              ).trim();
              if (title)
                void dialogAction(() =>
                  actions.renameSession(session.id, title),
                );
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
                disabled={dialogBusy}
              />
            </label>
            {dialogError && (
              <p className="sales-error" role="alert">
                {dialogError}
              </p>
            )}
            <div className="dialog-actions">
              <button
                className="button secondary"
                type="button"
                disabled={dialogBusy}
                onClick={() => setDialog(null)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                type="submit"
                disabled={dialogBusy}
              >
                {dialogBusy ? "Saving…" : "Save name"}
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {dialog === "delete" && session && (
        <Dialog
          title="Delete this session?"
          onClose={() => {
            if (!dialogBusy) setDialog(null);
          }}
        >
          <p>
            “{session.title}” will be removed from the workspace’s session list.
            Related tasks, decisions, and CRM audit records are kept.
          </p>
          {dialogError && (
            <p className="sales-error" role="alert">
              {dialogError}
            </p>
          )}
          <div className="dialog-actions">
            <button
              className="button secondary"
              disabled={dialogBusy}
              onClick={() => setDialog(null)}
            >
              Cancel
            </button>
            <button
              className="button danger"
              disabled={dialogBusy}
              onClick={() =>
                void dialogAction(async () => {
                  await actions.deleteSession(session.id);
                  navigate("chat");
                })
              }
            >
              {dialogBusy ? "Deleting…" : "Delete session"}
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
