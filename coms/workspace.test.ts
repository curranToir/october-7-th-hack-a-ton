import { test } from "node:test";
import assert from "node:assert/strict";
import { createDemoWorkspace } from "./seed.ts";
import { decideTask, newSession, sendMessage } from "./workspace.ts";
import { readWorkspace, saveWorkspace, STORAGE_KEY } from "./storage.ts";
import type { StoragePort } from "./types.ts";

function memoryStorage(): StoragePort {
  const data = new Map<string, string>();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

test("an approval is recorded once and cannot be overwritten by a second decision", () => {
  const original = createDemoWorkspace();
  const approved = decideTask(original, "task-maya", "approved");
  assert.equal(original.tasks[0].status, "pending");
  assert.equal(approved.tasks[0].status, "approved");
  assert.ok(approved.tasks[0].decidedAt);
  assert.equal(
    approved.tasks.filter((task) => task.status === "pending").length,
    3,
  );
  assert.equal(decideTask(approved, "task-maya", "denied"), approved);
  assert.equal(decideTask(approved, "missing", "approved"), approved);
});

test("preferences, edited profile, conversations and decisions survive a reload", () => {
  const storage = memoryStorage();
  const state = decideTask(createDemoWorkspace(), "task-eli", "denied");
  state.preferences.theme = "dark";
  state.profile.name = "Demo Reviewer";
  const withMessage = sendMessage(state, "research", "New research request");
  assert.equal(saveWorkspace(storage, withMessage), true);
  assert.deepEqual(readWorkspace(storage), { state: withMessage });
});

test("turning off saved history removes old persisted conversations while keeping decisions", () => {
  const storage = memoryStorage();
  const state = decideTask(createDemoWorkspace(), "task-maya", "approved");
  saveWorkspace(storage, state);
  state.preferences.saveHistory = false;
  saveWorkspace(storage, state);
  const loaded = readWorkspace(storage).state;
  assert.deepEqual(loaded.sessions, []);
  assert.equal(loaded.tasks[0].status, "approved");
  assert.ok(
    state.sessions.length > 0,
    "current visit keeps its in-memory conversations",
  );
});

test("corrupt, incompatible and incomplete stored payloads recover to a usable demo", () => {
  const storage = memoryStorage();
  const badStates = [
    "{broken",
    JSON.stringify({ version: 9 }),
    JSON.stringify({ ...createDemoWorkspace(), sessions: [{ id: "bad" }] }),
    JSON.stringify({ ...createDemoWorkspace(), tasks: [{ id: "bad" }] }),
  ];
  for (const value of badStates) {
    storage.setItem(STORAGE_KEY, value);
    const result = readWorkspace(storage);
    assert.ok(result.warning);
    assert.equal(result.state.tasks.length, 4);
    assert.equal(result.state.version, 1);
  }
});

test("an invalid time zone cannot crash decision history after restoring storage", () => {
  const storage = memoryStorage();
  const state = createDemoWorkspace();
  state.preferences.timeZone = "invalid/timezone";
  storage.setItem(STORAGE_KEY, JSON.stringify(state));
  assert.ok(readWorkspace(storage).warning);
});

test("storage denial and quota exhaustion are surfaced without losing in-memory work", () => {
  const denied: StoragePort = {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("quota");
    },
  };
  assert.ok(readWorkspace(denied).warning);
  assert.equal(saveWorkspace(denied, createDemoWorkspace()), false);
});

test("new chat titles use the first message and empty sends do nothing", () => {
  const state = createDemoWorkspace();
  const session = newSession("Research agent");
  state.sessions.unshift(session);
  assert.equal(sendMessage(state, session.id, "   "), state);
  const sent = sendMessage(state, session.id, "  Research a new account  ");
  assert.equal(sent.sessions[0].title, "Research a new account");
  assert.equal(sent.sessions[0].agent, "Research agent");
  assert.equal(sent.sessions[0].messages.length, 2);
  assert.match(sent.sessions[0].messages[1].content, /demo conversation/);
  assert.equal(state.sessions[0].messages.length, 0);
  assert.equal(
    sendMessage(sent, session.id, "Another message").sessions[0].title,
    "Research a new account",
  );
});
