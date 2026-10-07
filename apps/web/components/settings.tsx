"use client";

import { useState, type ReactNode } from "react";
import type {
  Preferences,
  SettingPage,
  WorkspaceState,
} from "../../../coms/types.ts";
import { workspaceClient } from "../../../coms/storage.ts";
import { createDemoWorkspace } from "../../../coms/seed.ts";
import type { UpdateWorkspace } from "./use-workspace";
import { Icon, type IconName } from "./icon";
import { Dialog } from "./dialog";

export const settingsPages: {
  id: SettingPage;
  label: string;
  icon: IconName;
}[] = [
  { id: "general", label: "General", icon: "general" },
  { id: "profile", label: "Profile", icon: "profile" },
  { id: "agents", label: "Agents & permissions", icon: "brain" },
  { id: "connections", label: "Connected apps", icon: "connections" },
  { id: "notifications", label: "Notifications", icon: "notifications" },
  { id: "privacy", label: "Data & privacy", icon: "shield" },
  { id: "workspace", label: "Workspace", icon: "workspace" },
  { id: "billing", label: "Billing", icon: "billing" },
];

function Row({
  label,
  detail,
  children,
}: {
  label: string;
  detail?: string;
  children: ReactNode;
}) {
  return (
    <div className="setting-row">
      <div className="setting-row-label">
        <span>{label}</span>
        {detail && <p>{detail}</p>}
      </div>
      <div className="setting-control">{children}</div>
    </div>
  );
}
function Toggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="toggle"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export function Settings({
  page,
  state,
  update,
  navigate,
  toast,
  onReset,
  storageAvailable,
}: {
  page: SettingPage;
  state: WorkspaceState;
  update: UpdateWorkspace;
  navigate: (page: string) => void;
  toast: (message: string) => void;
  onReset: () => void;
  storageAvailable: boolean;
}) {
  const [confirm, setConfirm] = useState<"history" | "reset" | null>(null);
  const [connection, setConnection] = useState<string | null>(null);
  const prefs = state.preferences;
  const setPreference = <K extends keyof Preferences>(
    key: K,
    value: Preferences[K],
  ) =>
    update((current) => ({
      ...current,
      preferences: { ...current.preferences, [key]: value },
    }));
  const toggle = (key: keyof Preferences, label: string) => (
    <Toggle
      label={label}
      checked={!!prefs[key]}
      onChange={(value) => setPreference(key, value)}
    />
  );
  const activeConnection = state.connections.find(
    (item) => item.id === connection,
  );
  const exportData = () => {
    const url = URL.createObjectURL(
      new Blob([workspaceClient.export(state)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "toir-export.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Workspace export downloaded.");
  };
  return (
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings categories">
        {settingsPages.map((item) => (
          <button
            key={item.id}
            className={page === item.id ? "active" : ""}
            aria-current={page === item.id ? "page" : undefined}
            onClick={() => navigate(item.id)}
          >
            <Icon name={item.icon} size={17} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="settings-content" key={page}>
        <h2>{settingsPages.find((item) => item.id === page)?.label}</h2>
        {page === "general" && (
          <>
            <Section title="Appearance">
              <Row label="Theme">
                <div className="theme-switch" role="group" aria-label="Theme">
                  {(["light", "dark", "system"] as const).map((theme) => (
                    <button
                      key={theme}
                      aria-pressed={prefs.theme === theme}
                      onClick={() => setPreference("theme", theme)}
                    >
                      <Icon
                        name={
                          theme === "light"
                            ? "sun"
                            : theme === "dark"
                              ? "moon"
                              : "monitor"
                        }
                        size={16}
                      />
                      {theme[0].toUpperCase() + theme.slice(1)}
                    </button>
                  ))}
                </div>
              </Row>
              <Row
                label="Compact sidebar"
                detail="A little less space between your sessions."
              >
                {toggle("compactSidebar", "Compact sidebar")}
              </Row>
            </Section>
            <Section title="Language & region">
              <Row label="Language">
                <span className="static-control">
                  English <small>More languages soon</small>
                </span>
              </Row>
              <Row label="Time zone">
                <select
                  aria-label="Time zone"
                  value={prefs.timeZone}
                  onChange={(event) =>
                    setPreference("timeZone", event.target.value)
                  }
                >
                  {[
                    "America/Los_Angeles",
                    "America/New_York",
                    "Europe/London",
                    "Europe/Paris",
                    "Asia/Tokyo",
                    "Australia/Sydney",
                    "UTC",
                  ].map((zone) => (
                    <option key={zone}>{zone}</option>
                  ))}
                </select>
              </Row>
            </Section>
            <Section title="Chat & tasks">
              <Row
                label="Default agent"
                detail="Used when you start a new conversation."
              >
                <select
                  aria-label="Default agent"
                  value={prefs.defaultAgent}
                  onChange={(event) =>
                    setPreference("defaultAgent", event.target.value)
                  }
                >
                  {[
                    "Auto",
                    "Prospecting agent",
                    "Pipeline agent",
                    "Customer agent",
                    "Research agent",
                  ].map((agent) => (
                    <option key={agent}>{agent}</option>
                  ))}
                </select>
              </Row>
              <Row
                label="Open next task after a decision"
                detail="Keep reviewing without returning to the queue."
              >
                {toggle("autoAdvance", "Open next task after a decision")}
              </Row>
              <Row
                label="Require approval for external actions"
                detail="Ask before updating connected tools."
              >
                {toggle(
                  "requireApproval",
                  "Require approval for external actions",
                )}
              </Row>
            </Section>
          </>
        )}
        {page === "profile" && (
          <form
            className="settings-form"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const name = String(data.get("name") ?? "").trim();
              if (!name) return;
              const saved = update((current) => ({
                ...current,
                profile: {
                  ...current.profile,
                  name,
                  email: String(data.get("email") ?? "").trim(),
                },
              }));
              toast(
                saved
                  ? "Profile saved on this device."
                  : "Profile updated for this visit only.",
              );
            }}
          >
            <div className="profile-preview">
              <span className="avatar avatar-large">
                {state.profile.name
                  .split(/\s+/)
                  .map((word) => word[0])
                  .slice(0, 2)
                  .join("")}
              </span>
              <div>
                <strong>{state.profile.name}</strong>
                <p>{state.profile.role}</p>
              </div>
            </div>
            <label>
              Full name
              <input
                name="name"
                defaultValue={state.profile.name}
                required
                maxLength={60}
                autoComplete="name"
                pattern=".*\S.*"
              />
            </label>
            <label>
              Email address
              <input
                type="email"
                name="email"
                defaultValue={state.profile.email}
                required
                autoComplete="email"
                maxLength={120}
              />
            </label>
            <p className="field-hint">
              Your demo profile is saved on this device. Account sign-in will be
              available later.
            </p>
            <button className="button primary" type="submit">
              Save profile
            </button>
          </form>
        )}
        {page === "agents" && (
          <>
            <p className="settings-intro">
              Decide how your agents work with your tools.
            </p>
            <div className="notice">
              <Icon name="shield" />
              <p>
                These preferences are saved for your workspace. In this demo,
                every external action stays in the approval queue and no tools
                are changed.
              </p>
            </div>
            <Section title="Approval preferences">
              <Row
                label="Require approval for external actions"
                detail="Review proposed changes before an agent carries them out."
              >
                {toggle(
                  "requireApproval",
                  "Require approval for external actions",
                )}
              </Row>
              <Row
                label="Open next task after a decision"
                detail="Move to the next pending item automatically."
              >
                {toggle("autoAdvance", "Open next task after a decision")}
              </Row>
            </Section>
            <Section title="Agents">
              {[
                "Prospecting agent",
                "Pipeline agent",
                "Customer agent",
                "Research agent",
              ].map((agent) => (
                <Row
                  key={agent}
                  label={agent}
                  detail={
                    agent === "Prospecting agent"
                      ? "Find contacts and research companies"
                      : agent === "Pipeline agent"
                        ? "Review opportunities and meeting notes"
                        : agent === "Customer agent"
                          ? "Prepare customer follow-ups"
                          : "Explore your company knowledge"
                  }
                >
                  <span className="muted-tag">Demo agent</span>
                </Row>
              ))}
            </Section>
          </>
        )}
        {page === "connections" && (
          <>
            <p className="settings-intro">
              Give your agents context from the tools you use.
            </p>
            <div className="notice">
              <Icon name="info" />
              <p>
                Demo connections only. No account access is granted and no
                external data is read or changed.
              </p>
            </div>
            <div className="connections">
              {state.connections.map((item) => (
                <div className="connection-row" key={item.id}>
                  <span className={`app-icon app-${item.id}`}>
                    <Icon
                      name={
                        item.id === "hubspot"
                          ? "crm"
                          : item.id === "slack"
                            ? "messages"
                            : "files"
                      }
                      size={21}
                    />
                  </span>
                  <div className="connection-copy">
                    <strong>{item.name}</strong>
                    <p>{item.description}</p>
                    <span
                      className={
                        item.connected
                          ? "connection-status connected"
                          : "connection-status"
                      }
                    >
                      {item.connected ? "Demo connected" : "Not connected"}
                    </span>
                  </div>
                  <button
                    className="button secondary"
                    onClick={() => setConnection(item.id)}
                  >
                    {item.connected ? "Manage" : "Connect"}
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
        {page === "notifications" && (
          <>
            <p className="settings-intro">
              Choose the updates you want from your agents.
            </p>
            <div className="notice">
              <Icon name="info" />
              <p>
                Preferences are saved now. Notifications will become available
                when live agents are connected.
              </p>
            </div>
            <Section title="Agent activity">
              <Row
                label="Tasks awaiting approval"
                detail="When an agent has an action ready for your review."
              >
                {toggle("notifyTasks", "Tasks awaiting approval notifications")}
              </Row>
              <Row
                label="Completed work"
                detail="When an agent finishes an approved action."
              >
                {toggle("notifyCompleted", "Completed work notifications")}
              </Row>
              <Row
                label="Notification sounds"
                detail="Play a sound for new updates."
              >
                {toggle("sound", "Notification sounds")}
              </Row>
            </Section>
          </>
        )}
        {page === "privacy" && (
          <>
            <p className="settings-intro">
              Manage the information saved on this device.
            </p>
            <Section title="Conversation history">
              <Row
                label="Save chat history"
                detail="When off, conversations are kept only for this visit. Previously saved conversations are removed from browser storage."
              >
                {toggle("saveHistory", "Save chat history")}
              </Row>
              <Row
                label="Clear all conversations"
                detail="Permanently remove your saved chats. Approval history is kept."
              >
                <button
                  className="button secondary danger-text"
                  onClick={() => setConfirm("history")}
                  disabled={!state.sessions.length}
                >
                  Clear history
                </button>
              </Row>
            </Section>
            <Section title="Your data">
              <Row
                label="Export workspace data"
                detail="Download your profile, preferences, chats and task history as JSON."
              >
                <button className="button secondary" onClick={exportData}>
                  <Icon name="download" size={16} />
                  Export data
                </button>
              </Row>
              <Row
                label="Reset demo workspace"
                detail="Restore the example sessions and tasks. Your current local data will be replaced."
              >
                <button
                  className="button secondary danger-text"
                  onClick={() => setConfirm("reset")}
                >
                  Reset demo
                </button>
              </Row>
            </Section>
          </>
        )}
        {page === "workspace" && (
          <form
            className="settings-form"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const name = String(data.get("name") ?? "").trim();
              if (!name) return;
              const saved = update((current) => ({
                ...current,
                workspace: {
                  name,
                  description: String(data.get("description") ?? "").trim(),
                },
              }));
              toast(
                saved
                  ? "Workspace details saved."
                  : "Workspace updated for this visit only.",
              );
            }}
          >
            <label>
              Workspace name
              <input
                name="name"
                defaultValue={state.workspace.name}
                required
                maxLength={32}
                pattern=".*\S.*"
              />
            </label>
            <label>
              Description
              <textarea
                name="description"
                defaultValue={state.workspace.description}
                maxLength={240}
                rows={3}
              />
            </label>
            <button className="button primary" type="submit">
              Save workspace
            </button>
            <Section title="Members">
              <Row label={state.profile.name} detail={state.profile.email}>
                <span className="muted-tag">Owner</span>
              </Row>
              <p className="field-hint">
                Team invitations will be available when workspace accounts are
                connected.
              </p>
            </Section>
          </form>
        )}
        {page === "billing" && (
          <>
            <div className="billing-intro">
              <span className="agent-avatar">
                <Icon name="brain" size={25} />
              </span>
              <h3>You’re exploring the demo.</h3>
              <p>
                No subscription, payment method or charges are associated with
                this workspace.
              </p>
            </div>
            <Section title="Plan details">
              <Row label="Current workspace">
                <span className="muted-tag">Demo</span>
              </Row>
              <Row label="Payment method">
                <span className="muted">None</span>
              </Row>
              <Row label="Billing history">
                <span className="muted">No invoices</span>
              </Row>
            </Section>
          </>
        )}
        {!["profile", "workspace", "billing", "connections"].includes(page) && (
          <p className="saved-status">
            <Icon name={storageAvailable ? "check" : "info"} size={16} />
            {storageAvailable
              ? "Preferences saved on this device"
              : "Changes are temporary for this visit"}
          </p>
        )}
      </div>
      {confirm && (
        <Dialog
          title={
            confirm === "history"
              ? "Clear all conversations?"
              : "Reset your demo workspace?"
          }
          onClose={() => setConfirm(null)}
        >
          <p>
            {confirm === "history"
              ? "This removes all conversations from this browser. Your task decisions and settings will be kept. This cannot be undone."
              : "This replaces your profile, settings, conversations and task decisions with the original demo. This cannot be undone."}
          </p>
          <div className="dialog-actions">
            <button
              className="button secondary"
              onClick={() => setConfirm(null)}
            >
              Cancel
            </button>
            <button
              className="button danger"
              onClick={() => {
                if (confirm === "history") {
                  update((current) => ({ ...current, sessions: [] }));
                  toast("Conversation history cleared.");
                } else {
                  update(() => createDemoWorkspace());
                  onReset();
                  toast("Demo workspace reset.");
                }
                setConfirm(null);
              }}
            >
              {confirm === "history" ? "Clear conversations" : "Reset demo"}
            </button>
          </div>
        </Dialog>
      )}
      {activeConnection && (
        <Dialog
          title={`${activeConnection.name} connection`}
          onClose={() => setConnection(null)}
        >
          <p>
            {activeConnection.description}. You can change the demo connection
            state to preview this integration. This does not sign in to{" "}
            {activeConnection.name}.
          </p>
          <div className="dialog-actions">
            <button
              className="button secondary"
              onClick={() => setConnection(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              onClick={() => {
                update((current) => ({
                  ...current,
                  connections: current.connections.map((item) =>
                    item.id === activeConnection.id
                      ? { ...item, connected: !item.connected }
                      : item,
                  ),
                }));
                toast(
                  `${activeConnection.name} demo ${activeConnection.connected ? "disconnected" : "connected"}.`,
                );
                setConnection(null);
              }}
            >
              {activeConnection.connected ? "Disconnect demo" : "Connect demo"}
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
