"use client";

import { useEffect, useRef, useState } from "react";
import type { Decision, Task, WorkspaceState } from "../../../coms/types.ts";
import { Icon } from "./icon";
import { Dialog } from "./dialog";

export function Tasks({
  state,
  onDecide,
  onSession,
}: {
  state: WorkspaceState;
  onDecide: (id: string, decision: Decision) => void;
  onSession: (id: string) => void;
}) {
  const [tab, setTab] = useState("pending");
  const [index, setIndex] = useState(0);
  const [reviewed, setReviewed] = useState<Task | null>(null);
  const [source, setSource] = useState<Task | null>(null);
  const [deciding, setDeciding] = useState(false);
  const decisionLock = useRef(false);
  const decisionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (decisionTimer.current) clearTimeout(decisionTimer.current);
    },
    [],
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const pending = state.tasks.filter((task) => task.status === "pending");
  const history = state.tasks
    .filter((task) => task.status !== "pending")
    .sort((a, b) => (b.decidedAt ?? "").localeCompare(a.decidedAt ?? ""));
  const position = Math.min(index, Math.max(0, pending.length - 1));
  const task = pending[position];
  const lastTask = useRef(task?.id);
  useEffect(() => {
    if (lastTask.current !== task?.id) heading.current?.focus();
    lastTask.current = task?.id;
  }, [task?.id]);
  function decide(decision: Decision) {
    if (!task || decisionLock.current) return;
    // Prevent a double-click from accidentally approving the next card.
    decisionLock.current = true;
    setDeciding(true);
    decisionTimer.current = setTimeout(() => {
      decisionLock.current = false;
      setDeciding(false);
    }, 400);
    onDecide(task.id, decision);
    if (!state.preferences.autoAdvance)
      setReviewed({ ...task, status: decision });
  }
  return (
    <div className="tasks-workspace">
      <div className="task-tabs" role="tablist" aria-label="Task status">
        <button
          role="tab"
          aria-selected={tab === "pending"}
          aria-controls="task-panel"
          id="pending-tab"
          className={tab === "pending" ? "active" : ""}
          onClick={() => setTab("pending")}
        >
          Pending <span>{pending.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "history"}
          aria-controls="task-panel"
          id="history-tab"
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          History{history.length > 0 && <span>{history.length}</span>}
        </button>
      </div>
      <div
        className="task-panel"
        id="task-panel"
        role="tabpanel"
        aria-labelledby={`${tab}-tab`}
      >
        {tab === "history" ? (
          <div className="history-list">
            {history.length ? (
              history.map((item) => (
                <div className="history-row" key={item.id}>
                  <span className={`history-icon ${item.status}`}>
                    <Icon
                      name={item.status === "approved" ? "check" : "close"}
                      size={20}
                    />
                  </span>
                  <div className="history-copy">
                    <strong>{item.title}</strong>
                    <p>
                      {item.subject} <span>· {item.agent}</span>
                    </p>
                    <small>
                      {item.decidedAt &&
                        new Intl.DateTimeFormat("en-US", {
                          month: "short",
                          day: "numeric",
                          hour: "numeric",
                          minute: "2-digit",
                          timeZone: state.preferences.timeZone,
                        }).format(new Date(item.decidedAt))}
                    </small>
                  </div>
                  <span className={`status-label ${item.status}`}>
                    {item.status === "approved" ? "Approved" : "Denied"}
                  </span>
                  <button
                    className="icon-button"
                    aria-label={`View details for ${item.subject}`}
                    onClick={() => setSource(item)}
                  >
                    <Icon name="chevron" size={18} />
                  </button>
                </div>
              ))
            ) : (
              <Empty
                title="No decisions yet"
                detail="Tasks you approve or deny will appear here."
              />
            )}
          </div>
        ) : reviewed ? (
          <div className="task-result">
            <span className="success-orbit">
              <Icon
                name={reviewed.status === "approved" ? "check" : "close"}
                size={30}
              />
            </span>
            <h2>
              {reviewed.status === "approved"
                ? "Approval recorded"
                : "Task denied"}
            </h2>
            <p>{reviewed.subject} · Demo decision saved</p>
            <button
              className="button primary"
              onClick={() => setReviewed(null)}
            >
              {pending.length ? "Review next task" : "Finish review"}
              <Icon name="arrow" size={17} />
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
            <article className="approval-card" key={task.id}>
              <div className="approval-meta">
                <span className="agent-avatar">
                  <Icon name="brain" size={23} />
                </span>
                <div>
                  <strong>{task.agent}</strong>
                  <span>Ready for your review</span>
                </div>
                <span className="approval-badge">Needs approval</span>
              </div>
              <h2 ref={heading} tabIndex={-1}>
                {task.title}
              </h2>
              <div className="finding">
                <h3>What the agent found</h3>
                <strong>{task.subject}</strong>
                <p className="finding-subtitle">{task.subtitle}</p>
                <p>{task.finding}</p>
                <div className="evidence-links">
                  <button onClick={() => setSource(task)}>
                    View source
                    <Icon name="link" size={14} />
                  </button>
                  <button
                    disabled={
                      !state.sessions.some(
                        (session) => session.id === task.sessionId,
                      )
                    }
                    title={
                      state.sessions.some(
                        (session) => session.id === task.sessionId,
                      )
                        ? "Open the conversation"
                        : "This session has been deleted"
                    }
                    onClick={() => onSession(task.sessionId)}
                  >
                    View agent session
                    <Icon name="arrow" size={15} />
                  </button>
                </div>
              </div>
              <div className="proposed-steps">
                <h3>Proposed steps</h3>
                <ol>
                  {task.steps.map((step, i) => (
                    <li key={step}>
                      <span className="step-number">{i + 1}</span>
                      <span>{step}</span>
                      <span
                        className={`step-state ${i === task.steps.length - 1 ? "awaiting" : "complete"}`}
                      >
                        <Icon
                          name={i === task.steps.length - 1 ? "clock" : "check"}
                          size={15}
                        />
                        <span>
                          {i === task.steps.length - 1
                            ? "Needs approval"
                            : "Complete"}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="permission-scope">
                <Icon name="info" size={19} />
                <p>
                  {task.scope}
                  <span>{task.restriction}</span>
                </p>
              </div>
              <div className="approval-actions">
                <button
                  className="button deny"
                  disabled={deciding}
                  onClick={() => decide("denied")}
                >
                  <Icon name="close" size={18} />
                  Deny
                </button>
                <button
                  className="button primary"
                  disabled={deciding}
                  onClick={() => decide("approved")}
                >
                  <Icon name="check" size={19} />
                  {task.approveLabel}
                </button>
              </div>
            </article>
            <p className="queue-caption">
              {state.preferences.autoAdvance
                ? "After a decision, the next task opens."
                : "You choose when to move to the next task."}
            </p>
          </>
        ) : (
          <Empty
            title="You’re all caught up."
            detail="New findings from your agents will appear here for review."
            action={
              <button
                className="button secondary"
                onClick={() => setTab("history")}
              >
                View decision history
                <Icon name="arrow" size={16} />
              </button>
            }
          />
        )}
      </div>
      {source && (
        <Dialog title={source.source} onClose={() => setSource(null)}>
          <span className="muted-tag">Example evidence</span>
          <h3 className="source-subject">{source.subject}</h3>
          <p>{source.sourceDetail}</p>
          <div className="source-footer">
            <Icon name="brain" size={18} />
            Prepared by {source.agent}
          </div>
        </Dialog>
      )}
    </div>
  );
}
function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="task-empty">
      <span className="success-orbit">
        <Icon name="check" size={28} />
      </span>
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </div>
  );
}
