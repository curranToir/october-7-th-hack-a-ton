"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { meetingClient } from "../../../coms/meeting-client.ts";
import type { MeetingCreate, MeetingImport, MeetingWorkspace } from "../../../coms/meeting-types.ts";

export function useMeetings(enabled: boolean) {
  const [state, setState] = useState<MeetingWorkspace | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const alive = useRef(false);
  const mutation = useRef(false);
  const keys = useRef(new Map<string, string>());
  const refresh = useCallback(async (force = false) => {
    if (!alive.current || (mutation.current && !force) || (request.current && !force)) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const current = ++generation.current;
    try {
      const next = await meetingClient.workspace(controller.signal);
      if (!alive.current || current !== generation.current) return;
      setState(next);
      setError("");
    } catch (cause) {
      if (!alive.current || current !== generation.current || controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "Unable to load meetings.");
    } finally {
      if (alive.current && current === generation.current) setLoading(false);
      if (request.current === controller) request.current = null;
    }
  }, []);
  useEffect(() => {
    if (!enabled) {
      setState(null);
      setError("");
      setLoading(true);
      return;
    }
    alive.current = true;
    void refresh();
    const onFocus = () => void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    window.addEventListener("focus", onFocus);
    return () => {
      alive.current = false;
      ++generation.current;
      request.current?.abort();
      request.current = null;
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, refresh]);
  const perform = async <T,>(operation: () => Promise<T>): Promise<T> => {
    if (mutation.current) throw new Error("Another meeting update is still being saved.");
    mutation.current = true;
    request.current?.abort();
    request.current = null;
    ++generation.current;
    setBusy(true);
    try {
      return await operation();
    } finally {
      await refresh(true);
      // Prevent a double click from approving another card as this one leaves the queue.
      await new Promise(resolve => setTimeout(resolve, 400));
      mutation.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const keyed = <T,>(action: string, operation: (key: string) => Promise<T>) => {
    if (!keys.current.has(action)) keys.current.set(action, crypto.randomUUID());
    return perform(() => operation(keys.current.get(action)!)).then(value => {
      keys.current.delete(action);
      return value;
    });
  };
  return {
    state, error, loading, busy, refresh,
    create: (value: MeetingCreate) => keyed(`create:${JSON.stringify(value)}`, key => meetingClient.create(value, key)),
    import: (value: MeetingImport) => keyed(`import:${JSON.stringify(value)}`, key => meetingClient.import(value, key)),
    demo: () => keyed("demo", meetingClient.demo),
    retryMeeting: (id: string) => perform(() => meetingClient.retryMeeting(id)),
    edit: (id: string, version: number, title: string, body: string) =>
      perform(() => meetingClient.edit(id, version, title, body)),
    decide: (id: string, version: number, decision: "approve" | "reject") =>
      perform(() => meetingClient.decide(id, version, decision)),
    retryTask: (id: string) => perform(() => meetingClient.retryTask(id)),
    reconcile: (id: string) => perform(() => meetingClient.reconcile(id)),
  };
}
export type MeetingActions = ReturnType<typeof useMeetings>;
