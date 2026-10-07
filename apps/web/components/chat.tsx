"use client";

import { useEffect, useRef, useState } from "react";
import type { Session, WorkspaceState } from "../../../coms/types.ts";
import { Icon } from "./icon";
import { Logo } from "./logo";

export function Chat({
  session,
  state,
  onSend,
  onTasks,
}: {
  session?: Session;
  state: WorkspaceState;
  onSend: (content: string) => void;
  onTasks: () => void;
}) {
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (session?.messages.length)
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages.length]);
  const submit = () => {
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
    input.current?.focus();
  };
  return (
    <div className="chat-workspace">
      <div className="chat-scroll">
        {!session?.messages.length ? (
          <div className="chat-welcome">
            <span className="welcome-mark">
              <Logo size={54} alt="TOIR" />
            </span>
            <h1>What’s on your mind?</h1>
            <p>A place to think, find answers, and work with your agents.</p>
            <div className="suggestions">
              {[
                "Find new prospects",
                "Review my pipeline",
                "Plan customer follow-ups",
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    setDraft(suggestion);
                    input.current?.focus();
                  }}
                >
                  {suggestion}
                  <Icon name="arrow" size={15} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div
            className="conversation"
            role="log"
            aria-label="Conversation"
            aria-live="polite"
          >
            {session.messages.map((message) => {
              const pending = state.tasks.filter(
                (task) =>
                  message.taskIds?.includes(task.id) &&
                  task.status === "pending",
              ).length;
              return (
                <div
                  key={message.id}
                  className={`message message-${message.role}`}
                >
                  {message.role === "assistant" && (
                    <span className="message-avatar">
                      <Logo size={32} />
                    </span>
                  )}
                  <div className="message-body">
                    {message.role === "assistant" && (
                      <span className="message-author">{session.agent}</span>
                    )}
                    <div className="message-content">{message.content}</div>
                    {message.taskIds && (
                      <button className="review-tasks" onClick={onTasks}>
                        <span className="review-icon">
                          <Icon name={pending ? "tasks" : "check"} size={18} />
                        </span>
                        <span>
                          {pending
                            ? `${pending} ${pending === 1 ? "task" : "tasks"} waiting for approval`
                            : "All tasks reviewed"}
                        </span>
                        <strong>
                          {pending ? "Review tasks" : "View tasks"}
                          <Icon name="arrow" size={17} />
                        </strong>
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            <div ref={end} />
          </div>
        )}
      </div>
      <div className="composer-wrap">
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label className="sr-only" htmlFor="message-input">
            Message TOIR
          </label>
          <textarea
            ref={input}
            id="message-input"
            placeholder="Message TOIR…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={1}
            maxLength={10000}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                submit();
              }
            }}
          />
          <div className="composer-tools">
            <span className="composer-agent">
              <Icon name="brain" size={16} />
              {session?.agent === "TOIR" ||
              session?.agent === "Company Brain" ||
              !session
                ? state.preferences.defaultAgent
                : session.agent}
            </span>
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={!draft.trim()}
            >
              <Icon name="send" size={21} />
            </button>
          </div>
        </form>
        <p className="composer-caption">
          <Icon name="info" size={13} />
          Demo workspace. Messages and approvals stay on this device.
        </p>
      </div>
    </div>
  );
}
