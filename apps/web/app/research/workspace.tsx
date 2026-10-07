"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, errorMessage, isAbort, safeHttps } from "./client";
import type { Brief, Capabilities, Citation, CompetitorFact, Lead, Report, Run, RunDetail, Source } from "./types";
import s from "./workspace.module.css";

const initialBrief: Brief = {
  request: "Find US companies that could benefit from practical AI integration. Prioritize newly appointed technology or operations leaders, recent funding and partnerships, and clear operational needs Toir can help solve.",
  geography: "US", employee_min: 20, employee_max: 1000, target_count: 10,
  leadership_days: 180, signal_days: 90, deadline_seconds: 600,
};
const active = (run: Run) => run.status === "queued" || run.status === "running";
const statusLabel: Record<Run["status"], string> = {
  queued: "Queued", running: "Researching", completed: "Complete", failed: "Failed",
  cancelled: "Cancelled", interrupted: "Interrupted",
};
const signalLabel = { leadership: "New leadership", funding: "Funding", partnership: "Partnership", business_need: "Business need" };
const competitorGroups: { kind: CompetitorFact["kind"]; title: string }[] = [
  { kind: "advertisement", title: "Advertisements" }, { kind: "marketing", title: "Marketing & offers" },
  { kind: "pricing", title: "Public pricing" }, { kind: "customer", title: "Documented customers" },
];
const tabs = ["Companies", "Market context", "Sources"] as const;
type Tab = typeof tabs[number];

function date(value: string | null, withTime = false) {
  if (!value) return "Date not established";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00Z` : value);
  if (Number.isNaN(parsed.getTime())) return "Date not established";
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } as const : { timeZone: "UTC" }),
  }).format(parsed);
}
function stageName(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
function ExternalLink({ url, children }: { url: string; children: React.ReactNode }) {
  const href = safeHttps(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className={s.sourceLink}>{children}<span aria-hidden="true"> ↗</span><span className={s.srOnly}> (opens in a new tab)</span></a> : <span>{children}</span>;
}
function Status({ run }: { run: Run }) {
  return <span className={`${s.status} ${active(run) ? s.live : ""}`}><span aria-hidden="true" />{statusLabel[run.status]}</span>;
}
function Citations({ citations, sources }: { citations: Citation[]; sources: Map<string, Source> }) {
  return <details className={s.citations}>
    <summary>View evidence <span>{citations.length}</span></summary>
    <div className={s.evidenceList}>{citations.map((citation, index) => {
      const source = sources.get(citation.source_id);
      return <div className={s.evidence} key={`${citation.source_id}-${index}`}>
        <blockquote>“{citation.quote}”</blockquote>
        {source ? <><ExternalLink url={source.url}>{source.title || source.url}</ExternalLink><p className={s.caption}>{source.published_at ? `Published ${date(source.published_at)} · ` : ""}Retrieved {date(source.retrieved_at)}</p></> : <p className={s.caption}>Source unavailable in this report.</p>}
      </div>;
    })}</div>
  </details>;
}
function Company({ lead, index, sources }: { lead: Lead; index: number; sources: Map<string, Source> }) {
  return <article className={s.company}>
    <div className={s.companyHeading}>
      <span className={s.rank}>{String(index + 1).padStart(2, "0")}</span>
      <div className={s.companyIdentity}><h3>{lead.company}</h3><div className={s.companyMeta}><ExternalLink url={`https://${lead.domain}`}>{lead.domain}</ExternalLink><span>United States</span><span>{lead.employee_count === null ? "Company size unverified" : `${lead.employee_count.toLocaleString()} employees`}</span></div></div>
      <div className={s.fit}><strong>{lead.fit_score}</strong><span>Fit / 100</span></div>
    </div>
    <div className={s.companyBody}>
      <p className={s.rationale}>{lead.rationale}</p>
      {lead.decision_maker && <p className={s.decisionMaker}><span>Decision-maker</span>{lead.decision_maker}</p>}
      <Citations citations={lead.identity_citations} sources={sources} />
      <div className={s.signalList}><h4 className={s.sectionLabel}>Sourced buying signals</h4>{lead.signals.map((signal, i) => <div className={s.signal} key={`${signal.kind}-${i}`}>
        <div className={s.signalMeta}><strong>{signalLabel[signal.kind]}</strong><span>{signal.event_date ? `Event: ${date(signal.event_date)}` : "Event date unverified"}</span></div>
        <p>{signal.claim}</p><Citations citations={signal.citations} sources={sources} />
      </div>)}</div>
      <div className={s.hypotheses}><h4>Toir’s opportunity <span>Inferred</span></h4><dl><div><dt>AI use case</dt><dd>{lead.ai_use_case}</dd></div><div><dt>Outreach angle</dt><dd>{lead.outreach_angle}</dd></div></dl></div>
    </div>
  </article>;
}
function ReportView({ report, tab }: { report: Report; tab: Tab }) {
  const sources = new Map(report.sources.map((source) => [source.id, source]));
  if (tab === "Companies") return report.leads.length ? <div>{[...report.leads].sort((a, b) => b.fit_score - a.fit_score).map((lead, index) => <Company lead={lead} index={index} sources={sources} key={lead.domain} />)}</div> : <div className={s.resultEmpty}><h3>No qualified companies yet</h3><p>Companies appear when their buying signals have supporting evidence.</p></div>;
  if (tab === "Market context") return <div className={s.market}>
    <p className={s.contextNote}>Public competitor evidence, with hypotheses for Toir’s positioning. Missing prices or customer relationships are not assumed.</p>
    {competitorGroups.map((group) => {
      const facts = report.competitors.filter((fact) => fact.kind === group.kind);
      return <section className={s.marketGroup} key={group.kind}><h3>{group.title}<span>{facts.length}</span></h3>{facts.length ? facts.map((fact, index) => <article className={s.competitor} key={`${fact.company}-${index}`}><h4>{fact.company}</h4><p>{fact.claim}</p><Citations citations={fact.citations} sources={sources} />{fact.positioning_hypothesis && <div className={s.positioning}><span>Positioning hypothesis</span><p>{fact.positioning_hypothesis}</p></div>}</article>) : <p className={s.caption}>No verified evidence collected for this category.</p>}</section>;
    })}
  </div>;
  return report.sources.length ? <div className={s.sources}><p className={s.contextNote}>Retrieved material used for this report. Quotes attached to findings show the supporting passages.</p>{report.sources.map((source) => <article className={s.source} key={source.id}>
    <ExternalLink url={source.url}>{source.title || source.url}</ExternalLink><p className={s.sourceUrl}>{source.url}</p><p className={s.caption}>{source.published_at ? `Published ${date(source.published_at)} · ` : "Publication date unverified · "}Retrieved {date(source.retrieved_at)}</p><details className={s.sourceText}><summary>Read retrieved text</summary><p>{source.text}</p></details>
  </article>)}</div> : <div className={s.resultEmpty}><h3>No sources collected yet</h3><p>Retrieved pages and supporting passages will appear here.</p></div>;
}

export default function ResearchWorkspace() {
  const [brief, setBrief] = useState<Brief>(initialBrief);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [detailError, setDetailError] = useState("");
  const networkError = historyError || detailError;
  const [actionError, setActionError] = useState("");
  const [pending, setPending] = useState<"create" | "cancel" | "retry" | null>(null);
  const [tab, setTab] = useState<Tab>("Companies");
  const [refresh, setRefresh] = useState(0);
  const submission = useRef<{ payload: string; key: string } | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const initialSelection = useRef(false);
  const mounted = useRef(false);
  const actionController = useRef<AbortController | null>(null);
  const currentRun = detail?.run.id === selectedId ? detail.run : null;
  const busyRun = runs.find(active);
  const canStart = capabilities?.configured === true && !capabilities.maintenance && !busyRun && !pending;

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; actionController.current?.abort(); }; }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function sync() {
      try {
        const [history, setup] = await Promise.all([
          api<Run[]>("/research-runs", { signal: controller.signal }),
          api<Capabilities>("/research-capabilities", { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        setRuns(history.sort((a, b) => b.created_at.localeCompare(a.created_at)));
        setCapabilities(setup);
        setHistoryError("");
        if (!initialSelection.current) {
          initialSelection.current = true;
          setSelectedId(history[0]?.id ?? null);
        }
      } catch (error) {
        if (!isAbort(error)) setHistoryError(errorMessage(error));
      } finally {
        if (!controller.signal.aborted) { setLoaded(true); timer = setTimeout(sync, 8000); }
      }
    }
    void sync();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoadingDetail(true);
    async function sync() {
      try {
        const result = await api<RunDetail>(`/research-runs/${encodeURIComponent(selectedId!)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setDetail(result);
        setDetailError("");
        setRuns((previous) => previous.map((run) => run.id === result.run.id ? result.run : run));
        if (active(result.run)) timer = setTimeout(sync, 2000);
      } catch (error) {
        if (!isAbort(error)) { setDetailError(errorMessage(error)); timer = setTimeout(sync, 5000); }
      } finally { if (!controller.signal.aborted) setLoadingDetail(false); }
    }
    void sync();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedId, refresh]);

  const acceptRun = useCallback((run: Run) => {
    initialSelection.current = true;
    setRuns((previous) => [run, ...previous.filter((item) => item.id !== run.id)]);
    setSelectedId(run.id);
    setDetail({ run, events: [] });
    setTab("Companies");
    setRefresh((value) => value + 1);
    requestAnimationFrame(() => {
      if (!mounted.current) return;
      const results = document.getElementById("research-results");
      results?.focus({ preventScroll: true });
      if (window.matchMedia("(max-width: 780px)").matches) {
        results?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
      }
    });
  }, []);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canStart) return;
    if (brief.employee_min > brief.employee_max) { setActionError("Minimum company size must not exceed maximum company size."); return; }
    if (brief.request.trim().length < 10) { setActionError("Describe the companies or business needs to research in at least 10 characters."); return; }
    setActionError(""); setPending("create");
    const payload = JSON.stringify({ ...brief, request: brief.request.trim() });
    if (submission.current?.payload !== payload) submission.current = { payload, key: crypto.randomUUID() };
    const controller = new AbortController(); actionController.current = controller;
    try {
      const run = await api<Run>("/research-runs", { method: "POST", headers: { "Idempotency-Key": submission.current.key }, body: payload, signal: controller.signal });
      if (!mounted.current) return;
      submission.current = null;
      acceptRun(run);
    } catch (error) { if (mounted.current && !isAbort(error)) setActionError(errorMessage(error)); }
    finally { if (mounted.current) setPending(null); }
  }
  async function cancel() {
    if (!currentRun || pending) return;
    setPending("cancel"); setActionError("");
    const controller = new AbortController(); actionController.current = controller;
    try {
      const run = await api<Run>(`/research-runs/${encodeURIComponent(currentRun.id)}/cancellation`, { method: "POST", signal: controller.signal });
      if (mounted.current) acceptRun(run);
    } catch (error) { if (mounted.current && !isAbort(error)) setActionError(errorMessage(error)); }
    finally { if (mounted.current) setPending(null); }
  }
  async function retry() {
    if (!currentRun || pending) return;
    setPending("retry"); setActionError("");
    const controller = new AbortController(); actionController.current = controller;
    const key = retryKeys.current.get(currentRun.id) ?? crypto.randomUUID();
    retryKeys.current.set(currentRun.id, key);
    try {
      const run = await api<Run>(`/research-runs/${encodeURIComponent(currentRun.id)}/retries`, { method: "POST", headers: { "Idempotency-Key": key }, signal: controller.signal });
      if (mounted.current) { retryKeys.current.delete(currentRun.id); acceptRun(run); }
    } catch (error) { if (mounted.current && !isAbort(error)) setActionError(errorMessage(error)); }
    finally { if (mounted.current) setPending(null); }
  }
  function selectRun(run: Run) {
    if (run.id === selectedId) return;
    setSelectedId(run.id); setDetail(null); setActionError(""); setDetailError(""); setTab("Companies");
  }
  function tabKeys(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault(); setTab(tabs[next]); document.getElementById(`report-tab-${next}`)?.focus();
  }

  return <div className={s.workspace}>
    <div className={s.topbar}>
      <Link href="/" className={s.brand} aria-label="Toir home">toir<span className={s.brandDot}>.</span></Link>
      <div className={s.breadcrumb}><span>Company Brain</span><span aria-hidden="true">/</span><strong>Research</strong></div>
      <span className={s.internal}>Internal workspace</span>
    </div>
    <div className={s.heading}><div><p className={s.eyebrow}>Sales intelligence</p><h1>Research</h1></div><p>Find companies with a reason to act.<br />Review the evidence before reaching out.</p></div>
    {networkError && <div className={s.connectionError} role="alert"><p>{networkError}</p><button type="button" onClick={() => setRefresh((value) => value + 1)}>Reconnect</button></div>}
    {loaded && capabilities && !capabilities.configured && <div className={s.setup} role="status"><div className={s.setupMark} aria-hidden="true">!</div><div><strong>Research connections need setup</strong><p>An administrator needs to connect the research services before a run can start. Saved reports remain available.</p>{capabilities.missing.length > 0 && <details><summary>Connection details</summary><ul>{capabilities.missing.map((item) => <li key={item}>{item.replaceAll("_", " ")}</li>)}</ul></details>}</div></div>}
    {capabilities?.maintenance && <div className={s.setup} role="status"><div><strong>Research is paused for maintenance</strong><p>Saved reports remain available. New runs and retries will resume when maintenance is complete.</p></div></div>}
    <div className={s.columns}>
      <div className={s.sidebar}>
        <form className={s.briefForm} onSubmit={start}>
          <div className={s.sectionHeading}><h2>New research</h2><span>Up to 10 minutes</span></div>
          <label htmlFor="research-brief">Research focus</label><textarea id="research-brief" name="request" value={brief.request} onChange={(event) => setBrief({ ...brief, request: event.target.value })} rows={7} minLength={10} maxLength={4000} required aria-describedby="brief-help" />
          <p className={s.fieldHelp} id="brief-help">Describe industries, business needs or signals worth investigating for Toir.</p>
          <div className={s.fieldRow}><div><label htmlFor="geography">Market</label><select id="geography" value="US" onChange={() => {}}><option value="US">United States</option></select></div><div><label htmlFor="target-count">Companies</label><input id="target-count" type="number" min={1} max={10} required value={brief.target_count} onChange={(event) => setBrief({ ...brief, target_count: Number(event.target.value) })} /></div></div>
          <div className={s.fieldRow}><div><label htmlFor="employee-min">Min. employees</label><input id="employee-min" type="number" min={1} max={100000} required value={brief.employee_min} onChange={(event) => setBrief({ ...brief, employee_min: Number(event.target.value) })} /></div><div><label htmlFor="employee-max">Max. employees</label><input id="employee-max" type="number" min={1} max={100000} required value={brief.employee_max} onChange={(event) => setBrief({ ...brief, employee_max: Number(event.target.value) })} /></div></div>
          <details className={s.filters}><summary>Signal windows</summary><div className={s.fieldRow}><div><label htmlFor="leadership-days">New leaders · days</label><input id="leadership-days" type="number" min={1} max={365} required value={brief.leadership_days} onChange={(event) => setBrief({ ...brief, leadership_days: Number(event.target.value) })} /></div><div><label htmlFor="signal-days">Other signals · days</label><input id="signal-days" type="number" min={1} max={365} required value={brief.signal_days} onChange={(event) => setBrief({ ...brief, signal_days: Number(event.target.value) })} /></div></div></details>
          <button className={s.primary} type="submit" disabled={!canStart}>{pending === "create" ? "Starting research…" : "Start research"}<span aria-hidden="true">↗</span></button>
          <p className={s.fieldHelp}>{!loaded ? "Checking connections…" : capabilities?.maintenance ? "Research is paused for maintenance." : busyRun ? "One research run can be active at a time." : capabilities?.configured ? "Returns fewer companies when evidence is insufficient." : "Research will be available after connections are configured."}</p>
        </form>
        <section className={s.history} aria-labelledby="history-heading"><div className={s.sectionHeading}><h2 id="history-heading">Run history</h2><span>{runs.length}</span></div>{!loaded ? <p className={s.caption}>Loading runs…</p> : runs.length === 0 ? <p className={s.historyEmpty}>Your research runs will appear here.</p> : <ol>{runs.map((run) => <li key={run.id}><button type="button" className={`${s.historyItem} ${selectedId === run.id ? s.selected : ""}`} aria-current={selectedId === run.id ? "true" : undefined} onClick={() => selectRun(run)}><span className={s.historyTitle}>{run.brief.request}</span><span className={s.historyMeta}><Status run={run} /><time dateTime={run.created_at}>{date(run.created_at)}</time></span></button></li>)}</ol>}</section>
      </div>
      <main className={s.results} id="research-results" tabIndex={-1}>
        {actionError && <div className={s.actionError} role="alert">{actionError}</div>}
        {loadingDetail && !currentRun ? <div className={s.resultEmpty} role="status"><h2>Loading research…</h2></div> : !currentRun ? <div className={s.welcome}><p className={s.eyebrow}>Evidence before outreach</p><h2>Start with a research brief.</h2><p>Find prospective clients, understand their buying signals, and review a grounded angle for Toir.</p><div className={s.emptyColumns}><div><span>01</span><h3>Find the signal</h3><p>New leaders, funding, partnerships and operational needs.</p></div><div><span>02</span><h3>Check the evidence</h3><p>Source links and dated events behind each qualified company.</p></div><div><span>03</span><h3>Shape the approach</h3><p>AI use cases and competitive context to inform outreach.</p></div></div></div> : <>
          <div className={s.runHeading}><div><p className={s.eyebrow}>Research report</p><h2>Prospective clients</h2><p className={s.runDate}>Started {date(currentRun.created_at, true)}{currentRun.parent_id ? " · Retried from a previous run" : ""}</p></div><Status run={currentRun} /></div>
          <details className={s.runBrief}><summary>Research brief</summary><p>{currentRun.brief.request}</p><p className={s.caption}>Target: up to {currentRun.brief.target_count} companies · United States · {currentRun.brief.employee_min.toLocaleString()}–{currentRun.brief.employee_max.toLocaleString()} employees · New leaders: {currentRun.brief.leadership_days} days · Other signals: {currentRun.brief.signal_days} days</p></details>
          <div className={s.progress}><div aria-live="polite" aria-atomic="true"><strong>{stageName(currentRun.stage)}</strong><p>{active(currentRun) ? "Research continues if you leave this page." : currentRun.status === "completed" ? `${currentRun.report.leads.length} qualified ${currentRun.report.leads.length === 1 ? "company" : "companies"} · ${currentRun.report.sources.length} ${currentRun.report.sources.length === 1 ? "source" : "sources"} collected` : "Saved findings are available below. You can retry this brief when ready."}</p></div>{active(currentRun) ? <button type="button" className={s.secondary} onClick={cancel} disabled={!!pending}>{pending === "cancel" ? "Cancelling…" : "Cancel run"}</button> : <button type="button" className={s.secondary} onClick={retry} disabled={!!pending || !!busyRun || !capabilities?.configured || capabilities.maintenance}>{pending === "retry" ? "Starting…" : "Retry brief"}</button>}</div>
          {currentRun.error && <p className={s.runError} role="alert">{currentRun.error}</p>}
          {currentRun.report.summary && <p className={s.reportSummary}>{currentRun.report.summary}</p>}
          {detail && detail.events.length > 0 && <details className={s.activity}><summary>Run activity <span>{detail.events.length}</span></summary><ol>{detail.events.slice(-50).map((event) => <li key={event.sequence}><time dateTime={event.created_at}>{date(event.created_at, true)}</time><span>{event.message}</span></li>)}</ol></details>}
          {currentRun.report.gaps.length > 0 && <details className={s.gaps}><summary>Evidence gaps <span>{currentRun.report.gaps.length}</span></summary><ul>{currentRun.report.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></details>}
          <div className={s.tabs} role="tablist" aria-label="Research results">{tabs.map((label, index) => <button id={`report-tab-${index}`} key={label} type="button" role="tab" aria-selected={tab === label} aria-controls={`report-panel-${index}`} tabIndex={tab === label ? 0 : -1} onKeyDown={(event) => tabKeys(event, index)} onClick={() => setTab(label)}>{label}<span aria-hidden="true">{label === "Companies" ? currentRun.report.leads.length : label === "Market context" ? currentRun.report.competitors.length : currentRun.report.sources.length}</span></button>)}</div>
          {tabs.map((label, index) => <div key={label} role="tabpanel" id={`report-panel-${index}`} aria-labelledby={`report-tab-${index}`} hidden={tab !== label} tabIndex={0}>{tab === label && <ReportView report={currentRun.report} tab={label} />}</div>)}
          {Object.keys(currentRun.usage).length > 0 && <details className={s.usage}><summary>Run usage</summary><dl>{Object.entries(currentRun.usage).map(([key, value]) => <div key={key}><dt>{stageName(key)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl></details>}
        </>}
      </main>
    </div>
    <div className={s.footer}><span>Toir · Company Brain</span><span>Research informs outreach. No messages are sent.</span></div>
  </div>;
}
