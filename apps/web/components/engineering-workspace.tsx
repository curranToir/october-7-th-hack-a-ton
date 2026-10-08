"use client";

import { useState } from "react";
import type { WorkspaceUser } from "../../../coms/types.ts";
import { Logo } from "./logo";

export function EngineeringWorkspace({
  user,
  onSignOut,
  onRefresh,
  warning,
}: {
  user: WorkspaceUser;
  onSignOut: () => Promise<void>;
  onRefresh: () => Promise<void>;
  warning: string;
}) {
  const [busy, setBusy] = useState(false);
  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch {
      // The workspace hook presents the request error below.
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="loading-workspace sign-in-panel engineering-workspace">
      <Logo size={48} alt="TOIR" />
      <span className="department-label">Engineering</span>
      <h1>You’re signed in.</h1>
      <p className="engineering-identity">{user.email}</p>
      <section className="memory-access" aria-labelledby="memory-access-title">
        <h2 id="memory-access-title">Sales memories are unavailable</h2>
        <p>
          Your Engineering account is active. Sales memories and conversations
          are not shared with this department.
        </p>
        <p>
          No sales research history, meeting notes, or CRM records are available
          to this account.
        </p>
      </section>
      {warning && <p role="alert" className="sales-error">{warning}</p>}
      <div className="engineering-actions">
        <button className="button secondary" disabled={busy} onClick={() => void act(onRefresh)}>
          Refresh access
        </button>
        <button className="button primary" disabled={busy} onClick={() => void act(onSignOut)}>
          Sign out
        </button>
      </div>
    </main>
  );
}
