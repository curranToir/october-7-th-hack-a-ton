// Run with Playwright CLI against a local Next server. No external requests are made.
async (page) => {
  const origin = new URL(page.url()).origin;
  if (!["127.0.0.1", "localhost"].includes(new URL(origin).hostname))
    throw new Error("Use a local test server");
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.unroute("**/api/**");
  const now = new Date().toISOString();
  const sales = {
    user: { email: "curran@toirinc.com", name: "Curran", workspace_id: "toir" },
    sessions: [], messages: [], tasks: [], jobs: [],
    automation: { enabled: false },
    capabilities: { ready: false, crm: false, reasons: [] },
  };
  const state = {
    meetings: [], tasks: [], capabilities: {
      owner_email: "curran@toirinc.com", repository: "curranToir/october-7-th-hack-a-ton",
      zoom_ready: true, analysis_ready: true, github_ready: false, missing: ["MEETING_GITHUB_TOKEN"],
    },
  };
  const quote = "When I click Export CSV the downloaded file is empty, despite 248 visible rows.";
  const transcript = [
    { id: "s1", speaker: "Alex", start: 8, text: quote },
    { id: "s2", speaker: "Alex", start: 26, text: "We are also evaluating Linear." },
  ];
  const issue = {
    title: "[Demo] Customer report exports empty CSV", problem: quote,
    impact: "The customer copies rows manually.", expected_behavior: "Export the 248 visible rows.",
    reproduction_steps: ["Open customer report", "Select Export CSV"],
    evidence: [{ segment_id: "s1", quote }],
  };
  const task = {
    id: "issue-1", meeting_id: "meeting-1", title: issue.title,
    body: `## Problem\n${quote}\n\n## Impact\nThe customer copies rows manually.\n\n<!-- toir-meeting-task:issue-1 -->`,
    repository: state.capabilities.repository, version: 1, status: "pending", source: "demo",
    created_at: now, decided_by: null, decided_at: null, issue_url: null, error: null,
  };
  const meeting = {
    id: "meeting-1", title: "Demo · Customer export issue", owner_email: "curran@toirinc.com",
    source: "demo", status: "ready", created_at: now, error: null, bot_id: null, transcript,
    notes: { summary: "A fictional customer reports an empty CSV export and mentions Linear.", customer: "Alex · Example customer", needs: ["Export visible rows"], next_steps: ["Review the engineering issue"], issues: [issue], mentions: [] },
    research: [{
      id: "r1", kind: "company", name: "Linear", context: "Product the customer is evaluating.",
      evidence: { segment_id: "s2", quote: transcript[1].text }, status: "completed", run_id: null,
      identity_status: "matched", summary: "Linear provides issue tracking software.", error: null,
      facts: [{ claim: "Linear provides issue tracking software.", citations: [{ source_id: "source-1", quote: "Linear provides issue tracking software for product teams." }] }],
      sources: [{ id: "source-1", title: "Linear product", url: "https://linear.app" }, { id: "unsafe", title: "Unsafe link", url: "javascript:alert(1)" }], gaps: [],
    }],
  };
  const calls = [];
  let failPoll = false;
  await page.route("**/api/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const data = req.postData() ? req.postDataJSON() : {};
    calls.push({ path, method, data, key: req.headers()["idempotency-key"] });
    const respond = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/sales/workspace") return respond(sales);
    if (path === "/api/meetings/workspace") return failPoll ? respond({ detail: "Temporary meeting connection failure" }, 503) : respond(state);
    if (path === "/api/meetings/demos") {
      if (!req.headers()["idempotency-key"]) throw new Error("Demo creation omitted its idempotency key");
      state.meetings.push(meeting); state.tasks.push(task);
      return respond(meeting, 202);
    }
    if (path === "/api/meetings/tasks/issue-1" && method === "PATCH") {
      if (data.version !== task.version) return respond({ detail: "The task changed; refresh before editing" }, 409);
      Object.assign(task, { title: data.title, body: data.body, version: task.version + 1 });
      return respond(task);
    }
    if (path === "/api/meetings/tasks/issue-1/decisions") {
      if (!state.capabilities.github_ready || data.version !== task.version) return respond({ detail: "Review the latest version" }, 409);
      await new Promise(resolve => setTimeout(resolve, 250));
      Object.assign(task, { status: data.decision === "approve" ? "approved" : "rejected", decided_by: sales.user.email, decided_at: now });
      return respond(task);
    }
    if (path === "/api/meetings/tasks/issue-1/reconciliation") {
      Object.assign(task, { status: "published", issue_url: "https://github.com/curranToir/october-7-th-hack-a-ton/issues/123", error: null });
      return respond(task);
    }
    if (path === "/api/meetings/tasks/issue-1/retries") {
      Object.assign(task, { status: "approved", error: null });
      return respond(task);
    }
    return respond({ detail: "Unexpected fixture request" }, 404);
  });
  const refresh = () => page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.goto(`${origin}/#meetings`);
  await page.getByRole("heading", { name: "Listen. Understand. Follow through." }).waitFor();
  await page.getByRole("button", { name: "Add Zoom call" }).click();
  await page.getByRole("textbox", { name: "Meeting title" }).fill("Live customer feedback");
  await page.getByRole("textbox", { name: "Zoom invitation link" }).fill("https://us02web.zoom.us/j/123456789");
  const join = page.getByRole("button", { name: "Add meeting agent" });
  if (!(await join.isDisabled())) throw new Error("Zoom submission allowed without consent");
  await page.getByRole("checkbox").check();
  if (await join.isDisabled()) throw new Error("Consent did not enable the configured Zoom integration");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Run demo", exact: true }).click();
  await page.getByRole("heading", { name: meeting.title }).waitFor();
  await page.getByText("What we heard", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Transcript (2)" }).click();
  await page.getByText(quote, { exact: true }).waitFor();
  await page.getByRole("button", { name: "Related research (1)" }).click();
  await page.getByRole("heading", { name: "Linear", exact: true }).waitFor();
  if (await page.getByRole("link", { name: "Unsafe link" }).count()) throw new Error("Unsafe research URL rendered as a link");
  await page.getByRole("button", { name: "Review issue", exact: true }).click();
  const approve = page.getByRole("button", { name: "Approve & create issue" });
  await approve.waitFor();
  if (!(await approve.isDisabled())) throw new Error("Issue approval allowed before GitHub connection");
  if (calls.some(call => call.path.endsWith("/decisions"))) throw new Error("A meeting automatically approved an issue");
  await page.getByRole("button", { name: "Edit draft" }).click();
  const title = page.getByRole("textbox", { name: "Issue title" });
  await title.fill("[Demo] Empty filtered CSV export needs investigation");
  await page.getByRole("button", { name: "Save new version" }).click();
  await page.getByText("Version 2", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Edit draft" }).click();
  await title.fill("My unsaved customer issue wording");
  failPoll = true;
  await refresh();
  await page.getByText("Temporary meeting connection failure", { exact: true }).waitFor();
  if (await title.inputValue() !== "My unsaved customer issue wording") throw new Error("Connection failure erased edits");
  failPoll = false;
  task.version = 3; task.title = "Updated by another reviewer";
  await page.getByRole("button", { name: "Retry connection" }).click();
  await page.getByText(/The saved task changed while you were editing/).waitFor();
  if (await title.inputValue() !== "My unsaved customer issue wording") throw new Error("Polling overwrote local draft");
  if (!(await page.getByRole("button", { name: "Save new version" }).isDisabled())) throw new Error("Stale version could be saved");
  await page.getByRole("button", { name: "Replace my draft with saved version 3" }).click();
  await title.fill("[Demo] Investigate empty filtered customer export");
  await page.getByRole("button", { name: "Save new version" }).click();
  await page.getByText("Version 4", { exact: true }).waitFor();
  state.capabilities.github_ready = true; state.capabilities.missing = [];
  await refresh();
  await approve.click();
  await page.getByRole("heading", { name: "No customer issues waiting." }).waitFor();
  const decisions = calls.filter(call => call.path.endsWith("/decisions"));
  if (decisions.length !== 1 || decisions[0].data.version !== 4 || decisions[0].data.decision !== "approve") throw new Error("Approval did not target exactly the reviewed version");
  await page.getByRole("button", { name: "History 1", exact: true }).click();
  await page.getByText("Approved · queued", { exact: true }).waitFor();
  if (await page.getByRole("link", { name: "Open GitHub issue" }).count()) throw new Error("UI fabricated a GitHub publication receipt");
  task.status = "publish_failed"; task.error = "GitHub rejected publication";
  await refresh();
  await page.getByRole("button", { name: "Retry approved publication" }).click();
  await page.getByText("Approved · queued", { exact: true }).waitFor();
  task.status = "publish_unknown"; task.error = "Provider receipt was lost";
  await refresh();
  await page.getByRole("button", { name: "Check GitHub result" }).click();
  await page.getByRole("link", { name: "Open GitHub issue" }).waitFor();
  if (calls.filter(call => call.path.endsWith("/decisions")).length !== 1) throw new Error("Retry or reconciliation created another approval");
  await page.setViewportSize({ width: 390, height: 844 });
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error("Meeting task view overflows on mobile");
  await page.goto(`${origin}/#meetings/meeting-1`);
  await page.getByRole("heading", { name: meeting.title }).waitFor();
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error("Meeting detail overflows on mobile");
  return { passed: true, checks: ["Zoom consent", "idempotent demo", "saved notes/transcript", "safe research sources", "GitHub readiness", "versioned editing", "error/draft preservation", "concurrent reviewer conflict", "explicit approval", "honest publication receipt", "retry/reconciliation", "mobile layout"], calls: calls.filter(call => call.method !== "GET") };
}
