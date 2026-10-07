"use client";

import { useState } from "react";
import type {
  SalesCitation,
  SalesContact,
  SalesProposal,
  SalesSource,
} from "../../../coms/types.ts";
import { evidenceUrl } from "../../../coms/sales-client.ts";
import type { WorkspaceActions } from "./use-workspace";
import { Icon } from "./icon";

export type ProposalActions = Pick<
  WorkspaceActions,
  "decide" | "deciding" | "editProposal" | "retry"
>;
const label = (value: string) => value.replaceAll("_", " ");
function difference(a: string[], b: string[]) {
  return a.length !== b.length || a.some((id) => !b.includes(id));
}

export function Citations({
  citations,
  sources,
  title = "Evidence",
}: {
  citations: SalesCitation[];
  sources: SalesSource[];
  title?: string;
}) {
  if (!citations.length) return null;
  return (
    <details className="sales-evidence">
      <summary>
        {title} · {citations.length}{" "}
        {citations.length === 1 ? "citation" : "citations"}
      </summary>
      {citations.map((citation, index) => {
        const source = sources.find((s) => s.id === citation.source_id);
        const url = evidenceUrl(source?.url);
        return (
          <div key={`${citation.source_id}-${index}`} className="citation">
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                {source?.title || url}
                <Icon name="link" size={13} />
              </a>
            ) : (
              <strong>Source {citation.source_id}</strong>
            )}
            <blockquote>{citation.quote}</blockquote>
            {source && (
              <small>
                Retrieved {new Date(source.retrieved_at).toLocaleString()}
              </small>
            )}
          </div>
        );
      })}
    </details>
  );
}
export function ContactDetails({
  contact,
  sources,
}: {
  contact: SalesContact;
  sources: SalesSource[];
}) {
  const linkedin = evidenceUrl(contact.linkedin_url);
  return (
    <div className="contact-copy">
      <strong>{contact.name}</strong>
      <p>{contact.title}</p>
      <p>{contact.buying_relevance}</p>
      <dl className="contact-info">
        <div>
          <dt>LinkedIn</dt>
          <dd>
            {linkedin ? (
              <a href={linkedin} target="_blank" rel="noreferrer">
                View verified profile <Icon name="link" size={13} />
              </a>
            ) : (
              "Not found"
            )}
          </dd>
        </div>
        <div>
          <dt>Business email</dt>
          <dd>{contact.email || "Not found"}</dd>
        </div>
        <div>
          <dt>Business phone</dt>
          <dd>{contact.phone || "Not found"}</dd>
        </div>
      </dl>
      <Citations
        citations={contact.citations}
        sources={sources}
        title="Role and company evidence"
      />
      <Citations
        citations={contact.linkedin_citations}
        sources={sources}
        title="LinkedIn evidence"
      />
      <Citations
        citations={contact.email_citations}
        sources={sources}
        title="Email evidence"
      />
      <Citations
        citations={contact.phone_citations}
        sources={sources}
        title="Phone evidence"
      />
    </div>
  );
}

export function ProposalCard({
  proposal: task,
  actions,
  onSession,
  sessionAvailable = true,
  onDecided,
}: {
  proposal: SalesProposal;
  actions: ProposalActions;
  onSession?: (id: string) => void;
  sessionAvailable?: boolean;
  onDecided?: () => void;
}) {
  const [contacts, setContacts] = useState(task.excluded_contact_ids);
  const [operations, setOperations] = useState(task.excluded_operation_ids);
  const [fields, setFields] = useState<Record<string, string[]>>(
    task.excluded_fields ?? {},
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = task.status === "pending";
  const locked = !!busy || actions.deciding;
  const dirty =
    difference(contacts, task.excluded_contact_ids) ||
    difference(operations, task.excluded_operation_ids) ||
    JSON.stringify(fields) !== JSON.stringify(task.excluded_fields ?? {});
  const excluded = new Set(operations);
  task.operations.forEach((op) => {
    if (op.contact_id && contacts.includes(op.contact_id)) excluded.add(op.id);
  });
  // Mirror dependency exclusions in the preview; the server validates them again.
  for (let i = 0; i < task.operations.length; i++)
    task.operations.forEach((op) => {
      if (op.depends_on.some((id) => excluded.has(id))) excluded.add(op.id);
    });
  const included = task.operations.filter((op) => !excluded.has(op.id));
  const toggle = (values: string[], id: string) =>
    values.includes(id) ? values.filter((v) => v !== id) : [...values, id];
  const run = async (
    name: string,
    action: () => Promise<unknown>,
    message: string,
  ) => {
    if (locked) return;
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
      if (name === "approved" || name === "denied") onDecided?.();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Unable to save. Please try again.",
      );
    } finally {
      setBusy("");
    }
  };
  return (
    <article
      className="approval-card sales-proposal"
      aria-label={`CRM proposal for ${task.company.name}`}
    >
      <div className="approval-meta">
        <span className="agent-avatar">
          <Icon name="brain" size={23} />
        </span>
        <div>
          <strong>Contact research</strong>
          <span>Proposal version {task.version}</span>
        </div>
        <span className={`approval-badge status-${task.status}`}>
          {pending ? "Needs approval" : label(task.status)}
        </span>
      </div>
      <h2>
        {pending ? "Review CRM changes for" : "CRM proposal for"}{" "}
        {task.company.name}
      </h2>
      <div className="finding">
        <h3>What the agent found</h3>
        <strong>{task.company.name}</strong>
        <p className="finding-subtitle">
          {task.company.domain} · Fit {task.company.fit_score}/100
          {task.company.employee_count
            ? ` · ${task.company.employee_count.toLocaleString()} employees`
            : ""}
        </p>
        <p>{task.company.description}</p>
        <Citations
          citations={task.company.citations}
          sources={task.sources}
          title="Company evidence"
        />
        {task.company.sales_angle && (
          <div className="sales-angle">
            <h4>Suggested sales angle</h4>
            <p>{task.company.sales_angle}</p>
          </div>
        )}
        {onSession && (
          <div className="evidence-links">
            <button
              disabled={!sessionAvailable}
              title={
                sessionAvailable
                  ? "Open conversation"
                  : "This session was deleted; the task audit is retained"
              }
              onClick={() => onSession(task.session_id)}
            >
              View agent session <Icon name="arrow" size={15} />
            </button>
          </div>
        )}
      </div>
      <section className="proposal-section">
        <h3>Decision-makers · {task.contacts.length}</h3>
        {pending && task.contacts.length > 0 && (
          <p className="field-hint">
            Uncheck a contact to omit them from this CRM update.
          </p>
        )}
        {task.contacts.length ? (
          task.contacts.map((contact) => (
            <div
              className={`sales-contact ${contacts.includes(contact.id) ? "excluded" : ""}`}
              key={contact.id}
            >
              {pending && (
                <input
                  type="checkbox"
                  aria-label={`Include ${contact.name}`}
                  checked={!contacts.includes(contact.id)}
                  disabled={
                    locked || task.excluded_contact_ids.includes(contact.id)
                  }
                  onChange={() => setContacts(toggle(contacts, contact.id))}
                />
              )}
              <ContactDetails contact={contact} sources={task.sources} />
            </div>
          ))
        ) : (
          <p>
            No decision-makers were verified. Review the company research and
            gaps below.
          </p>
        )}
      </section>
      <section className="proposal-section">
        <h3>Exact proposed changes · {included.length}</h3>
        <p className="field-hint">
          Review each operation and its fields. Saved exclusions only remove
          work. Required identity and association fields stay selected.
        </p>
        {task.operations.map((operation) => {
          const contact = task.contacts.find(
            (c) => c.id === operation.contact_id,
          );
          const dependencyExcluded = operation.depends_on.some((id) =>
            excluded.has(id),
          );
          const contactExcluded =
            !!operation.contact_id && contacts.includes(operation.contact_id);
          return (
            <details
              className={`crm-operation ${excluded.has(operation.id) ? "excluded" : ""}`}
              key={operation.id}
              open={operation.kind !== "note"}
            >
              <summary>
                <span>
                  {label(operation.action)} {operation.kind}
                  {contact ? ` · ${contact.name}` : ""}
                </span>
                <small>
                  {excluded.has(operation.id)
                    ? "Excluded"
                    : pending
                      ? "Proposed"
                      : label(operation.status)}
                </small>
              </summary>
              {pending && (
                <label className="include-operation">
                  <input
                    type="checkbox"
                    checked={!excluded.has(operation.id)}
                    disabled={
                      locked ||
                      dependencyExcluded ||
                      contactExcluded ||
                      task.excluded_operation_ids.includes(operation.id) ||
                      operation.status === "succeeded"
                    }
                    onChange={() =>
                      setOperations(toggle(operations, operation.id))
                    }
                  />
                  Include this change
                  {dependencyExcluded
                    ? " (requires another excluded change)"
                    : ""}
                </label>
              )}
              {(operation.record_id || operation.result_id) && (
                <p className="field-hint">
                  HubSpot record: {operation.result_id || operation.record_id}
                </p>
              )}
              {Object.keys(operation.properties).length > 0 ? (
                <div className="diff-scroll">
                  <table className="field-diff">
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>Current value</th>
                        <th>Proposed value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(operation.properties).map(
                        ([field, value]) => (
                          <tr
                            key={field}
                            className={
                              fields[operation.id]?.includes(field)
                                ? "excluded"
                                : ""
                            }
                          >
                            <th>
                              {pending ? (
                                <label className="field-selection">
                                  <input
                                    type="checkbox"
                                    aria-label={`Include ${field} in ${operation.kind}${contact ? ` for ${contact.name}` : ""}`}
                                    checked={
                                      !fields[operation.id]?.includes(field)
                                    }
                                    disabled={
                                      locked ||
                                      excluded.has(operation.id) ||
                                      operation.status === "succeeded" ||
                                      task.excluded_fields?.[
                                        operation.id
                                      ]?.includes(field) ||
                                      operation.kind === "association" ||
                                      String(value).startsWith("op:") ||
                                      (operation.action === "create" &&
                                        [
                                          "name",
                                          "domain",
                                          "firstname",
                                          "lastname",
                                          "hs_note_body",
                                          "hs_timestamp",
                                        ].includes(field))
                                    }
                                    onChange={() =>
                                      setFields({
                                        ...fields,
                                        [operation.id]: toggle(
                                          fields[operation.id] ?? [],
                                          field,
                                        ),
                                      })
                                    }
                                  />
                                  {field}
                                </label>
                              ) : (
                                field
                              )}
                            </th>
                            <td>
                              {String(operation.before[field] ?? "Not set")}
                            </td>
                            <td>
                              {fields[operation.id]?.includes(field)
                                ? "Excluded"
                                : String(value ?? "Clear value")}
                            </td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p>Associate the approved contact and company records.</p>
              )}
              {operation.error && (
                <p className="sales-error" role="alert">
                  {operation.error}
                </p>
              )}
            </details>
          );
        })}
        {!task.operations.length && (
          <p>
            No CRM operations are ready. Resolve the research or matching issue
            before approving.
          </p>
        )}
      </section>
      {task.gaps.length > 0 && (
        <section className="proposal-section">
          <h3>Research gaps</h3>
          <ul>
            {task.gaps.map((gap, i) => (
              <li key={i}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
      <div className="permission-scope">
        <Icon name="info" size={19} />
        <p>
          Approval applies to this version and its selected CRM changes.
          <span>No messages or outreach will be sent.</span>
        </p>
      </div>
      <div className="execution-status" aria-live="polite">
        <strong>CRM execution: {label(task.execution)}</strong>
        <span>Research memory: {label(task.memory_status)}</span>
        {task.decided_by && (
          <small>
            {label(task.status)} by {task.decided_by}
            {task.decided_at
              ? ` · ${new Date(task.decided_at).toLocaleString()}`
              : ""}
          </small>
        )}
        {task.error && <p className="sales-error">{task.error}</p>}
      </div>
      {error && (
        <p className="sales-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="sales-success" role="status">
          {notice}
        </p>
      )}
      {pending && (
        <div className="approval-actions">
          {dirty ? (
            <>
              <button
                className="button secondary"
                disabled={locked}
                onClick={() => {
                  setContacts(task.excluded_contact_ids);
                  setOperations(task.excluded_operation_ids);
                  setFields(task.excluded_fields ?? {});
                }}
              >
                Discard edits
              </button>
              <button
                className="button primary"
                disabled={locked}
                onClick={() =>
                  void run(
                    "edit",
                    () =>
                      actions.editProposal(
                        task.id,
                        task.version,
                        contacts,
                        operations,
                        fields,
                      ),
                    "A new proposal version is ready for review.",
                  )
                }
              >
                {busy === "edit" ? "Saving…" : "Save new version"}
              </button>
            </>
          ) : (
            <>
              <button
                className="button deny"
                disabled={locked}
                onClick={() =>
                  void run(
                    "denied",
                    () => actions.decide(task.id, task.version, "denied"),
                    "Denial recorded.",
                  )
                }
              >
                <Icon name="close" size={18} />
                {busy === "denied" ? "Saving…" : "Deny"}
              </button>
              <button
                className="button primary"
                disabled={locked || !included.length}
                onClick={() =>
                  void run(
                    "approved",
                    () => actions.decide(task.id, task.version, "approved"),
                    "Approval recorded. CRM execution is tracked separately.",
                  )
                }
              >
                <Icon name="check" size={19} />
                {busy === "approved" ? "Approving…" : "Approve CRM changes"}
              </button>
            </>
          )}
        </div>
      )}
      {task.status === "approved" &&
        ["partial", "failed"].includes(task.execution) && (
          <div className="approval-actions">
            <button
              className="button secondary"
              disabled={locked}
              onClick={() =>
                void run(
                  "retry",
                  () => actions.retry(task.id),
                  "Unfinished operations queued for retry.",
                )
              }
            >
              {busy === "retry" ? "Queuing…" : "Retry unfinished changes"}
            </button>
          </div>
        )}
    </article>
  );
}
