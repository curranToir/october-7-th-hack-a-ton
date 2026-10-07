/** Database-facing contracts. The UI only consumes this local demo adapter for now. */
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
