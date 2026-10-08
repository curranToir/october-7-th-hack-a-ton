/** UI presentation types. Live sales contracts below mirror the coordinator models.
 * Legacy demo adapters remain isolated test fixtures and are never loaded by the app. */
export type Theme = "light" | "dark" | "system";
export type Decision = "approved" | "denied";
export type SettingPage =
  | "general"
  | "profile"
  | "agents"
  | "connections"
  | "notifications"
  | "privacy"
  | "workspace"
  | "billing";

export interface Preferences {
  theme: Theme;
  compactSidebar: boolean;
  timeZone: string;
  defaultAgent: string;
  autoAdvance: boolean;
  requireApproval: boolean;
  notifyTasks: boolean;
  notifyCompleted: boolean;
  sound: boolean;
  saveHistory: boolean;
}
export interface Profile {
  name: string;
  email: string;
  role: string;
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  taskIds?: string[];
}
export interface Session {
  id: string;
  title: string;
  agent: string;
  createdAt: string;
  messages: Message[];
}
export interface Task {
  id: string;
  sessionId: string;
  agent: string;
  title: string;
  subject: string;
  subtitle: string;
  finding: string;
  source: string;
  sourceDetail: string;
  steps: string[];
  scope: string;
  restriction: string;
  approveLabel: string;
  status: "pending" | Decision;
  createdAt: string;
  decidedAt?: string;
}
export interface Connection {
  id: string;
  name: string;
  description: string;
  connected: boolean;
  initials: string;
}
export interface WorkspaceState {
  version: 1;
  profile: Profile;
  preferences: Preferences;
  workspace: { name: string; description: string };
  sessions: Session[];
  tasks: Task[];
  connections: Connection[];
}
export interface ReadResult {
  state: WorkspaceState;
  warning?: string;
}
export interface StoragePort {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface SalesRecord {
  id: string;
  workspace_id: "toir";
  created_at: string;
  updated_at: string;
}
export interface SalesCitation {
  source_id: string;
  quote: string;
}
export interface SalesSource {
  id: string;
  url: string;
  title: string;
  retrieved_at: string;
  published_at?: string | null;
  text: string;
}
export interface SalesCompany {
  name: string;
  domain: string;
  description: string;
  country: string | null;
  employee_count: number | null;
  citations: SalesCitation[];
  fit_score: number;
  sales_angle: string;
}
export interface SalesContact {
  id: string;
  name: string;
  title: string;
  company_domain: string;
  buying_relevance: string;
  linkedin_url: string | null;
  email: string | null;
  phone: string | null;
  citations: SalesCitation[];
  linkedin_citations: SalesCitation[];
  email_citations: SalesCitation[];
  phone_citations: SalesCitation[];
}
export interface SalesSession extends SalesRecord {
  title: string;
  owner: string;
  automation: boolean;
  deleted: boolean;
}
export interface SalesMessage extends SalesRecord {
  session_id: string;
  role: "user" | "assistant";
  content: string;
  task_ids: string[];
  job_id: string | null;
  request_key: string | null;
}
export interface SalesAutomation extends SalesRecord {
  enabled: boolean;
  owner: string;
  request: string;
  geography: "US";
  employee_min: number;
  employee_max: number;
  fit_threshold: number;
  daily_enrichments: number;
  daily_discoveries: number;
  timezone: "America/Los_Angeles";
}
export type AutomationUpdate = Pick<
  SalesAutomation,
  | "enabled"
  | "request"
  | "employee_min"
  | "employee_max"
  | "fit_threshold"
  | "daily_enrichments"
  | "daily_discoveries"
>;
export interface SalesJob extends SalesRecord {
  session_id: string;
  requested_by: string;
  kind: "chat" | "discovery" | "resolve" | "enrich";
  origin: "chat" | "background";
  status:
    | "queued"
    | "running"
    | "needs_input"
    | "completed"
    | "failed"
    | "cancelled"
    | "interrupted";
  query: string;
  propose_crm: boolean;
  company: SalesCompany | null;
  candidates: SalesCompany[];
  sources: SalesSource[];
  report: {
    candidates: SalesCompany[];
    company: SalesCompany | null;
    contacts: SalesContact[];
    sources: SalesSource[];
    gaps: string[];
    summary: string;
  } | null;
  task_id: string | null;
  research_run_id: string | null;
  memory_status?: "not_requested" | "pending" | "synced" | "blocked";
  deadline_at: string | null;
  progress: string;
  error: string | null;
  budget_day: string | null;
  parent_id: string | null;
}
export interface CRMOperation {
  id: string;
  kind: "company" | "contact" | "association" | "note";
  action: "create" | "update" | "associate";
  contact_id: string | null;
  record_id: string | null;
  properties: Record<string, string | number | null>;
  before: Record<string, string | number | null>;
  depends_on: string[];
  status: "pending" | "running" | "succeeded" | "failed" | "uncertain";
  result_id: string | null;
  error: string | null;
}
export interface SalesProposal extends SalesRecord {
  session_id: string;
  job_id: string;
  requested_by: string;
  company: SalesCompany;
  contacts: SalesContact[];
  sources: SalesSource[];
  gaps: string[];
  version: number;
  status: "pending" | Decision;
  execution:
    | "not_started"
    | "queued"
    | "running"
    | "succeeded"
    | "partial"
    | "failed"
    | "needs_review";
  operations: CRMOperation[];
  excluded_contact_ids: string[];
  excluded_operation_ids: string[];
  excluded_fields: Record<string, string[]>;
  decided_by: string | null;
  decided_at: string | null;
  error: string | null;
  memory_status: "pending" | "synced" | "blocked";
}
export interface SalesCapabilities {
  ready: boolean;
  research_ready?: boolean;
  reasons: string[];
  postgres: boolean;
  research: boolean;
  contacts: boolean;
  crm: boolean;
  brain: boolean;
  auth: boolean;
}
export interface SalesSnapshot {
  user: { email: string; name: string; workspace_id: "toir" };
  sessions: SalesSession[];
  messages: SalesMessage[];
  tasks: SalesProposal[];
  jobs: SalesJob[];
  automation: SalesAutomation;
  capabilities: SalesCapabilities;
}
export interface LiveWorkspace extends WorkspaceState {
  proposals: SalesProposal[];
  jobs: SalesJob[];
  automation: SalesAutomation;
  capabilities: SalesCapabilities;
}

/** Browser logins are separate from shared sales conversations. No bearer tokens. */
export interface WorkspaceUser {
  id: string;
  email: string;
  name: string;
  workspace_id: "toir";
  role: "sales" | "engineering";
  email_verified: boolean;
}

export interface AuthSession {
  id: string;
  created_at: number;
  last_seen_at: number;
  expires_at: number;
  user_agent: string;
  current: boolean;
}
