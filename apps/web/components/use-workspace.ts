"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { workspaceClient } from "../../../coms/storage.ts";
import type { WorkspaceState } from "../../../coms/types.ts";

export function useWorkspace() {
  const [state, setState] = useState<WorkspaceState | null>(null);
  const current = useRef<WorkspaceState | null>(null);
  const [warning, setWarning] = useState("");
  useEffect(() => {
    const result = workspaceClient.read();
    current.current = result.state;
    setState(result.state);
    if (result.warning) setWarning(result.warning);
  }, []);
  const update = useCallback(
    (change: (state: WorkspaceState) => WorkspaceState) => {
      if (!current.current) return false;
      const next = change(current.current);
      if (next === current.current) return true;
      current.current = next;
      setState(next);
      const saved = workspaceClient.save(next);
      setWarning(
        saved
          ? ""
          : "Changes are available for this visit, but could not be saved to this browser.",
      );
      return saved;
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
  }, [state?.preferences.theme, !!state]); // Apply the persisted theme before the workspace paints.
  return { state, update, warning, clearWarning: () => setWarning("") };
}
export type UpdateWorkspace = ReturnType<typeof useWorkspace>["update"];
