"use client";

import { useState } from "react";
import type { Meeting, MeetingCapabilities } from "../../../coms/meeting-types.ts";
import { parseMeetingTranscript } from "../../../coms/meeting-client.ts";
import { evidenceUrl } from "../../../coms/sales-client.ts";
import { Dialog } from "./dialog";
import { Icon } from "./icon";
import { MeetingLoadNotice, MeetingStatus, meetingDate, meetingTime } from "./meeting-shared";
import type { MeetingActions } from "./use-meetings";

export function Meetings({ actions, selectedId, onSelect, onTasks }: {
  actions: MeetingActions;
  selectedId: string;
  onSelect: (id: string) => void;
  onTasks: () => void;
}) {
  const [intake, setIntake] = useState<"zoom" | "import" | null>(null);
  const [error, setError] = useState("");
  const { state, busy } = actions;
  const selected = state?.meetings.find(meeting => meeting.id === selectedId);
  const runDemo = async () => {
    setError("");
    try { onSelect((await actions.demo()).id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "The demo could not be created."); }
  };
  return <div className="meetings-workspace">
    <MeetingLoadNotice actions={actions} />
    {state && <>
      {selected ? <>
        <button className="meeting-back text-button" onClick={() => onSelect("")}>
          <Icon name="chevron" className="rotate" size={15} /> All meetings
        </button>
        <MeetingDetail key={selected.id} meeting={selected} actions={actions} onTasks={onTasks} />
      </> : <>
        <div className="meeting-intro">
          <div><span className="meeting-eyebrow">CUSTOMER CONVERSATIONS</span>
            <h2>Listen. Understand. Follow through.</h2>
            <p>Your meeting agent keeps the notes, finds the product issues, and researches the companies and people mentioned.</p>
          </div>
          <button className="button primary" onClick={() => setIntake("zoom")} disabled={busy}>
            <Icon name="plus" size={17} /> Add Zoom call
          </button>
        </div>
        <div className="meeting-pipeline" aria-label="Meeting workflow">
          <span><Icon name="messages" size={17} /> Capture the conversation</span>
          <Icon name="chevron" size={14} />
          <span><Icon name="tasks" size={17} /> Review in Tasks</span>
          <Icon name="chevron" size={14} />
          <span><Icon name="check" size={17} /> Approve a GitHub issue</span>
        </div>
        <MeetingConnections capabilities={state.capabilities} />
        <div className="meeting-demo-strip">
          <div><strong>Try the customer issue demo</strong>
            <p>A fictional customer reports an empty CSV export and mentions Linear. Review the saved notes and proposed issue.</p>
            <small>Uses a saved example transcript. Approving its task creates a real GitHub issue.</small>
          </div>
          <button className="button secondary" disabled={busy} onClick={() => void runDemo()}>
            {busy ? "Working…" : "Run demo"}<Icon name="arrow" size={16} />
          </button>
        </div>
        {error && <p className="sales-error" role="alert">{error}</p>}
        <div className="meeting-list-heading"><h3>Recent meetings <span>{state.meetings.length}</span></h3>
          <button className="text-button" onClick={() => setIntake("import")} disabled={busy}>Import transcript</button>
        </div>
        {state.meetings.length ? <div className="meeting-list">
          {state.meetings.map(meeting => <button key={meeting.id} className="meeting-row" onClick={() => onSelect(meeting.id)}>
            <span className="meeting-row-icon"><Icon name="messages" size={21} /></span>
            <span className="meeting-row-copy"><strong>{meeting.title}</strong>
              <small>{meeting.source === "demo" ? "Fictional demo" : meeting.source === "import" ? "Imported transcript" : "Zoom"} · {meetingDate(meeting.created_at)}</small>
            </span>
            <MeetingStatus status={meeting.status} /><Icon name="chevron" size={17} />
          </button>)}
        </div> : <div className="meeting-empty"><Icon name="messages" size={28} /><h3>Your next conversation starts here.</h3>
          <p>Add a Zoom call, import a transcript, or run the demo to see the complete review flow.</p></div>}
      </>}
      {intake && <MeetingIntake mode={intake} actions={actions} capabilities={state.capabilities}
        onClose={() => { if (!busy) setIntake(null); }} onCreated={id => { setIntake(null); onSelect(id); }} />}
    </>}
  </div>;
}

function MeetingConnections({ capabilities: value }: { capabilities: MeetingCapabilities }) {
  return <details className="meeting-connections">
    <summary><span><Icon name="connections" size={16} /> Connections</span>
      <span className="meeting-connection-summary">
        <span className={value.zoom_ready ? "connected" : ""}>Zoom {value.zoom_ready ? "ready" : "setup needed"}</span>
        <span className={value.analysis_ready ? "connected" : ""}>Notes {value.analysis_ready ? "ready" : "setup needed"}</span>
        <span className={value.github_ready ? "connected" : ""}>GitHub {value.github_ready ? "ready" : "setup needed"}</span>
      </span>
    </summary>
    <div className="meeting-connection-detail"><p>Meeting owner: <strong>{value.owner_email}</strong></p>
      <p>Approved issues go to <strong>{value.repository}</strong>.</p>
      {!value.zoom_ready && <p>Finish the Recall.ai connection and webhook setup to invite the Zoom attendee.</p>}
      {!value.analysis_ready && <p>Connect the meeting analysis service to turn live calls and imported transcripts into notes.</p>}
      {!value.github_ready && <p>Connect GitHub to enable approval and issue publication. You can review and edit drafts now.</p>}
      {value.missing.length > 0 && <details><summary>Setup details</summary><ul>{value.missing.map(item => <li key={item}>{item}</li>)}</ul></details>}
    </div>
  </details>;
}

function MeetingIntake({ mode, actions, capabilities, onClose, onCreated }: {
  mode: "zoom" | "import";
  actions: MeetingActions;
  capabilities: MeetingCapabilities;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);
  const available = capabilities.analysis_ready && (mode === "import" || capabilities.zoom_ready);
  return <Dialog title={mode === "zoom" ? "Add a Zoom call" : "Import a customer transcript"} onClose={onClose}>
    <form className="meeting-intake" onSubmit={async event => {
      event.preventDefault();
      if (!consent || actions.busy || !available) return;
      const fields = new FormData(event.currentTarget);
      setError("");
      try {
        const title = String(fields.get("title") ?? "").trim();
        const result = mode === "zoom" ? await actions.create({
          title, meeting_url: String(fields.get("url") ?? "").trim(),
          join_at: fields.get("join_at") ? new Date(String(fields.get("join_at"))).toISOString() : null,
          consent_confirmed: true,
        }) : await actions.import({ title, segments: parseMeetingTranscript(String(fields.get("transcript") ?? "")), consent_confirmed: true });
        onCreated(result.id);
      } catch (cause) { setError(cause instanceof Error ? cause.message : "The meeting could not be saved."); }
    }}>
      <p className="meeting-form-description">{mode === "zoom"
        ? `TOIR’s named meeting attendee will join for ${capabilities.owner_email}. Admit it from the Zoom waiting room when the call starts.`
        : "Save an existing transcript and turn the customer’s feedback into notes, research, and tasks for review."}</p>
      <label className="form-label">Meeting title<input name="title" required maxLength={200} autoFocus placeholder="Customer product feedback" disabled={actions.busy} /></label>
      {mode === "zoom" ? <>
        <label className="form-label">Zoom invitation link<input name="url" type="url" required maxLength={2048} placeholder="https://us02web.zoom.us/j/…" disabled={actions.busy} /></label>
        <label className="form-label">Join at (optional)<input name="join_at" type="datetime-local" disabled={actions.busy} />
          <small>Leave blank to join now. Scheduled time uses your device’s local timezone.</small></label>
      </> : <label className="form-label">Transcript<textarea name="transcript" required rows={9} maxLength={120000} disabled={actions.busy}
        placeholder={"[00:08] Customer: The exported report is empty.\n[00:24] Curran: What did you expect to see?"} />
        <small>One speaker turn per line. Optional [MM:SS] or [HH:MM:SS] timestamps; omitted timestamps are stored as 00:00.</small></label>}
      <label className="meeting-consent"><input type="checkbox" required checked={consent} onChange={event => setConsent(event.target.checked)} disabled={actions.busy} />
        <span>{mode === "zoom" ? "I have the participants’ consent to record and transcribe this call." : "I have permission to save and analyze this transcript."}</span></label>
      {mode === "zoom" && <p className="meeting-form-hint">The bot announces that it records and transcribes. Customer issues stay in Tasks until you approve publishing them.</p>}
      {!available && <p className="sales-error">{!capabilities.analysis_ready ? "Connect the meeting analysis service before adding a live call or transcript. " : ""}{mode === "zoom" && !capabilities.zoom_ready ? "Complete Recall.ai and webhook setup before joining Zoom." : ""} The demo is available now.</p>}
      {error && <p className="sales-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button className="button secondary" type="button" disabled={actions.busy} onClick={onClose}>Cancel</button>
        <button className="button primary" type="submit" disabled={actions.busy || !consent || !available}>{actions.busy ? "Saving…" : mode === "zoom" ? "Add meeting agent" : "Save and analyze"}</button></div>
    </form>
  </Dialog>;
}

function MeetingDetail({ meeting, actions, onTasks }: { meeting: Meeting; actions: MeetingActions; onTasks: () => void }) {
  const [tab, setTab] = useState<"notes" | "transcript" | "research">("notes");
  const [error, setError] = useState("");
  const tasks = actions.state?.tasks.filter(task => task.meeting_id === meeting.id) ?? [];
  const checkTranscript = ["done", "call_ended"].includes(meeting.status);
  const retryable = checkTranscript || ["analysis_failed", "capture_failed"].includes(meeting.status) || meeting.research.some(item => ["blocked", "failed", "cancelled"].includes(item.status));
  return <article className="meeting-detail">
    <header className="meeting-detail-heading"><div><div className="meeting-detail-meta">
      <span>{meeting.source === "demo" ? "FICTIONAL DEMO" : meeting.source === "zoom" ? "ZOOM CALL" : "IMPORTED TRANSCRIPT"}</span><MeetingStatus status={meeting.status} />
    </div><h2>{meeting.title}</h2><p>{meetingDate(meeting.created_at)} · {meeting.owner_email}</p></div>
      {tasks.length > 0 && <button className="button primary" onClick={onTasks}>Review {tasks.length === 1 ? "issue" : `${tasks.length} issues`}<Icon name="arrow" size={16} /></button>}
    </header>
    {meeting.source === "demo" && <p className="meeting-source-notice">This meeting uses a fictional example transcript and saved example notes. Mention research, when connected, runs live. Approving a demo task creates a real GitHub issue.</p>}
    {meeting.error && <p className="sales-error" role="alert">{meeting.error}</p>}
    {retryable && <button className="button secondary" disabled={actions.busy} onClick={async () => {
      setError("");
      try { await actions.retryMeeting(meeting.id); }
      catch (cause) { setError(cause instanceof Error ? cause.message : "Retry failed."); }
    }}>{checkTranscript ? "Check completed transcript" : meeting.status === "analysis_failed" ? "Retry analysis" : meeting.status === "capture_failed" ? "Retry capture processing" : "Retry research"}</button>}
    {error && <p className="sales-error" role="alert">{error}</p>}
    <nav className="meeting-detail-tabs" aria-label="Meeting details">
      {(["notes", "transcript", "research"] as const).map(name => <button key={name} aria-pressed={tab === name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
        {name === "notes" ? "Customer notes" : name === "transcript" ? `Transcript (${meeting.transcript.length})` : `Related research (${meeting.research.length})`}
      </button>)}
    </nav>
    {tab === "notes" && (meeting.notes ? <div className="meeting-notes">
      <section><h3>What we heard</h3><p className="meeting-summary">{meeting.notes.summary}</p>
        {meeting.notes.customer && <p className="meeting-customer"><Icon name="profile" size={17} /> {meeting.notes.customer}</p>}</section>
      <div className="meeting-notes-columns"><NoteList title="Customer needs" items={meeting.notes.needs} /><NoteList title="Next steps" items={meeting.notes.next_steps} /></div>
      <section className="meeting-issue-findings"><h3>Product issues <span>{meeting.notes.issues.length}</span></h3>
        {meeting.notes.issues.length ? meeting.notes.issues.map((issue, index) => <div className="meeting-finding" key={index}>
          <h4>{issue.title}</h4><p>{issue.problem}</p>
          {issue.impact && <p><strong>Customer impact:</strong> {issue.impact}</p>}
          <details><summary>Evidence from the conversation</summary>{issue.evidence.map((item, i) => {
            const segment = meeting.transcript.find(part => part.id === item.segment_id);
            return <blockquote key={i}><p>“{item.quote}”</p><cite>{segment ? `${segment.speaker} · ${meetingTime(segment.start)}` : item.segment_id}</cite></blockquote>;
          })}</details>
        </div>) : <p>No product issue was identified in this conversation.</p>}
      </section>
    </div> : <div className="meeting-empty"><Icon name="messages" size={27} /><h3>{["analysis_failed", "capture_failed", "join_unknown"].includes(meeting.status) ? "This meeting needs attention." : "Your notes will appear here."}</h3>
      <p>{meeting.status === "in_waiting_room" ? "Admit the TOIR attendee from the Zoom waiting room." : "After the call, the agent saves the customer’s needs, supporting quotes, and proposed engineering issues."}</p></div>)}
    {tab === "transcript" && <section className="meeting-transcript" aria-label="Saved transcript">
      {meeting.transcript.length ? meeting.transcript.map(segment => <div className="meeting-transcript-turn" key={segment.id}>
        <time>{meetingTime(segment.start)}</time><div><strong>{segment.speaker}</strong><p>{segment.text}</p></div>
      </div>) : <div className="meeting-empty"><p>Transcript segments appear here as they arrive from the call.</p></div>}
    </section>}
    {tab === "research" && <section className="meeting-research">
      <p className="meeting-research-intro">Named companies and people are researched using public business information. Findings stay linked to their sources.</p>
      {meeting.research.length ? meeting.research.map(item => <article className="meeting-research-item" key={item.id}>
        <header><div><span className="meeting-eyebrow">{item.kind}</span><h3>{item.name}</h3></div><MeetingStatus status={item.status} /></header>
        <p>{item.context}</p><blockquote>“{item.evidence.quote}”</blockquote>
        {item.identity_status === "ambiguous" && <p className="meeting-source-notice">The identity is ambiguous. No confirmed match has been established.</p>}
        {item.identity_status === "not_found" && <p className="meeting-source-notice">A reliable identity match was not found.</p>}
        {item.summary && <p className="meeting-research-summary">{item.summary}</p>}
        {!!item.facts?.length && <ul className="meeting-research-facts">{item.facts.map((fact, index) => <li key={index}>{fact.claim}
          <details className="sales-evidence"><summary>Supporting sources</summary>{fact.citations.map((citation, citationIndex) => {
            const source = item.sources?.find(candidate => candidate.id === citation.source_id);
            const href = evidenceUrl(source?.url);
            return <div className="citation" key={citationIndex}>{href && <a href={href} target="_blank" rel="noopener noreferrer">{source?.title || "Source"}<Icon name="link" size={12} /></a>}
              <blockquote>{citation.quote}</blockquote></div>;
          })}</details>
        </li>)}</ul>}
        {!!item.gaps?.length && <><h4>What remains unclear</h4><ul className="meeting-research-gaps">{item.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></>}
        {item.error && <p className="sales-error">{item.error}</p>}
        {!!item.sources?.length && <ul className="meeting-research-sources">{item.sources.map((source, index) => {
          const href = evidenceUrl(source.url);
          return href ? <li key={index}><a href={href} target="_blank" rel="noopener noreferrer">{source.title || new URL(href).hostname}<Icon name="link" size={12} /></a></li> : null;
        })}</ul>}
        {item.run_id && <a href="/research" className="text-button">Open research workspace <Icon name="arrow" size={13} /></a>}
      </article>) : <div className="meeting-empty"><Icon name="search" size={26} /><h3>No research subjects yet.</h3><p>Explicitly mentioned companies and people will appear after notes are ready.</p></div>}
    </section>}
  </article>;
}
function NoteList({ title, items }: { title: string; items: string[] }) {
  return <section><h3>{title}</h3>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="meeting-muted">None recorded.</p>}</section>;
}
