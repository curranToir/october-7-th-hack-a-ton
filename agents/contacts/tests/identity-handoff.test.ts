import { expect, test } from "bun:test";
import { Sources } from "../../research/src/research/sources";
import { EMPTY_REPORT, type Company, type ContactTask } from "../src/research/contracts";
import { finalizeReport } from "../src/research/evidence";

function fixture(selectedName: string, officialName: string, officialDomain = "jazwares.com") {
  const sources = new Sources();
  const discovery = sources.add({
    url: "https://news.example.net/announcement",
    text: `${selectedName} announced an expanded technology program.`,
  })!;
  const official = sources.add({
    url: `https://${officialDomain}/team`,
    text: `${officialName} develops toys. Jane Smith is Chief Technology Officer at ${officialName}.`,
  })!;
  const selected: Company = {
    name: selectedName, domain: "jazwares.com", description: "Selected discovery profile",
    country: "US", employee_count: 750, fit_score: 87, sales_angle: "Explore integration needs.",
    citations: [{ source_id: discovery.id, quote: discovery.text }],
  };
  const company: Company = {
    ...selected, name: officialName, domain: officialDomain, fit_score: 1,
    description: "Unreviewed replacement profile", sales_angle: "Unreviewed replacement angle",
    citations: [{ source_id: official.id, quote: official.text }],
  };
  const task: ContactTask = {
    version: 1, task_id: "task-identity", run_id: "run-identity", mode: "enrich",
    query: "Find company decision makers", company: selected,
    deadline_at: new Date(Date.now() + 600_000).toISOString(), prior_sources: [discovery], usage: {},
  };
  const report = {
    ...structuredClone(EMPTY_REPORT), company,
    contacts: [{
      name: "Jane Smith", title: "Chief Technology Officer", company_domain: officialDomain,
      buying_relevance: "Potential buyer for AI integration work",
      citations: [{ source_id: official.id, quote: official.text }],
    }],
  };
  return { sources, task, report, selected, company, official };
}

test.each([
  ["Jazwares, LLC", "Jazwares"],
  ["Jazwares", "Jazwares, LLC"],
  ["Jazwares Inc.", "Jazwares"],
  ["Jazwares", "Jazwares"],
])("fresh official name bridges same-domain legal suffix only: %s -> %s", (before, after) => {
  const { sources, task, report, selected, company } = fixture(before, after);
  const result = finalizeReport(report, task, sources);
  expect(result.company).toEqual({ ...selected, name: after, citations: company.citations });
  expect(result.company?.domain).toBe(selected.domain);
  expect(result.company?.fit_score).toBe(87);
  expect(result.company?.sales_angle).toBe(selected.sales_angle);
  expect(result.company?.description).toBe(selected.description);
  expect(result.contacts).toHaveLength(1);
  expect(task.company?.name).toBe(before);
  expect(result.company?.citations[0].quote).toContain(after);
});

test.each([
  ["Jazwares, LLC", "Different Brand", "jazwares.com"],
  ["Jazwares, LLC", "Jazwares", "different.example"],
  ["JazwaresLLC", "Jazwares", "jazwares.com"],
  ["Jazwares LLC Toys", "Jazwares", "jazwares.com"],
  ["Jazwares, LLC", "Jazwares Licensing", "jazwares.com"],
])("identity bridge rejects different brands/domains and embedded suffixes: %s / %s / %s", (
  before, after, host,
) => {
  const { sources, task, report } = fixture(before, after, host);
  expect(() => finalizeReport(report, task, sources)).toThrow("verified identity evidence");
});

test("a same-domain suffix variant still requires the fresh name literally in retrieved evidence", () => {
  const { sources, task, report } = fixture("Jazwares", "Jazwares");
  report.company.name = "Jazwares, LLC";
  expect(() => finalizeReport(report, task, sources)).toThrow("verified identity evidence");
});

test("model-authored identity quotations cannot authorize the suffix bridge", () => {
  const { sources, task, report } = fixture("Jazwares, LLC", "Jazwares");
  report.company.citations[0].quote = "Jazwares is a verified company at jazwares.com.";
  expect(() => finalizeReport(report, task, sources)).toThrow("verified identity evidence");
});
