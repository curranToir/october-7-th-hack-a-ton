import Ajv from "ajv";
import addFormats from "ajv-formats";
import taskSchema from "../../contracts/task.schema.json";
import reportSchema from "../../contracts/report.schema.json";
import type { Citation, Source, Usage } from "../../../research/src/research/contracts";
export type { Citation, Source, Usage };

export interface Company {
  name: string;
  domain: string;
  description: string;
  country: string | null;
  employee_count: number | null;
  citations: Citation[];
  fit_score: number;
  sales_angle: string;
}
export interface Contact {
  id: string;
  name: string;
  title: string;
  company_domain: string;
  buying_relevance: string;
  linkedin_url: string | null;
  email: string | null;
  phone: string | null;
  citations: Citation[];
  linkedin_citations: Citation[];
  email_citations: Citation[];
  phone_citations: Citation[];
}
export interface ContactReport {
  candidates: Company[];
  company: Company | null;
  contacts: Contact[];
  sources: Source[];
  gaps: string[];
  summary: string;
}
export interface ContactTask {
  version: 1;
  task_id: string;
  run_id: string;
  mode: "resolve" | "enrich";
  query: string;
  company?: Company | null;
  deadline_at: string;
  prior_sources: Source[];
  usage: Record<string, number>;
}
export interface ContactStatus {
  version: 1;
  task_id: string;
  run_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  progress: string;
  usage: Usage;
  sources: Source[];
  report?: ContactReport;
  error?: string;
}
export const EMPTY_REPORT: ContactReport = {
  candidates: [], company: null, contacts: [], sources: [], gaps: [], summary: "",
};
const ajv = new Ajv({ strict: false, allErrors: true, useDefaults: true });
addFormats(ajv);
const validateTask = ajv.compile<ContactTask>(taskSchema);
export const validateReport = ajv.compile<ContactReport>(reportSchema);
export { reportSchema };
export function validTask(value: unknown): value is ContactTask {
  if (!validateTask(value)) return false;
  const task = value as ContactTask;
  task.prior_sources ??= [];
  task.usage ??= {};
  return /^[A-Za-z0-9_-]{1,100}$/.test(task.task_id)
    && /^[A-Za-z0-9_-]{1,100}$/.test(task.run_id)
    && Number.isFinite(Date.parse(task.deadline_at))
    && Object.values(task.usage).every((n) => Number.isSafeInteger(n) && n >= 0)
    && (task.mode !== "enrich" || !!task.company);
}
