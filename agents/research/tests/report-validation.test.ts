import { expect, test } from "bun:test";
import { EMPTY_REPORT, validateReport, type Lead, type Report } from "../src/research/contracts";
import { ResearchError } from "../src/research/budget";
import { Sources } from "../src/research/sources";

function fixture() {
  const sources = new Sources();
  const identity = "Example is a US company with 100 employees.";
  const need = "Example needs to automate manual warehouse scheduling.";
  const offer = "OtherCo offers implementation of AI integrations for warehouse operations.";
  const source = sources.add({
    url: "https://example.com/news", text: `${identity} ${need} ${offer}`,
  })!;
  const citation = (quote: string) => ({ source_id: source.id, quote });
  const lead: Lead = {
    company: "Example", domain: "example.com", country: "US", employee_count: 100,
    identity_citations: [citation(identity)], decision_maker: null,
    signals: [{ kind: "business_need", claim: need, event_date: null, citations: [citation(need)] }],
    ai_use_case: "Automate warehouse scheduling with AI.",
    rationale: "A documented scheduling need could support an integration pilot.",
    outreach_angle: "Offer a scoped scheduling pilot with measurable outcomes.",
    fit_score: 85,
  };
  const competitor: Report["competitors"][number] = {
    company: "OtherCo", kind: "marketing", claim: offer,
    citations: [citation(offer)], positioning_hypothesis: "Consider a narrower integration scope.",
  };
  return { sources, source, lead, competitor };
}

test("invalid candidates cannot discard valid companies or competitor evidence", () => {
  const { sources, source, lead, competitor } = fixture();
  const mixed = {
    ...EMPTY_REPORT,
    leads: [lead, { ...lead, domain: null }, { ...lead, fit_score: "85" }],
    competitors: [competitor, { ...competitor, kind: null }],
  };
  const original = structuredClone(mixed);
  expect(validateReport(mixed)).toBe(false); // Shared input contract is still strict.
  const result = sources.finalize(mixed);
  expect(result.leads).toEqual([lead]);
  expect(result.competitors).toEqual([competitor]);
  expect(result.sources).toEqual([source]);
  expect(result.gaps).toEqual([
    "Excluded 2 company candidates: invalid report fields.",
    "Excluded 1 competitor candidate: invalid report fields.",
  ]);
  expect(validateReport(result)).toBe(true);
  expect(mixed).toEqual(original); // Filtering must not coerce or mutate model values.
});

test("schema-valid candidates still need retrieved citations after malformed peers are removed", () => {
  const { sources, source, lead, competitor } = fixture();
  const inventedQuote = "This funding announcement was never retrieved from the public web.";
  const ungrounded = {
    ...lead,
    company: "Unsupported company", domain: "unsupported.example",
    signals: [{
      ...lead.signals[0], claim: inventedQuote,
      citations: [{ source_id: source.id, quote: inventedQuote }],
    }],
  };
  const absentSource = {
    ...competitor,
    citations: [{ source_id: "src_ffffffffffffffff", quote: competitor.claim }],
  };
  const result = sources.finalize({
    ...EMPTY_REPORT,
    leads: [{ ...lead, domain: null }, lead, ungrounded],
    competitors: [absentSource],
    sources: [{ ...source, text: `${source.text} ${inventedQuote}` }],
  });
  expect(result.leads).toEqual([lead]);
  expect(result.competitors).toEqual([]);
  expect(result.sources).toEqual([source]);
  expect(result.gaps).toContain(
    "Excluded Unsupported company: a quotation was not present in retrieved evidence.",
  );
  expect(result.gaps).toContain(
    "Excluded OtherCo: competitor evidence did not support the quotation.",
  );
});

test("all malformed candidates return zero findings and sanitized gaps with original sources", () => {
  const { sources, source, lead, competitor } = fixture();
  const result = sources.finalize({
    ...EMPTY_REPORT,
    leads: [
      { ...lead, company: "private-model-company", domain: null },
      { ...lead, signals: [] },
      null,
    ],
    competitors: [{ ...competitor, claim: "private-model-value", citations: [] }, false],
    gaps: Array.from({ length: 40 }, (_, i) => `Unverified model gap ${i}`),
  });
  expect(result.leads).toEqual([]);
  expect(result.competitors).toEqual([]);
  expect(result.sources).toEqual([source]);
  expect(result.gaps.slice(0, 2)).toEqual([
    "Excluded 3 company candidates: invalid report fields.",
    "Excluded 2 competitor candidates: invalid report fields.",
  ]);
  expect(result.gaps).toHaveLength(40);
  expect(JSON.stringify(result)).not.toContain("private-model-");
  expect(validateReport(result)).toBe(true);
});

test.each([
  ["array root", []],
  ["null leads", { leads: null }],
  ["object leads", { leads: {} }],
  ["string competitors", { competitors: "not-an-array" }],
  ["too many leads", { leads: Array(31).fill(null) }],
  ["too many competitors", { competitors: Array(21).fill(null) }],
  ["null gaps", { gaps: null }],
  ["invalid gap", { gaps: [42] }],
  ["too many gaps", { gaps: Array(41).fill("Coverage unavailable") }],
  ["unknown field", { unexpected: "private-value" }],
])("malformed report envelope remains fatal: %s", (_label, value) => {
  const sources = new Sources();
  expect(() => sources.finalize(value)).toThrow(ResearchError);
});

test("only schema-nullable fields may retain unknown values", () => {
  const { sources, lead } = fixture();
  const nullable = { ...lead, employee_count: null, decision_maker: null };
  const result = sources.finalize({
    leads: [nullable, { ...lead, country: null }, { ...lead, ai_use_case: null }],
  });
  expect(result.leads).toEqual([nullable]);
  expect(result.leads[0].signals[0].event_date).toBeNull();
  expect(result.gaps).toEqual(["Excluded 2 company candidates: invalid report fields."]);
});

test.each([
  "unknown", "example..com", "-example.com", "example_com", "169.254.169.254",
  "https://example.com/news", "example.com/path", " ",
])("unverified or malformed domain syntax is excluded without repair: %s", (domain) => {
  const { sources, lead } = fixture();
  const result = sources.finalize({ leads: [lead, { ...lead, domain }] });
  expect(result.leads).toEqual([lead]);
  expect(result.gaps).toEqual(["Excluded 1 company candidate: invalid report fields."]);
});
