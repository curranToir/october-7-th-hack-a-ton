"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  defaultPreferences,
  presentWorkspace,
  readPreferences,
  SalesApiError,
  salesClient,
} from "../../../coms/sales-client.ts";
import type {
  AutomationUpdate,
  Decision,
  LiveWorkspace,
  Preferences,
  SalesSnapshot,
} from "../../../coms/types.ts";

const PREFERENCES_KEY = "toir-sales-preferences-v1";
export function useWorkspace() {
  const [state, setState] = useState<LiveWorkspace | null>(null);
  const [warning, setWarning] = useState("");
  const [loadWarning, setLoadWarning] = useState("");
  const [unauthorized, setUnauthorized] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deciding, setDeciding] = useState(false);
  const decisionLock = useRef(false);
  const decisionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefs = useRef<Preferences>({ ...defaultPreferences });
  const snapshot = useRef<SalesSnapshot | null>(null);
  const generation = useRef(0);
  const polling = useRef(0);
  const alive = useRef(true);
  const keys = useRef(new Map<string, string>());
  const keyFor = (action: string) => {
    if (!keys.current.has(action))
      keys.current.set(action, crypto.randomUUID());
    return keys.current.get(action)!;
  };
  const refresh = useCallback(async (force = false) => {
    if (polling.current && !force) return;
    const current = ++generation.current;
    polling.current = current;
    try {
      const data = await salesClient.workspace();
      if (!alive.current || current !== generation.current) return;
      snapshot.current = data;
      setState(presentWorkspace(data, prefs.current));
      setUnauthorized(false);
      setLoadWarning("");
      setLoading(false);
    } catch (error) {
      if (!alive.current || current !== generation.current) return;
      if (error instanceof SalesApiError && error.status === 401) {
        setUnauthorized(true);
        setState(null);
        snapshot.current = null;
      } else
        setLoadWarning(
          error instanceof Error
            ? error.message
            : "Unable to load the sales workspace.",
        );
      setLoading(false);
    } finally {
      if (polling.current === current) polling.current = 0;
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    try {
      prefs.current = readPreferences(localStorage.getItem(PREFERENCES_KEY));
    } catch {
      /* Local preferences are optional. */
    }
    void refresh();
    const onFocus = () => {
      void refresh();
    };
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
    window.addEventListener("focus", onFocus);
    return () => {
      alive.current = false;
      ++generation.current;
      polling.current = 0;
      if (decisionTimer.current) clearTimeout(decisionTimer.current);
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);
  const perform = useCallback(
    async <T>(operation: () => Promise<T>): Promise<T> => {
      // An older poll must not overwrite the state returned after a mutation.
      ++generation.current;
      try {
        const result = await operation();
        setWarning("");
        await refresh(true);
        return result;
      } catch (error) {
        if (error instanceof SalesApiError && error.status === 401) {
          setUnauthorized(true);
          setState(null);
        }
        setWarning(
          error instanceof Error ? error.message : "The request failed.",
        );
        await refresh(true);
        throw error;
      }
    },
    [refresh],
  );
  const updatePreferences = useCallback(
    (
      values: Partial<
        Pick<Preferences, "theme" | "compactSidebar" | "autoAdvance">
      >,
    ) => {
      prefs.current = readPreferences(
        JSON.stringify({ ...prefs.current, ...values }),
      );
      if (snapshot.current)
        setState(presentWorkspace(snapshot.current, prefs.current));
      try {
        localStorage.setItem(PREFERENCES_KEY, JSON.stringify(prefs.current));
      } catch {
        setWarning(
          "Your display preferences are available for this visit but could not be saved on this device.",
        );
      }
    },
    [],
  );
  useLayoutEffect(() => {
    if (!state) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme =
        state.preferences.theme === "system"
          ? query.matches
            ? "dark"
            : "light"
          : state.preferences.theme;
    };
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [state?.preferences.theme, !!state]);
  return {
    state,
    warning: warning || loadWarning,
    unauthorized,
    loading,
    deciding,
    refresh,
    updatePreferences,
    clearWarning: () => {
      setWarning("");
      setLoadWarning("");
    },
    createSession: () => perform(salesClient.createSession),
    renameSession: (id: string, title: string) =>
      perform(() => salesClient.renameSession(id, title)),
    deleteSession: (id: string) => perform(() => salesClient.deleteSession(id)),
    send: (id: string, content: string) => {
      const action = `send:${id}:${content}`;
      return perform(() => salesClient.send(id, content, keyFor(action))).then(
        (value) => {
          keys.current.delete(action);
          return value;
        },
      );
    },
    decide: async (id: string, version: number, decision: Decision) => {
      if (decisionLock.current)
        throw new Error("A decision is already being saved.");
      decisionLock.current = true;
      setDeciding(true);
      try {
        return await perform(() =>
          salesClient.decide(
            id,
            version,
            decision,
            keyFor(`decision:${id}:${version}:${decision}`),
          ),
        );
      } finally {
        // A double-click must not approve the next card when the queue advances.
        decisionTimer.current = setTimeout(() => {
          decisionLock.current = false;
          if (alive.current) setDeciding(false);
        }, 500);
      }
    },
    editProposal: (
      id: string,
      version: number,
      contacts: string[],
      operations: string[],
      fields: Record<string, string[]>,
    ) =>
      perform(() =>
        salesClient.editProposal(id, version, contacts, operations, fields),
      ),
    retry: (id: string) => perform(() => salesClient.retry(id)),
    saveAutomation: (update: Partial<AutomationUpdate>) =>
      perform(() => salesClient.automation(update)),
    cancelJob: (id: string) => perform(() => salesClient.cancelJob(id)),
    retryJob: (id: string) => perform(() => salesClient.retryJob(id)),
    logout: () => perform(salesClient.logout),
  };
}
export type WorkspaceActions = ReturnType<typeof useWorkspace>;
