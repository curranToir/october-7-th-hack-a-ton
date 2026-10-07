/** Contract v1, mirrored from apps/orchestrator/models/research.py. */
export interface Brief {
  request: string;
  geography: "US";
  employee_min: number;
  employee_max: number;
  target_count: number;
  leadership_days: number;
  signal_days: number;
  deadline_seconds: number;
}
export interface Citation { source_id: string; quote: string }
export interface Source {
  id: string; url: string; title: string; retrieved_at: string;
  published_at: string | null; text: string;
}
export interface Signal {
  kind: "leadership" | "funding" | "partnership" | "business_need";
  claim: string; event_date: string | null; citations: Citation[];
}
export interface Lead {
  company: string; domain: string; country: "US"; employee_count: number | null;
  identity_citations: Citation[]; decision_maker: string | null;
  signals: Signal[]; ai_use_case: string; rationale: string;
  outreach_angle: string; fit_score: number;
}
export interface CompetitorFact {
  company: string; kind: "advertisement" | "marketing" | "pricing" | "customer";
  claim: string; citations: Citation[]; positioning_hypothesis: string;
}
export interface Report {
  leads: Lead[]; competitors: CompetitorFact[]; sources: Source[];
  gaps: string[]; summary: string;
}
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export interface Run {
  id: string; parent_id: string | null; status: RunStatus; stage: string;
  brief: Brief; created_at: string; updated_at: string; deadline_at: string;
  task_id: string | null; pass_number: number; report: Report;
  plan: { queries: string[]; focus: string } | null;
  error: string | null; usage: Record<string, number>; trace_id: string | null;
}
export interface RunEvent {
  sequence: number; run_id: string; created_at: string; stage: string; message: string;
}
export interface RunDetail { run: Run; events: RunEvent[] }
export interface Capabilities { configured: boolean; missing: string[]; maintenance?: boolean }
