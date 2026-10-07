import Ajv from "ajv";
import addFormats from "ajv-formats";
import reportSchema from "../../contracts/report.schema.json";
import briefSchema from "../../contracts/brief.schema.json";

export interface Source {
  id: string;
  url: string;
  title: string;
  retrieved_at: string;
  published_at: string | null;
  text: string;
}
export interface Citation {
  source_id: string;
  quote: string;
}
export interface Lead {
  company: string;
  domain: string;
  country: "US";
  employee_count?: number | null;
  identity_citations: Citation[];
  decision_maker?: string | null;
  signals: {
    kind: "leadership" | "funding" | "partnership" | "business_need";
    claim: string;
    event_date: string | null;
    citations: Citation[];
  }[];
  ai_use_case: string;
  rationale: string;
  outreach_angle: string;
  fit_score: number;
}
export interface Report {
  leads: Lead[];
  competitors: {
    company: string;
    kind: "advertisement" | "marketing" | "pricing" | "customer";
    claim: string;
    citations: Citation[];
    positioning_hypothesis: string;
  }[];
  sources: Source[];
  gaps: string[];
  summary: string;
}
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
export interface TaskRequest {
  version: 1;
  task_id: string;
  run_id: string;
  brief: Brief;
  deadline_at: string;
  queries: string[];
  focus: string;
  prior_report: Report;
  usage: Record<string, number>;
}
export type Usage = Record<
  "searches" | "pages" | "model_turns" | "input_tokens" | "output_tokens",
  number
>;
export interface TaskStatus {
  version: 1;
  task_id: string;
  run_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  progress: string;
  usage: Usage;
  sources: Source[];
  report?: Report;
  error?: string;
}
export const EMPTY_REPORT: Report = {
  leads: [],
  competitors: [],
  sources: [],
  gaps: [],
  summary: "",
};
const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);
export const validateReport = ajv.compile<Report>(reportSchema);
const validateBrief = ajv.compile<Brief>(briefSchema);
export { reportSchema };
export function validTask(value: unknown): value is TaskRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as TaskRequest;
  const keys = [
    "version",
    "task_id",
    "run_id",
    "brief",
    "deadline_at",
    "queries",
    "focus",
    "prior_report",
    "usage",
  ];
  return (
    Object.keys(v).every((k) => keys.includes(k)) &&
    v.version === 1 &&
    typeof v.task_id === "string" &&
    /^[A-Za-z0-9_-]{1,100}$/.test(v.task_id) &&
    typeof v.run_id === "string" &&
    /^[A-Za-z0-9_-]{1,100}$/.test(v.run_id) &&
    validateBrief(v.brief) &&
    v.brief.employee_min <= v.brief.employee_max &&
    typeof v.deadline_at === "string" &&
    Number.isFinite(Date.parse(v.deadline_at)) &&
    Array.isArray(v.queries) &&
    v.queries.length >= 1 &&
    v.queries.length <= 12 &&
    v.queries.every(
      (q) => typeof q === "string" && q.length > 0 && q.length <= 1000,
    ) &&
    typeof v.focus === "string" &&
    v.focus.length <= 2000 &&
    validateReport(v.prior_report) &&
    !!v.usage &&
    typeof v.usage === "object" &&
    !Array.isArray(v.usage) &&
    Object.values(v.usage).every((n) => Number.isSafeInteger(n) && n >= 0)
  );
}
export function canonicalJSON(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJSON).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonicalJSON(v)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
