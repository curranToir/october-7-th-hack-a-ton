"use client";

import { useEffect, useRef, useState } from "react";
import type { LiveWorkspace, SalesJob, Session } from "../../../coms/types.ts";
import type { WorkspaceActions } from "./use-workspace";
import { Icon } from "./icon";
import { Logo } from "./logo";
import { ContactDetails, ProposalCard } from "./proposal";

export function Chat({
  session,
  state,
  onSend,
  actions,
}: {
  session?: Session;
  state: LiveWorkspace;
  onSend: (content: string) => Promise<void>;
  actions: WorkspaceActions;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const jobs = state.jobs.filter((j) => j.session_id === session?.id);
  const proposals = state.proposals.filter(
    (p) =>
      p.session_id === session?.id ||
      session?.messages.some((m) => m.taskIds?.includes(p.id)),
  );
  useEffect(() => {
    if (session?.messages.length)
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages.length]);
  const submit = async () => {
    if (!draft.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      await onSend(draft.trim());
      setDraft("");
      input.current?.focus();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Your message could not be sent. It is preserved below.",
      );
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="chat-workspace">
      <div className="chat-scroll">
        {!session?.messages.length && !jobs.length ? (
          <div className="chat-welcome">
            <span className="welcome-mark">
              <Logo size={54} alt="TOIR" />
            </span>
            <h1>What’s on your mind?</h1>
            <p>
              Research a company, find decision-makers, and prepare CRM updates.
            </p>
            <div className="suggestions">
              {[
                "Find new prospects",
                "Research a company for me",
                "Research a company and add it to my CRM",
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
            {session?.messages.map((message) => (
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
                </div>
              </div>
            ))}
            {jobs.map((job) => (
              <JobProgress
                key={job.id}
                job={job}
                actions={actions}
                onSelect={(domain) => setDraft(domain)}
              />
            ))}
            {proposals.map((proposal) => (
              <ProposalCard
                key={`${proposal.id}:${proposal.version}`}
                proposal={proposal}
                actions={actions}
              />
            ))}
            <div ref={end} />
          </div>
        )}
      </div>
      <div className="composer-wrap">
        {error && (
          <p className="sales-error" role="alert">
            {error}
          </p>
        )}
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="sr-only" htmlFor="message-input">
            Message TOIR
          </label>
          <textarea
            ref={input}
            id="message-input"
            placeholder="Research a company or prepare a CRM addition…"
            value={draft}
            disabled={sending}
            onChange={(event) => setDraft(event.target.value)}
            rows={1}
            maxLength={4000}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="composer-tools">
            <span className="composer-agent">
              <Icon name="brain" size={16} />
              TOIR research
            </span>
            <button
              type="submit"
              className="send-button"
              aria-label={sending ? "Sending message" : "Send message"}
              disabled={!draft.trim() || sending}
            >
              <Icon name="send" size={21} />
            </button>
          </div>
        </form>
        <p className="composer-caption">
          <Icon name="info" size={13} />
          {state.capabilities.research_ready === true
            ? "On-demand research is ready."
            : state.capabilities.research_ready === false
              ? "On-demand research is waiting on service readiness."
              : "Research runs in the background."}{" "}
          Every CRM update needs your approval.
        </p>
      </div>
    </div>
  );
}
function JobProgress({
  job,
  actions,
  onSelect,
}: {
  job: SalesJob;
  actions: WorkspaceActions;
  onSelect: (domain: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = ["queued", "running"].includes(job.status);
  const retryable = ["failed", "interrupted", "cancelled"].includes(job.status);
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className={`job-progress job-${job.status}`}
      aria-label={`${job.kind} research ${job.status}`}
    >
      <div className="job-heading">
        <Icon
          name={
            active ? "clock" : job.status === "completed" ? "check" : "info"
          }
          size={18}
        />
        <strong>
          {job.kind === "enrich"
            ? "Contact research"
            : job.kind === "discovery"
              ? "Company discovery"
              : "Company research"}
        </strong>
        <span>{job.status.replaceAll("_", " ")}</span>
      </div>
      <p>{job.progress}</p>
      {job.kind !== "chat" &&
        (job.status === "completed" ||
          (job.memory_status && job.memory_status !== "not_requested")) && (
          <p aria-live="polite">
            Research memory:{" "}
            {job.memory_status === "synced"
              ? "Synced"
              : job.memory_status === "pending"
                ? "Pending sync"
                : job.memory_status === "blocked"
                  ? "Sync blocked"
                  : job.memory_status === "not_requested"
                    ? "Not requested"
                    : "Status unavailable"}
          </p>
        )}
      {job.error && <p className="sales-error">{job.error}</p>}
      {job.status === "needs_input" && job.candidates.length > 0 && (
        <>
          <p>Choose the company, then send the prepared message to confirm.</p>
          <div className="candidate-options">
            {job.candidates.map((candidate) => (
              <button
                key={candidate.domain}
                className="button secondary"
                onClick={() => onSelect(candidate.domain)}
              >
                {candidate.name} · {candidate.domain}
              </button>
            ))}
          </div>
        </>
      )}
      {job.report && !job.propose_crm && (
        <details className="research-findings">
          <summary>Read research findings</summary>
          <p>{job.report.summary}</p>
          {job.report.contacts.map((contact) => (
            <div key={contact.id} className="sales-contact">
              <ContactDetails contact={contact} sources={job.report!.sources} />
            </div>
          ))}
          {job.report.gaps.length > 0 && (
            <>
              <h4>Research gaps</h4>
              <ul>
                {job.report.gaps.map((gap, i) => (
                  <li key={i}>{gap}</li>
                ))}
              </ul>
            </>
          )}
        </details>
      )}
      {(active || retryable) && (
        <div className="job-actions">
          {active ? (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void act(() => actions.cancelJob(job.id))}
            >
              {busy ? "Cancelling…" : "Cancel research"}
            </button>
          ) : (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void act(() => actions.retryJob(job.id))}
            >
              {busy ? "Queuing…" : "Run research again"}
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="sales-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
