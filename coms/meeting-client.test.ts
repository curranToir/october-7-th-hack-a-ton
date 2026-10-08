import assert from "node:assert/strict";
import test from "node:test";
import { parseMeetingTranscript } from "./meeting-client.ts";

test("transcript import preserves speakers, quoted speech, and elapsed timestamps", () => {
  assert.deepEqual(parseMeetingTranscript(
    "[00:08] Alex: Export is empty: I expected 248 rows.\r\n\n[01:02:03] Curran: We will investigate.",
  ), [
    { id: "import-1", speaker: "Alex", start: 8, text: "Export is empty: I expected 248 rows." },
    { id: "import-2", speaker: "Curran", start: 3723, text: "We will investigate." },
  ]);
});

test("untimed imports do not fabricate elapsed time or speaker identity", () => {
  assert.deepEqual(parseMeetingTranscript("Alex: The issue started today.\nhttps://example.com/issue"), [
    { id: "import-1", speaker: "Alex", start: 0, text: "The issue started today." },
    { id: "import-2", speaker: "Speaker", start: 0, text: "https://example.com/issue" },
  ]);
});

test("invalid timestamps and empty timestamped turns are rejected before submission", () => {
  assert.throws(() => parseMeetingTranscript("[01:65] Alex: Example problem."), /timestamp on line 1/);
  assert.throws(() => parseMeetingTranscript("[00:08] "), /Line 1/);
  assert.throws(() => parseMeetingTranscript("  \n\n "), /at least one line/);
});

test("transcript imports enforce API size limits", () => {
  assert.throws(() => parseMeetingTranscript("x\n".repeat(2001)), /2,000 transcript lines/);
  assert.throws(() => parseMeetingTranscript("x".repeat(120001)), /120,000 characters/);
  assert.throws(() => parseMeetingTranscript("Alex: " + "x".repeat(12001)), /Line 1/);
});
