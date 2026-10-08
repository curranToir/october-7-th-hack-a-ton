/** Public meeting API contracts. Provider credentials stay on the server. */
export type MeetingSegment = {
  id: string;
  speaker: string;
  start: number;
  text: string;
};
export type MeetingEvidence = { segment_id: string; quote: string };
export type MeetingIssueDraft = {
  title: string;
  problem: string;
  impact: string;
  expected_behavior: string;
  reproduction_steps: string[];
  evidence: MeetingEvidence[];
};
export type MeetingMention = {
  kind: "company" | "person";
  name: string;
  context: string;
  evidence: MeetingEvidence;
};
export type MeetingResearch = MeetingMention & {
  id: string;
  status: string;
  run_id: string | null;
  error: string | null;
  summary?: string;
  sources?: { id?: string; title: string; url: string }[];
  identity_status?: "matched" | "ambiguous" | "not_found";
  facts?: { claim: string; citations: { source_id: string; quote: string }[] }[];
  gaps?: string[];
};
export type MeetingNotes = {
  summary: string;
  customer: string;
  needs: string[];
  next_steps: string[];
  issues: MeetingIssueDraft[];
  mentions: MeetingMention[];
};
export type Meeting = {
  id: string;
  title: string;
  owner_email: string;
  source: "zoom" | "import" | "demo";
  status: string;
  created_at: string;
  error: string | null;
  bot_id: string | null;
  transcript: MeetingSegment[];
  notes: MeetingNotes | null;
  research: MeetingResearch[];
};
export type MeetingTask = {
  id: string;
  meeting_id: string;
  title: string;
  body: string;
  repository: string;
  version: number;
  status: string;
  source: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  issue_url: string | null;
  error: string | null;
};
export type MeetingCapabilities = {
  owner_email: string;
  repository: string;
  zoom_ready: boolean;
  analysis_ready: boolean;
  github_ready: boolean;
  missing: string[];
};
export type MeetingWorkspace = {
  meetings: Meeting[];
  tasks: MeetingTask[];
  capabilities: MeetingCapabilities;
};
export type MeetingCreate = {
  title: string;
  meeting_url: string;
  join_at?: string | null;
  consent_confirmed: true;
};
export type MeetingImport = {
  title: string;
  segments: MeetingSegment[];
  consent_confirmed: true;
};
