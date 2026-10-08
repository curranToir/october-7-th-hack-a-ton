"use client";

import { useState } from "react";
import type { MeetingTask } from "../../../coms/meeting-types.ts";
import { evidenceUrl } from "../../../coms/sales-client.ts";
import { Icon } from "./icon";
import { MeetingLoadNotice, MeetingStatus, meetingDate } from "./meeting-shared";
import type { MeetingActions } from "./use-meetings";

export function MeetingTasks({ actions, onMeeting }: { actions: MeetingActions; onMeeting: (id: string) => void }) {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const pending = actions.state?.tasks.filter(task => task.status === "pending") ?? [];
  const history = actions.state?.tasks.filter(task => task.status !== "pending") ?? [];
  const tasks = tab === "pending" ? pending : history;
  return <section className="meeting-tasks" aria-label="Meeting issue approvals">
    <div className="meeting-task-intro"><div><h2>Customer issues, ready for your review.</h2>
      <p>Check the exact issue title, body, and repository before approving. Approved issues are sent to GitHub for engineering.</p></div></div>
    <MeetingLoadNotice actions={actions} />
    {actions.state && <>
      {!actions.state.capabilities.github_ready && <p className="meeting-source-notice"><Icon name="connections" size={17} /> GitHub needs to be connected before you can approve. Draft review and editing are available.</p>}
      <nav className="meeting-task-filters" aria-label="Meeting task status">
        <button aria-pressed={tab === "pending"} className={tab === "pending" ? "active" : ""} onClick={() => setTab("pending")}>Needs approval <span>{pending.length}</span></button>
        <button aria-pressed={tab === "history"} className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>History <span>{history.length}</span></button>
      </nav>
      {tasks.length ? <div className="meeting-task-list">{tasks.map(task => <MeetingTaskCard key={task.id} task={task} actions={actions} onMeeting={onMeeting} />)}</div>
        : <div className="meeting-empty"><span className="success-orbit"><Icon name="check" size={27} /></span>
          <h3>{tab === "pending" ? "No customer issues waiting." : "No meeting decisions yet."}</h3>
          <p>{tab === "pending" ? "Issues found in your meetings arrive here for review. Open Meetings to try the customer demo." : "Approved and rejected issues appear here, with publication progress and links to GitHub."}</p>
          <button className="button secondary" onClick={() => onMeeting("")}>Open Meetings<Icon name="arrow" size={15} /></button>
        </div>}
    </>}
  </section>;
}

function MeetingTaskCard({ task, actions, onMeeting }: { task: MeetingTask; actions: MeetingActions; onMeeting: (id: string) => void }) {
  const [editor, setEditor] = useState<{ version: number; title: string; body: string } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = task.status === "pending";
  const conflict = !!editor && (editor.version !== task.version || !pending);
  const busy = actions.busy;
  const meeting = actions.state?.meetings.find(item => item.id === task.meeting_id);
  const issueUrl = evidenceUrl(task.issue_url);
  const githubReady = actions.state?.capabilities.github_ready === true;
  const act = async (operation: () => Promise<unknown>, success: string) => {
    setError("");
    setNotice("");
    try { await operation(); setNotice(success); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "The issue could not be updated."); }
  };
  return <article className="meeting-task-card">
    <header><div className="meeting-task-origin"><span className="agent-avatar"><Icon name="messages" size={18} /></span>
      <div><strong>Meeting agent</strong><button className="text-button" onClick={() => onMeeting(task.meeting_id)}>{meeting?.title ?? "Open original meeting"}</button></div>
    </div><MeetingStatus status={task.status} /></header>
    <div className="meeting-task-destination"><Icon name="files" size={14} /><span>{task.repository}</span><span>Version {task.version}</span></div>
    {task.source === "demo" && <p className="meeting-demo-label">Fictional demo · approval creates a real GitHub issue</p>}
    {editor ? <form className="meeting-task-editor" onSubmit={event => {
      event.preventDefault();
      if (conflict || busy) return;
      void act(async () => {
        await actions.edit(task.id, editor.version, editor.title.trim(), editor.body.trim());
        setEditor(null);
      }, "Changes saved. Review the new version before approving.");
    }}>
      <label className="form-label">Issue title<input value={editor.title} onChange={event => setEditor({ ...editor, title: event.target.value })} minLength={5} maxLength={200} required disabled={busy} /></label>
      <label className="form-label">Issue body (Markdown)<textarea value={editor.body} onChange={event => setEditor({ ...editor, body: event.target.value })} rows={15} minLength={20} maxLength={16000} required disabled={busy} /></label>
      {conflict && <div className="meeting-edit-conflict" role="alert"><p>The saved task changed while you were editing. Your draft is preserved in the editor; approval and saving are paused.</p>
        <details><summary>Review saved version {task.version}</summary><h4>{task.title}</h4><pre className="meeting-issue-body">{task.body}</pre></details>
        {pending && <button type="button" className="text-button" disabled={busy} onClick={() => { setEditor({ version: task.version, title: task.title, body: task.body }); setError(""); }}>Replace my draft with saved version {task.version}</button>}
      </div>}
      <div className="meeting-task-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setEditor(null)}>Cancel editing</button>
        <button type="submit" className="button primary" disabled={busy || conflict}>Save new version</button></div>
    </form> : <>
      <h3 className="meeting-issue-title">{task.title}</h3>
      <details className="meeting-issue-preview" open={pending || undefined}><summary>Exact GitHub issue body · Markdown</summary><pre className="meeting-issue-body">{task.body}</pre></details>
    </>}
    {task.error && <p className="sales-error" role="alert">{task.error}</p>}
    {error && <p className="sales-error" role="alert">{error}</p>}
    {notice && <p className="sales-success" role="status">{notice}</p>}
    {pending && !editor && <>
      <p className="meeting-approval-notice">Approving version {task.version} authorizes publishing the title and body above to <strong>{task.repository}</strong>.</p>
      <div className="meeting-task-actions">
        <button className="button secondary" disabled={busy} onClick={() => { setEditor({ version: task.version, title: task.title, body: task.body }); setError(""); setNotice(""); }}><Icon name="edit" size={15} />Edit draft</button>
        <button className="button deny" disabled={busy} onClick={() => void act(() => actions.decide(task.id, task.version, "reject"), "Issue rejected. No GitHub issue will be created.")}>Reject</button>
        <button className="button primary" disabled={busy || !githubReady} title={!githubReady ? "Connect GitHub to approve" : undefined}
          onClick={() => void act(() => actions.decide(task.id, task.version, "approve"), "Approval saved. Follow GitHub publication in History.")}><Icon name="check" size={16} />Approve & create issue</button>
      </div>
    </>}
    {!pending && <div className="meeting-task-receipt">
      {task.decided_by && <p>{task.status === "rejected" ? "Rejected" : "Approved"} by {task.decided_by}{task.decided_at ? ` · ${meetingDate(task.decided_at)}` : ""}</p>}
      {["approved", "publishing"].includes(task.status) && <p role="status">Approval recorded. The issue is being sent to GitHub; this view updates automatically.</p>}
      {issueUrl && <a className="button secondary" href={issueUrl} target="_blank" rel="noopener noreferrer">Open GitHub issue<Icon name="link" size={15} /></a>}
      {task.status === "publish_failed" && <button className="button secondary" disabled={busy || !githubReady} onClick={() => void act(() => actions.retryTask(task.id), "The approved issue is queued for another publication attempt.")}>Retry approved publication</button>}
      {task.status === "publish_unknown" && <><p>The publication result is uncertain. Check GitHub for this issue before creating another; checking the result does not resubmit it.</p>
        <button className="button secondary" disabled={busy || !githubReady} onClick={() => void act(() => actions.reconcile(task.id), "GitHub checked. Review the current publication status above.")}>Check GitHub result</button></>}
    </div>}
  </article>;
}
