import type { MeetingActions } from "./use-meetings";
import { Icon } from "./icon";

const labels: Record<string, string> = {
  scheduled: "Scheduled", joining: "Joining Zoom", joining_call: "Joining Zoom",
  in_waiting_room: "Waiting for host", in_call_not_recording: "In call · awaiting recording",
  in_call_recording: "Listening", call_ended: "Preparing transcript", done: "Preparing transcript",
  join_unknown: "Check bot in Recall", capture_failed: "Capture needs attention",
  analysis_queued: "Notes queued", analyzing: "Writing notes", ready: "Notes ready",
  analysis_failed: "Analysis needs attention", pending: "Needs approval",
  approved: "Approved · queued", publishing: "Sending to GitHub", published: "Issue created",
  rejected: "Rejected", publish_failed: "Publication failed", publish_unknown: "Check GitHub result",
  queued: "Research queued", blocked: "Connection needed", started: "Research started",
  running: "Researching", completed: "Research complete", failed: "Research failed",
  cancelled: "Research cancelled", interrupted: "Research interrupted",
};
export function MeetingStatus({ status }: { status: string }) {
  const tone = ["ready", "published", "completed"].includes(status) ? "complete"
    : ["capture_failed", "analysis_failed", "publish_failed", "publish_unknown", "join_unknown", "blocked", "failed"].includes(status) ? "attention"
      : ["rejected", "cancelled"].includes(status) ? "muted" : "active";
  return <span className={`meeting-status ${tone}`}>{labels[status] ?? status.replaceAll("_", " ")}</span>;
}
export function MeetingLoadNotice({ actions }: { actions: MeetingActions }) {
  if (actions.error) return <div className="meeting-load-error" role="alert">
    <Icon name="info" size={17} /><span>{actions.error}</span>
    <button className="text-button" onClick={() => void actions.refresh(true)}>Retry connection</button>
  </div>;
  if (!actions.state) return <p className="meeting-loading" role="status">
    {actions.loading ? "Loading meetings…" : "No meeting data is available."}
  </p>;
  return null;
}
export function meetingDate(value: string) {
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
export function meetingTime(seconds: number) {
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
