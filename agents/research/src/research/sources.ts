import { createHash } from "node:crypto";
import { isIP } from "node:net";
import type { Citation, Report, Source } from "./contracts";
import {
  EMPTY_REPORT, validateReportEnvelope, validateLead, validateCompetitor, reportSchema,
} from "./contracts";
import type { ErrorObject } from "ajv";
import { ResearchError } from "./budget";

export function publicURL(raw: string): string {
  const url = new URL(raw);
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    (url.port && !["80", "443"].includes(url.port))
  )
    throw new ResearchError("url", "Only public HTTP(S) sources are allowed.");
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    isIP(host) ||
    !host.includes(".") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host.endsWith(".localhost") ||
    host === "localhost"
  )
    throw new ResearchError("url", "Only public HTTP(S) sources are allowed.");
  url.hash = "";
  return url.toString();
}
const sourceID = (url: string) =>
  `src_${createHash("sha256").update(url).digest("hex").slice(0, 16)}`;
const norm = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();
// The generated schema cannot express Python's domain validator. Require a
// hostname here; never repair a model's unknown domain or rewrite its identity.
const companyDomainOK = (domain: string) =>
  domain.includes(".") &&
  !isIP(domain) &&
  domain.split(".").every((label) =>
    /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label),
  );
// Diagnostics contain contract paths and validator rules only, never model values.
const schemaFields = new Set<string>();
function collectFields(node: unknown) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    node.forEach(collectFields);
    return;
  }
  const object = node as Record<string, unknown>;
  if (object.properties && typeof object.properties === "object") {
    Object.keys(object.properties).forEach((field) => schemaFields.add(field));
  }
  Object.values(object).forEach(collectFields);
}
collectFields(reportSchema);
export class ReportValidationError extends ResearchError {
  readonly issues: { path: string; rule: string }[];
  constructor(errors: ErrorObject[] | null | undefined) {
    const issues = (errors ?? []).slice(0, 5).map((error) => {
      const parts = error.instancePath.split("/").filter(Boolean);
      if (error.keyword === "required")
        parts.push(String(error.params.missingProperty));
      const path =
        "/" +
        parts
          .map((part) =>
            schemaFields.has(part) || /^\d{1,3}$/.test(part) ? part : "[field]",
          )
          .join("/");
      return { path: path.slice(0, 100), rule: error.keyword };
    });
    super(
      "report",
      "Research report failed validation: " +
        issues.map((issue) => `${issue.path} (${issue.rule})`).join("; ") +
        ".",
    );
    this.issues = issues;
  }
}
export class Sources {
  private items = new Map<string, Source>();
  constructor(prior: Source[] = []) {
    for (const source of prior) {
      try {
        const url = publicURL(source.url);
        if (source.id === sourceID(url))
          this.items.set(source.id, { ...source, url });
      } catch {
        /* invalid prior source is not trusted */
      }
    }
  }
  list() {
    return [...this.items.values()];
  }
  add(raw: Record<string, unknown>): Source | null {
    if (
      typeof raw.url !== "string" ||
      typeof raw.text !== "string" ||
      !raw.text.trim()
    )
      return null;
    let url: string;
    try {
      url = publicURL(raw.url);
    } catch {
      return null;
    }
    const id = sourceID(url);
    if (!this.items.has(id) && this.items.size >= 100) return null;
    const published =
      typeof raw.publishedDate === "string"
        ? raw.publishedDate.slice(0, 10)
        : null;
    const source: Source = {
      id,
      url,
      title:
        typeof raw.title === "string"
          ? raw.title.slice(0, 500)
          : new URL(url).hostname,
      retrieved_at: new Date().toISOString(),
      published_at:
        published &&
        /^\d{4}-\d{2}-\d{2}$/.test(published) &&
        Number.isFinite(Date.parse(published)) &&
        new Date(published).toISOString().slice(0, 10) === published
          ? published
          : null,
      text: raw.text.slice(0, 12000),
    };
    // Keep a longer retrieved excerpt when a later search returns only a short snippet.
    const existing = this.items.get(id);
    if (existing && existing.text.length > source.text.length) return existing;
    this.items.set(id, source);
    return source;
  }
  finalize(value: unknown): Report {
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new ResearchError(
        "report",
        "Research did not return a valid structured report.",
      );
    // Pydantic default_factory fields are optional in the shared JSON schema.
    // Apply those defaults before using the validated TypeScript shape.
    const envelope = {
      ...structuredClone(EMPTY_REPORT),
      ...value,
      // These fields are host-owned. Planner prose or an overlong model summary
      // must not discard otherwise valid evidence; the coordinator writes it.
      summary: "",
      sources: this.list(),
    };
    if (!validateReportEnvelope(envelope))
      throw new ReportValidationError(validateReportEnvelope.errors);
    const report: Report = {
      ...envelope,
      leads: envelope.leads.filter(
        (candidate): candidate is Report["leads"][number] =>
          validateLead(candidate) && companyDomainOK(candidate.domain),
      ),
      competitors: envelope.competitors.filter((candidate) => validateCompetitor(candidate)),
    };
    const removed: string[] = [];
    for (const [label, count] of [
      ["company", envelope.leads.length - report.leads.length],
      ["competitor", envelope.competitors.length - report.competitors.length],
    ] as const) {
      if (count)
        removed.push(`Excluded ${count} ${label} candidate${count === 1 ? "" : "s"}: invalid report fields.`);
    }
    const citationOK = (c: Citation) => {
      const s = this.items.get(c.source_id);
      const quote = norm(c.quote);
      return !!s && quote.length >= 12 && norm(s.text).includes(quote);
    };
    report.leads = report.leads.filter((l) => {
      const ok =
        l.identity_citations.every(citationOK) &&
        l.signals.every((s) => s.citations.every(citationOK));
      if (!ok)
        removed.push(
          `Excluded ${l.company}: a quotation was not present in retrieved evidence.`,
        );
      return ok;
    });
    report.competitors = report.competitors.filter((c) => {
      const ok = c.citations.every(citationOK);
      if (!ok)
        removed.push(
          `Excluded ${c.company}: competitor evidence did not support the quotation.`,
        );
      return ok;
    });
    report.gaps = [...new Set([...removed, ...report.gaps])].slice(0, 40);
    return report;
  }
}
