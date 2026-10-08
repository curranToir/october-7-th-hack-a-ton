import { ResearchError } from "../research/budget";

/** Accept one complete JSON value, optionally in one outer JSON fence. */
export function parseReportText(body: string, stopReason: string, label: "Research" | "Contact research" | "Subject research" = "Research"): unknown {
  const trimmed = body.trim();
  const fence = /^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```$/i.exec(trimmed);
  const text = fence ? fence[1].trim() : trimmed;
  const format = !trimmed ? "empty" : fence ? "json_fence" : /^[{[]/.test(trimmed) ? "json" : "other";
  const completion = ["stop", "length", "toolUse", "error", "aborted"].includes(stopReason) ? stopReason : "unknown";
  const fail = () => new ResearchError("report", `${label} returned an invalid report (format=${format}, completion=${completion}). Retrieved evidence was saved; retry explicitly.`);
  // Even syntactically closed JSON at the token limit may omit required findings.
  if (stopReason !== "stop") throw fail();
  try {
    return JSON.parse(text);
  } catch {
    // Never include model/source content, parser exception messages, or raw vendors.
    throw fail();
  }
}
