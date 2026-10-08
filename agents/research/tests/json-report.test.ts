import { expect, test } from "bun:test";
import { parseReportText } from "../src/harness/json-report";

test("final report accepts a single JSON object or one complete outer JSON fence", () => {
  expect(parseReportText(' {"leads":[]} ', "stop")).toEqual({leads: []});
  expect(parseReportText('```json\n{"leads":[]}\n```', "stop")).toEqual({leads: []});
  expect(parseReportText('```JSON\r\n{"leads":[]}\r\n```', "stop")).toEqual({leads: []});
});

test("final report rejects truncated, prose-wrapped, ambiguous, and non-final answers", () => {
  for (const text of ['{"leads":[', 'Here is the report: {"leads":[]}', '{"leads":[]} {"leads":[]}', '```json\n{"leads":[]}\n``` trailing']) {
    expect(() => parseReportText(text, "stop")).toThrow("invalid report");
  }
  for (const reason of ["length", "toolUse", "error", "aborted", "private-vendor-error"]) {
    expect(() => parseReportText('{"leads":[]}', reason)).toThrow("invalid report");
  }
});

test("parse diagnostics contain only fixed shape/completion labels", () => {
  let message = "";
  try { parseReportText('private-key-value and private-source-text', 'private-vendor-error'); }
  catch (error) { message = (error as Error).message; }
  expect(message).toContain("format=other, completion=unknown");
  expect(message).not.toContain("private-");
  expect(() => parseReportText('{"leads":[]}', "length")).toThrow("completion=length");
});
