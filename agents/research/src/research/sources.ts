import { createHash } from "node:crypto";
import { isIP } from "node:net";
import type { Citation, Report, Source } from "./contracts";
import { EMPTY_REPORT, validateReport } from "./contracts";
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
    if (!value || typeof value !== "object")
      throw new ResearchError(
        "report",
        "Research did not return a valid structured report.",
      );
    // Pydantic default_factory fields are optional in the shared JSON schema.
    // Apply those defaults before using the validated TypeScript shape.
    const report = { ...structuredClone(EMPTY_REPORT), ...value, sources: this.list() };
    if (!validateReport(report))
      throw new ResearchError(
        "report",
        "Research did not return a valid structured report.",
      );
    const citationOK = (c: Citation) => {
      const s = this.items.get(c.source_id);
      const quote = norm(c.quote);
      return !!s && quote.length >= 12 && norm(s.text).includes(quote);
    };
    const removed: string[] = [];
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
    report.gaps = [...new Set([...report.gaps, ...removed])].slice(0, 40);
    return report;
  }
}
