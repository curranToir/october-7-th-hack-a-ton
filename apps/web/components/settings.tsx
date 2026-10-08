"use client";

import { useState, type ReactNode } from "react";
import type {
  AutomationUpdate,
  LiveWorkspace,
  SettingPage,
} from "../../../coms/types.ts";
import type { WorkspaceActions } from "./use-workspace";
import { AccountSessions } from "./account-sessions";
import { Icon, type IconName } from "./icon";

export const settingsPages: {
  id: SettingPage;
  label: string;
  icon: IconName;
}[] = [
  { id: "general", label: "General", icon: "general" },
  { id: "profile", label: "Profile", icon: "profile" },
  { id: "agents", label: "Agents & automation", icon: "brain" },
  { id: "connections", label: "Connected apps", icon: "connections" },
  { id: "privacy", label: "Data & privacy", icon: "shield" },
  { id: "workspace", label: "Workspace", icon: "workspace" },
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
  actions,
  navigate,
}: {
  page: SettingPage;
  state: LiveWorkspace;
  actions: WorkspaceActions;
  navigate: (page: string) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const prefs = state.preferences;
  const logout = async () => {
    setBusy(true);
    try {
      await actions.logout();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign out failed.");
    } finally {
      setBusy(false);
    }
  };
  const exportData = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "toir-sales-export.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings">
        {settingsPages.map((item) => (
          <button
            key={item.id}
            className={page === item.id ? "active" : ""}
            aria-current={page === item.id ? "page" : undefined}
            onClick={() => navigate(item.id)}
          >
            <Icon name={item.icon} size={18} />
            {item.label}
          </button>
        ))}
      </nav>
      <div className="settings-content">
        <h2>{settingsPages.find((p) => p.id === page)?.label ?? "General"}</h2>
        {page === "general" && (
          <>
            <p className="settings-intro">
              Make this workspace comfortable to work in.
            </p>
            <Section title="Appearance">
              <Row label="Theme">
                <select
                  aria-label="Theme"
                  value={prefs.theme}
                  onChange={(e) =>
                    actions.updatePreferences({
                      theme: e.target.value as typeof prefs.theme,
                    })
                  }
                >
                  <option value="system">System</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </Row>
              <Row label="Compact sidebar" detail="Keep more sessions visible.">
                <Toggle
                  label="Compact sidebar"
                  checked={prefs.compactSidebar}
                  onChange={(compactSidebar) =>
                    actions.updatePreferences({ compactSidebar })
                  }
                />
              </Row>
            </Section>
            <Section title="Task review">
              <Row
                label="Automatically show next task"
                detail="Move to the next proposal after your decision is saved."
              >
                <Toggle
                  label="Automatically show next task"
                  checked={prefs.autoAdvance}
                  onChange={(autoAdvance) =>
                    actions.updatePreferences({ autoAdvance })
                  }
                />
              </Row>
              <Row
                label="CRM approval"
                detail="Every CRM change requires an explicit approval of the current proposal version."
              >
                <span className="muted-tag">Always required</span>
              </Row>
            </Section>
            <p className="saved-status">
              <Icon name="check" size={16} />
              Display preferences are saved on this device.
            </p>
          </>
        )}
        {page === "profile" && (
          <>
            <p className="settings-intro">
              Your Google sign-in gives you access to the Toir sales workspace.
            </p>
            <Section title="Signed-in account">
              <Row label={state.profile.name} detail={state.profile.email}>
                <span className="muted-tag">Sales member</span>
              </Row>
              <Row
                label="Session"
                detail="Sign out of this browser without interrupting approved background work."
              >
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void logout()}
                >
                  {busy ? "Signing out…" : "Sign out"}
                </button>
              </Row>
            </Section>
            <AccountSessions onChanged={actions.refresh} />
          </>
        )}
        {page === "agents" && (
          <AutomationSettings state={state} actions={actions} />
        )}
        {page === "connections" && (
          <>
            <p className="settings-intro">
              Connection readiness is checked by the coordinator.
            </p>
            <div className="connections">
              {state.connections.map((connection) => (
                <div className="connection-row" key={connection.id}>
                  <span className={`app-icon app-${connection.id}`}>
                    <Icon name="crm" size={21} />
                  </span>
                  <div className="connection-copy">
                    <strong>{connection.name}</strong>
                    <p>{connection.description}</p>
                    <span
                      className={`connection-status ${connection.connected ? "connected" : ""}`}
                    >
                      {connection.connected ? "Ready" : "Unavailable"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            <Section title="Shared sales memory">
              <Row
                label="Spark / Cognee"
                detail="Accepted research is synced independently from CRM execution."
              >
                <span className="muted-tag">
                  {state.capabilities.brain
                    ? "Ready"
                    : "Awaiting service readiness"}
                </span>
              </Row>
            </Section>
            <p className="field-hint">
              Account connections are managed by the workspace administrator.
              Connection ownership does not change who requested or approved a
              CRM update.
            </p>
            <Readiness state={state} />
          </>
        )}
        {page === "privacy" && (
          <>
            <p className="settings-intro">
              Conversations, research evidence, and approval decisions are
              stored by the coordinator.
            </p>
            <Section title="Workspace records">
              <Row
                label="Conversation history"
                detail="Use a session’s menu to delete it. Related task decisions and CRM audit records are retained."
              >
                <span className="muted-tag">Server managed</span>
              </Row>
              <Row
                label="Export visible workspace data"
                detail="Download your current sessions, proposals, evidence, and execution status."
              >
                <button className="button secondary" onClick={exportData}>
                  <Icon name="download" size={16} />
                  Export data
                </button>
              </Row>
            </Section>
          </>
        )}
        {page === "workspace" && (
          <>
            <p className="settings-intro">Toir’s shared sales workspace.</p>
            <Section title="Access">
              <Row
                label="Sales workspace"
                detail="Curran and Jared are authorized sales members. The server enforces workspace access for research, tasks, approvals, and memory."
              >
                <span className="muted-tag">Toir</span>
              </Row>
              <Row label="Current member" detail={state.profile.email}>
                <span>{state.profile.name}</span>
              </Row>
              <Row
                label="Automation timezone"
                detail="Daily discovery and enrichment quotas reset at midnight in this timezone."
              >
                <span>America/Los_Angeles</span>
              </Row>
            </Section>
          </>
        )}
        {error && (
          <p className="sales-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
export function Readiness({ state }: { state: LiveWorkspace }) {
  return !state.capabilities.ready ? (
    <div className="notice readiness-notice">
      <Icon name="info" />
      <div>
        <strong>Continuous dispatch is waiting on readiness</strong>
        {state.capabilities.research_ready === true && (
          <p>On-demand research is ready.</p>
        )}
        <ul>
          {state.capabilities.reasons.length ? (
            state.capabilities.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))
          ) : (
            <li>One or more services are not ready.</li>
          )}
        </ul>
      </div>
    </div>
  ) : null;
}
function AutomationSettings({
  state,
  actions,
}: {
  state: LiveWorkspace;
  actions: WorkspaceActions;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const save = async (update: Partial<AutomationUpdate>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await actions.saveAutomation(update);
      setNotice("Automation settings saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings could not be saved.");
    } finally {
      setBusy(false);
    }
  };
  const dailyJobs = state.jobs.filter(
    (j) =>
      j.origin === "background" &&
      j.budget_day ===
        new Intl.DateTimeFormat("en-CA", {
          timeZone: "America/Los_Angeles",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date()),
  );
  return (
    <>
      <p className="settings-intro">
        Company discovery and contact research work continuously, with a review
        before every CRM update.
      </p>
      <Section title="Continuous prospecting">
        <Row
          label={state.automation.enabled ? "Enabled" : "Paused"}
          detail="Pausing stops new background dispatch. Current research and approved CRM operations can finish."
        >
          <Toggle
            label="Continuous prospecting"
            checked={state.automation.enabled}
            disabled={
              busy || (!state.automation.enabled && !state.capabilities.ready)
            }
            onChange={(enabled) => void save({ enabled })}
          />
        </Row>
        <Row
          label="Company discovery"
          detail="Find and qualify companies using cited evidence."
        >
          <span className="muted-tag">
            {state.capabilities.research_ready === true
              ? "Ready"
              : state.capabilities.research_ready === false
                ? "Unavailable"
                : "Status unavailable"}
          </span>
        </Row>
        <Row
          label="Contact research"
          detail="Up to five verified executives and buying stakeholders per company."
        >
          <span className="muted-tag">
            {state.capabilities.contacts ? "Ready" : "Unavailable"}
          </span>
        </Row>
      </Section>
      <Readiness state={state} />
      <form
        className="settings-form automation-form"
        key={state.automation.updated_at}
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const number = (key: string) => Number(data.get(key));
          const min = number("employee_min"),
            max = number("employee_max");
          if (min > max) {
            setError("Minimum company size must not exceed the maximum.");
            return;
          }
          void save({
            request: String(data.get("request")).trim(),
            employee_min: min,
            employee_max: max,
            fit_threshold: number("fit_threshold"),
            daily_enrichments: number("daily_enrichments"),
            daily_discoveries: number("daily_discoveries"),
          });
        }}
      >
        <h3>Targeting and daily limits</h3>
        <label>
          Research brief
          <textarea
            name="request"
            defaultValue={state.automation.request}
            required
            minLength={10}
            maxLength={4000}
            rows={4}
            disabled={busy}
          />
        </label>
        <p className="field-hint">
          United States · Daily quotas reset in America/Los_Angeles. Explicit
          chat requests have priority at the next worker slot and do not use
          background quotas.
        </p>
        <div className="automation-fields">
          {(
            [
              ["employee_min", "Minimum employees", 1, 100000],
              ["employee_max", "Maximum employees", 1, 100000],
              ["fit_threshold", "Minimum fit score", 0, 100],
              ["daily_enrichments", "Company enrichments per day", 1, 100],
              ["daily_discoveries", "Discovery batches per day", 1, 100],
            ] as const
          ).map(([key, title, min, max]) => (
            <label key={key}>
              {title}
              <input
                name={key}
                type="number"
                min={min}
                max={max}
                step={1}
                required
                defaultValue={state.automation[key]}
                disabled={busy}
              />
            </label>
          ))}
        </div>
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save automation"}
        </button>
      </form>
      <p className="field-hint">
        Today: {dailyJobs.filter((j) => j.kind === "discovery").length}{" "}
        discovery batches and{" "}
        {dailyJobs.filter((j) => j.kind === "enrich").length} enrichment
        attempts recorded.
      </p>
      {error && (
        <p className="sales-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="sales-success" role="status">
          {notice}
        </p>
      )}
    </>
  );
}
