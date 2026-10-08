"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuthSession } from "../../../coms/types.ts";
import { salesClient } from "../../../coms/sales-client.ts";

function browserLabel(agent: string) {
  const browser = /Edg\//.test(agent)
    ? "Edge"
    : /Firefox\//.test(agent)
      ? "Firefox"
      : /Chrome\//.test(agent)
        ? "Chrome"
        : /Safari\//.test(agent)
          ? "Safari"
          : "Browser";
  const system = /iPhone|iPad/.test(agent)
    ? "iOS"
    : /Android/.test(agent)
      ? "Android"
      : /Windows/.test(agent)
        ? "Windows"
        : /Macintosh/.test(agent)
          ? "macOS"
          : /Linux/.test(agent)
            ? "Linux"
            : "";
  return system ? `${browser} on ${system}` : browser;
}

export function AccountSessions({
  onChanged,
}: {
  onChanged: () => Promise<unknown>;
}) {
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      setSessions(await salesClient.authSessions());
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Sessions could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const revoke = async (session?: AuthSession) => {
    setBusy(true);
    setError("");
    try {
      if (session) await salesClient.revokeSession(session.id);
      else await salesClient.revokeAllSessions();
      if (!session || session.current) await onChanged();
      else await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign out failed.");
    } finally {
      setBusy(false);
    }
  };
  const date = (value: number) => new Date(value * 1000).toLocaleString();
  return (
    <section className="settings-section" aria-label="Active sign-ins">
      <h3>Active sign-ins</h3>
      <p className="field-hint">
        Sign-ins expire after eight hours. Signing out keeps your conversations
        and approved work.
      </p>
      {loading && <p role="status">Loading sign-ins…</p>}
      {!loading &&
        sessions.map((session) => (
          <div className="setting-row" key={session.id}>
            <div className="setting-row-label">
              <span>
                {session.current ? "This browser" : "Another browser"}
              </span>
              <p>
                Signed in {date(session.created_at)} · Last active{" "}
                {date(session.last_seen_at)}
              </p>
              <p>Expires {date(session.expires_at)}</p>
              <p>{browserLabel(session.user_agent)}</p>
            </div>
            <div className="setting-control">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => void revoke(session)}
              >
                {session.current ? "Sign out this browser" : "Revoke sign-in"}
              </button>
            </div>
          </div>
        ))}
      <button
        className="button secondary"
        disabled={busy || loading || !sessions.length}
        onClick={() => void revoke()}
      >
        Sign out all browsers
      </button>
      {error && (
        <p className="sales-error" role="alert">
          {error}
        </p>
      )}
      {!loading && error && (
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => void load()}
        >
          Retry
        </button>
      )}
    </section>
  );
}
