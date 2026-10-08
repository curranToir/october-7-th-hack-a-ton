import Ajv from "ajv";
import addFormats from "ajv-formats";
import { Type, type Static } from "@sinclair/typebox";
import { ResearchError } from "../research/budget";
import type { Sources } from "../research/sources";

const text = (minLength: number, maxLength: number) =>
  Type.String({ minLength, maxLength });
const citation = Type.Object(
  { source_id: text(1, 100), quote: text(12, 1000) },
  { additionalProperties: false },
);
const subjectSchema = Type.Object(
  {
    kind: Type.Union([Type.Literal("company"), Type.Literal("person")]),
    name: text(2, 200),
    context: text(0, 1000),
    evidence: Type.Object(
      { segment_id: text(1, 128), quote: text(5, 2000) },
      { additionalProperties: false },
    ),
  },
  { additionalProperties: false },
);
const requestSchema = Type.Object(
  {
    version: Type.Literal(1),
    task_id: Type.String({ format: "uuid" }),
    subject: subjectSchema,
  },
  { additionalProperties: false },
);
export const subjectReportSchema = Type.Object(
  {
    identity_status: Type.Union([
      Type.Literal("matched"),
      Type.Literal("ambiguous"),
      Type.Literal("not_found"),
    ]),
    facts: Type.Array(
      Type.Object(
        {
          kind: Type.Union([
            Type.Literal("identity"),
            Type.Literal("product"),
            Type.Literal("role"),
            Type.Literal("business"),
          ]),
          claim: text(10, 1200),
          citations: Type.Array(citation, { minItems: 1, maxItems: 4 }),
        },
        { additionalProperties: false },
      ),
      { maxItems: 12 },
    ),
    gaps: Type.Array(text(1, 1000), { maxItems: 12 }),
  },
  { additionalProperties: false },
);
export type SubjectRequest = Static<typeof requestSchema>;
export type SubjectReport = Static<typeof subjectReportSchema>;
const ajv = new Ajv({ allErrors: true });
addFormats(ajv);
export const validSubjectRequest = ajv.compile<SubjectRequest>(requestSchema);
const validReport = ajv.compile<SubjectReport>(subjectReportSchema);
const normalized = (value: string) =>
  value.replace(/\s+/g, " ").trim().toLowerCase();

export function finalizeSubject(value: unknown, sources: Sources) {
  if (!validReport(value))
    throw new ResearchError(
      "report",
      "Subject research returned an invalid evidence report.",
    );
  const report = structuredClone(value);
  const retrieved = new Map(
    sources.list().map((source) => [source.id, source]),
  );
  const facts = report.facts.filter((fact) =>
    fact.citations.every((evidence) => {
      const source = retrieved.get(evidence.source_id);
      const quote = normalized(evidence.quote);
      return (
        source && quote.length >= 12 && normalized(source.text).includes(quote)
      );
    }),
  );
  if (facts.length !== report.facts.length)
    report.gaps.push(
      "Some claims were excluded because their quotations were not in retrieved evidence.",
    );
  report.facts = facts;
  if (
    report.identity_status === "matched" &&
    !facts.some((fact) => fact.kind === "identity")
  ) {
    report.identity_status = "ambiguous";
    report.gaps.push(
      "No supported identity match was established for the mentioned subject.",
    );
  }
  if (report.identity_status !== "matched") report.facts = [];
  const summary =
    report.identity_status === "matched"
      ? report.facts
          .map((fact) => fact.claim)
          .join(" ")
          .slice(0, 3000)
      : report.identity_status === "ambiguous"
        ? "The mentioned subject could not be identified unambiguously from public evidence."
        : "No reliable public professional profile was found for the mentioned subject.";
  return {
    ...report,
    summary,
    gaps: [...new Set(report.gaps)].slice(0, 12),
    sources: sources
      .list()
      .map(({ id, title, url, retrieved_at }) => ({
        id,
        title,
        url,
        retrieved_at,
      })),
  };
}
export type SubjectResult = ReturnType<typeof finalizeSubject>;
