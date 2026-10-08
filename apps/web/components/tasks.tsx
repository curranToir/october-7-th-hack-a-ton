"use client";

import { useState } from "react";
import type { LiveWorkspace } from "../../../coms/types.ts";
import { Icon } from "./icon";
import { ProposalCard, type ProposalActions } from "./proposal";
import { MeetingTasks } from "./meeting-tasks";
import type { MeetingActions } from "./use-meetings";

export function Tasks({
  state,
  actions,
  onSession,
  meetingActions,
  initialTab = "pending",
  onMeeting,
}: {
  state: LiveWorkspace;
  actions: ProposalActions;
  onSession: (id: string) => void;
  meetingActions: MeetingActions;
  initialTab?: string;
  onMeeting: (id: string) => void;
}) {
  const [tab, setTab] = useState(initialTab);
  const [index, setIndex] = useState(0);
  const [reviewed, setReviewed] = useState(false);
  const pending = state.proposals.filter((p) => p.status === "pending");
  const history = state.proposals
    .filter((p) => p.status !== "pending")
    .sort((a, b) =>
      (b.decided_at ?? b.updated_at).localeCompare(
        a.decided_at ?? a.updated_at,
      ),
    );
  const position = Math.min(index, Math.max(0, pending.length - 1));
  const task = pending[position];
  const card = (p: typeof task) => (
    <ProposalCard
      key={`${p.id}:${p.version}`}
      proposal={p}
      actions={actions}
      onSession={onSession}
      sessionAvailable={state.sessions.some((s) => s.id === p.session_id)}
      onDecided={() => {
        if (!state.preferences.autoAdvance) setReviewed(true);
      }}
    />
  );
  return (
    <div className="tasks-workspace">
      <div className="task-tabs" role="tablist" aria-label="Task status">
        {["pending", "history", "meetings"].map((name) => (
          <button
            key={name}
            role="tab"
            id={`${name}-tab`}
            aria-controls="task-panel"
            aria-selected={tab === name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {name === "pending" ? "Pending" : name === "history" ? "History" : "Meeting issues"}
            <span>{name === "pending" ? pending.length : name === "history" ? history.length : meetingActions.state?.tasks.filter(task => task.status === "pending").length ?? 0}</span>
          </button>
        ))}
      </div>
      <div
        className="task-panel"
        id="task-panel"
        role="tabpanel"
        aria-labelledby={`${tab}-tab`}
      >
        {tab === "meetings" ? <MeetingTasks actions={meetingActions} onMeeting={onMeeting} /> : tab === "history" ? (
          history.length ? (
            <div className="proposal-history">{history.map(card)}</div>
          ) : (
            <Empty
              title="No decisions yet"
              detail="Your approved and denied proposals will appear here, along with CRM execution status."
            />
          )
        ) : reviewed ? (
          <div className="task-result">
            <span className="success-orbit">
              <Icon name="check" size={28} />
            </span>
            <h2>Decision recorded</h2>
            <p>
              Execution progress is available in History and the originating
              session.
            </p>
            <button
              className="button primary"
              onClick={() => setReviewed(false)}
            >
              Continue review
            </button>
          </div>
        ) : task ? (
          <>
            <div className="queue-navigation">
              <button
                className="icon-button"
                aria-label="Previous task"
                disabled={position === 0}
                onClick={() => setIndex(position - 1)}
              >
                <Icon name="chevron" size={18} className="rotate" />
              </button>
              <span>
                {position + 1} of {pending.length} pending
              </span>
              <button
                className="icon-button"
                aria-label="Next task"
                disabled={position === pending.length - 1}
                onClick={() => setIndex(position + 1)}
              >
                <Icon name="chevron" size={18} />
              </button>
            </div>
            {card(task)}
          </>
        ) : (
          <Empty
            title="You’re all caught up."
            detail="New findings stay here until you approve or deny their proposed CRM changes."
          />
        )}
      </div>
    </div>
  );
}
function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="task-empty">
      <span className="success-orbit">
        <Icon name="check" size={28} />
      </span>
      <h2>{title}</h2>
      <p>{detail}</p>
    </div>
  );
}
