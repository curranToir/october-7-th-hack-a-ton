import { useRef, useEffect } from "react";
import type { WorkspaceState } from "../../../coms/types.ts";
import { Icon } from "./icon";
import { Logo } from "./logo";

export function Sidebar({
  state,
  view,
  sessionId,
  onChat,
  onTasks,
  onSettings,
  onNew,
  onHelp,
  open,
  onClose,
  search,
  setSearch,
  searching,
  setSearching,
}: {
  state: WorkspaceState;
  view: string;
  sessionId: string;
  open: boolean;
  onClose: () => void;
  onChat: (id: string) => void;
  onTasks: () => void;
  onSettings: (page?: string) => void;
  onNew: () => void;
  onHelp: () => void;
  search: string;
  setSearch: (value: string) => void;
  searching: boolean;
  setSearching: (value: boolean) => void;
}) {
  const searchRef = useRef<HTMLInputElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (searching) searchRef.current?.focus();
  }, [searching]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement;
    sidebarRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const elements = [
        ...(sidebarRef.current?.querySelectorAll<HTMLElement>(
          "button, input, a[href]",
        ) ?? []),
      ].filter((el) => el.getClientRects().length);
      const first = elements[0],
        last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [open, onClose]);
  const pending = state.tasks.filter(
    (task) => task.status === "pending",
  ).length;
  const sessions = state.sessions.filter((session) =>
    `${session.title} ${session.messages.map((m) => m.content).join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const initials =
    state.profile.name
      .trim()
      .split(/\s+/)
      .map((n) => n[0])
      .slice(0, 2)
      .join("") || "U";
  return (
    <>
      {open && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={onClose}
          tabIndex={-1}
        />
      )}
      <aside
        ref={sidebarRef}
        className={`sidebar ${open ? "is-open" : ""} ${state.preferences.compactSidebar ? "is-compact" : ""}`}
        aria-label="Workspace navigation"
      >
        <div className="brand-row">
          <button
            className="brand"
            onClick={onNew}
            aria-label="TOIR — New chat"
            title={state.workspace.name}
          >
            <Logo variant="wordmark" size={98} />
          </button>
          <button
            className="icon-button mobile-close"
            aria-label="Close navigation"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        <nav className="primary-nav" aria-label="Main navigation">
          <a className="nav-item" href="/research">
            <Icon name="search" />
            <span>Research</span>
          </a>
          <button
            className={`nav-item ${view === "tasks" ? "active" : ""}`}
            onClick={onTasks}
            aria-current={view === "tasks" ? "page" : undefined}
          >
            <Icon name="tasks" />
            <span>Tasks</span>
            {pending > 0 && <span className="count-badge">{pending}</span>}
          </button>
          <button className="nav-item" onClick={onNew}>
            <Icon name="plus" />
            <span>New chat</span>
            <kbd>⌘ K</kbd>
          </button>
          <button
            className={`nav-item ${searching ? "selected" : ""}`}
            onClick={() => setSearching(!searching)}
            aria-expanded={searching}
          >
            <Icon name="search" />
            <span>Search sessions</span>
          </button>
        </nav>
        <div className="session-list">
          <div className="sidebar-section-label">
            Sessions <span>{state.sessions.length}</span>
          </div>
          {searching && (
            <div className="session-search">
              <Icon name="search" size={16} />
              <input
                ref={searchRef}
                aria-label="Search sessions"
                placeholder="Search conversations…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setSearching(false);
                    setSearch("");
                  }
                }}
              />
              <button
                className="icon-button"
                aria-label="Clear search"
                onClick={() => {
                  setSearch("");
                  setSearching(false);
                }}
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          )}
          <nav aria-label="Sessions">
            {sessions.map((session) => (
              <button
                key={session.id}
                className={`nav-item session-item ${view === "chat" && sessionId === session.id ? "active" : ""}`}
                aria-current={
                  view === "chat" && sessionId === session.id
                    ? "page"
                    : undefined
                }
                onClick={() => onChat(session.id)}
                title={session.title}
              >
                <Icon name="chat" size={18} />
                <span>{session.title}</span>
              </button>
            ))}
          </nav>
          {!sessions.length && (
            <p className="sidebar-empty">
              {search
                ? "No matching sessions."
                : "Your conversations will appear here."}
            </p>
          )}
        </div>
        <div className="sidebar-bottom">
          <button
            className={`nav-item ${view === "settings" ? "active" : ""}`}
            aria-current={view === "settings" ? "page" : undefined}
            onClick={() => onSettings()}
          >
            <Icon name="settings" />
            <span>Settings</span>
          </button>
          <button className="nav-item" onClick={onHelp}>
            <Icon name="help" />
            <span>Help & feedback</span>
          </button>
          <button
            className="profile-button"
            onClick={() => onSettings("profile")}
          >
            <span className="avatar">{initials}</span>
            <span className="profile-label">
              <strong>{state.profile.name}</strong>
              <small>Personal workspace</small>
            </span>
            <Icon name="chevron" size={16} />
          </button>
        </div>
      </aside>
    </>
  );
}
