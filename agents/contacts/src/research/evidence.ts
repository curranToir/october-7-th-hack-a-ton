import { createHash } from "node:crypto";
import { Sources, publicURL } from "../../../research/src/research/sources";
import { ResearchError } from "../../../research/src/research/budget";
import { EMPTY_REPORT, validateReport, type Citation, type Company, type ContactReport, type ContactTask, type Source } from "./contracts";

const norm = (value: string) => value.replace(/\s+/g, " ").trim().toLowerCase();
// Used only after fresh company evidence independently verifies the same domain.
// Do not equate brands by substring or strip suffix-like words inside a name.
const identityName = (value: string) => norm(value).replace(
  /(?:,\s*|\s+)(?:l\.?l\.?c\.?|inc\.?|incorporated|corp\.?|corporation|ltd\.?|limited)$/,
  "",
).trim();
export function domain(raw: string): string {
  const url = new URL(publicURL(raw.includes("://") ? raw : `https://${raw}`));
  return url.hostname.toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
}
export function linkedin(raw: string): string | null {
  try {
    const url = new URL(publicURL(raw));
    if (url.protocol !== "https:" || !["linkedin.com", "www.linkedin.com"].includes(url.hostname)
        || !/^\/in\/[^/]+\/?$/.test(url.pathname)) return null;
    return `https://www.linkedin.com${url.pathname.replace(/\/$/, "")}`;
  } catch { return null; }
}

/** Only registry text is evidence. The model cannot supply or replace sources. */
export function finalizeReport(value: unknown, task: ContactTask, registry: Sources): ContactReport {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ResearchError("report", "Contact research did not return a structured report.");
  const report = { ...structuredClone(EMPTY_REPORT), ...structuredClone(value), sources: registry.list() };
  // Pydantic's factory defaults do not appear as JSON-schema defaults.
  if (Array.isArray(report.contacts)) report.contacts = report.contacts.map((contact) => ({
    ...contact, id: contact?.id ?? "", linkedin_citations: contact?.linkedin_citations ?? [],
    email_citations: contact?.email_citations ?? [], phone_citations: contact?.phone_citations ?? [],
  }));
  if (!validateReport(report))
    throw new ResearchError("report", "Contact report failed the shared contract validation.");
  const sources = new Map<string, Source>(registry.list().map((source) => [source.id, source]));
  const valid = (citation: Citation) => {
    const source = sources.get(citation.source_id);
    return !!source && norm(citation.quote).length >= 12 && norm(source.text).includes(norm(citation.quote));
  };
  const validAll = (citations: Citation[]) => citations.length > 0 && citations.every(valid);
  const gap = (message: string) => { report.gaps.push(message); };
  const companyOK = (company: Company): Company | null => {
    let host: string;
    try { host = domain(company.domain); } catch { return null; }
    if (!validAll(company.citations)) return null;
    // A retrieved company page, or an explicit domain reference, must bind the identity.
    if (!company.citations.some((citation) => {
      const source = sources.get(citation.source_id)!;
      const sourceHost = new URL(source.url).hostname.replace(/^www\./, "");
      return norm(citation.quote).includes(norm(company.name))
        && (sourceHost === host || sourceHost.endsWith(`.${host}`) || norm(citation.quote).includes(host));
    })) return null;
    return { ...company, domain: host };
  };
  const candidates = new Map<string, Company>();
  for (const candidate of report.candidates) {
    const verified = companyOK(candidate);
    if (verified) candidates.set(verified.domain, verified);
    else gap("Excluded a company candidate with unsupported identity or domain evidence.");
  }
  report.candidates = [...candidates.values()];
  if (report.company) {
    report.company = companyOK(report.company);
    if (!report.company) gap("The proposed company identity lacked verified name and domain evidence.");
  }
  if (task.mode === "resolve") {
    if (report.company) candidates.set(report.company.domain, report.company);
    if (candidates.size > 1) {
      report.company = null;
      report.candidates = [...candidates.values()].slice(0, 5);
      gap("Multiple company identities found; select a company before contact research.");
    }
    report.contacts = [];
  } else {
    let selected = task.company ? companyOK(task.company) : null;
    // Discovery may have a third-party citation without the explicit website.
    // Fresh evidence can confirm that same identity; it cannot change the target.
    if (!selected && task.company && report.company
        && domain(task.company.domain) === report.company.domain
        && identityName(task.company.name)
        && identityName(task.company.name) === identityName(report.company.name)) {
      // The returned name must occur literally in the fresh quotation, including
      // downstream coordinator review. Preserve all coordinator-owned sales data.
      selected = { ...task.company, name: report.company.name,
        domain: report.company.domain, citations: report.company.citations };
    }
    if (!selected) throw new ResearchError("report", "The selected company requires verified identity evidence.");
    // The selected company is coordinator-owned. Never allow model output to switch it.
    if (report.company && report.company.domain !== selected.domain)
      gap("Ignored a different company returned during contact research.");
    report.company = selected;
    report.candidates = [];
    const seen = new Set<string>();
    report.contacts = report.contacts.filter((contact) => {
      let host: string;
      try { host = domain(contact.company_domain); } catch { return false; }
      const sameCompany = host === selected.domain;
      const identityOK = validAll(contact.citations) && contact.citations.some((citation) => {
        const quote = norm(citation.quote);
        const source = sources.get(citation.source_id)!;
        const sourceHost = new URL(source.url).hostname.replace(/^www\./, "");
        return !/\b(former|previously|departed|stepped down|no longer|ex-)\b/.test(quote)
          && quote.includes(norm(contact.name)) && quote.includes(norm(contact.title))
          && (quote.includes(norm(selected.name)) || quote.includes(host)
            || sourceHost === host || sourceHost.endsWith(`.${host}`));
      });
      if (!sameCompany || !identityOK) {
        gap(`Excluded ${contact.name}: current role and selected-company evidence were not supported.`);
        return false;
      }
      contact.company_domain = host;
      // Each field needs its own exact quote. A URL may be verified by the source URL itself.
      const supports = (citations: Citation[], check: (quote: string, source: Source) => boolean) =>
        validAll(citations) && citations.some((citation) => {
          const source = sources.get(citation.source_id)!;
          return norm(citation.quote).includes(norm(contact.name)) && check(citation.quote, source);
        });
      if (contact.linkedin_url) {
        const url = linkedin(contact.linkedin_url);
        const supported = !!url && supports(contact.linkedin_citations, (quote, source) =>
          linkedin(source.url) === url || [...quote.matchAll(/https:\/\/[^\s<>"')\]]+/g)].some(([raw]) => linkedin(raw.replace(/[.,;:]+$/, "")) === url));
        if (supported) contact.linkedin_url = url;
        else {
          contact.linkedin_url = null; contact.linkedin_citations = [];
          gap(`Missing verified LinkedIn profile for ${contact.name}.`);
        }
      } else { contact.linkedin_citations = []; gap(`Missing verified LinkedIn profile for ${contact.name}.`); }
      if (contact.email) {
        const email = contact.email.trim().toLowerCase();
        if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
            && supports(contact.email_citations, (quote) => {
              const addresses: string[] = quote.toLowerCase().match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/g) ?? [];
              return addresses.includes(email);
            })) contact.email = email;
        else { contact.email = null; contact.email_citations = []; gap(`Missing verified business email for ${contact.name}.`); }
      } else { contact.email_citations = []; gap(`Missing verified business email for ${contact.name}.`); }
      if (contact.phone) {
        const digits = contact.phone.replace(/\D/g, "");
        if (digits.length < 7 || digits.length > 15 || !supports(contact.phone_citations,
          (quote) => quote.replace(/\D/g, "").includes(digits))) {
          contact.phone = null; contact.phone_citations = [];
          gap(`Missing verified business phone for ${contact.name}.`);
        }
      } else contact.phone_citations = [];
      const key = `${host}:${norm(contact.name)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      // Stable host-issued IDs support proposal revisions; never trust model-issued IDs.
      const hash = createHash("sha256").update(key).digest("hex");
      contact.id = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-a${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
      return true;
    });
  }
  report.gaps = [...new Set(report.gaps)].slice(0, 40);
  return report;
}
