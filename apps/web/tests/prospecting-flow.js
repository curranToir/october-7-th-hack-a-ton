// prettier-ignore
async (page) => {
  // Run through Playwright CLI on a local Next server; all /api calls are isolated fixtures.
  const origin = new URL(page.url()).origin;
  if (!["127.0.0.1", "localhost"].includes(new URL(origin).hostname))
    throw new Error("Use a local test server");
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.unroute("**/api/**");
  const now = new Date().toISOString();
  const base = { workspace_id: "toir", created_at: now, updated_at: now };
  const citation = {
    source_id: "src_0000000000000001",
    quote:
      "Example Company employs Jane Example as its Chief Operating Officer.",
  };
  const source = {
    id: citation.source_id,
    url: "https://example.com/about",
    title: "Company leadership",
    retrieved_at: now,
    text: citation.quote,
  };
  const company = {
    name: "Example Company",
    domain: "example.com",
    description: "A US company investing in operations automation.",
    country: "US",
    employee_count: 120,
    citations: [citation],
    fit_score: 82,
    sales_angle:
      "Discuss automating document workflows with the operations team.",
  };
  const contact = {
    id: "c1",
    name: "Jane Example",
    title: "Chief Operating Officer",
    company_domain: "example.com",
    buying_relevance: "Owns operations and budget for workflow improvements.",
    linkedin_url: "https://www.linkedin.com/in/jane-example",
    email: null,
    phone: null,
    citations: [citation],
    linkedin_citations: [citation],
    email_citations: [],
    phone_citations: [],
  };
  const operation = {
    id: "op1",
    kind: "company",
    action: "create",
    contact_id: null,
    record_id: null,
    properties: {
      name: company.name,
      domain: company.domain,
      description: company.description,
    },
    before: {},
    depends_on: [],
    status: "pending",
    result_id: null,
    error: null,
  };
  const state = {
    user: { email: "curran@toirinc.com", name: "Curran", workspace_id: "toir" },
    sessions: [
      {
        ...base,
        id: "s1",
        title: "Research Example Company",
        owner: "curran@toirinc.com",
        automation: false,
        deleted: false,
      },
    ],
    messages: [
      {
        ...base,
        id: "m1",
        session_id: "s1",
        role: "user",
        content: "Research Example Company and add it to my CRM",
        task_ids: [],
        job_id: "j1",
        request_key: "k1",
      },
      {
        ...base,
        id: "m2",
        session_id: "s1",
        role: "assistant",
        content: "Research is ready. Review the proposed CRM changes below.",
        task_ids: ["p1"],
        job_id: "j1",
        request_key: null,
      },
    ],
    tasks: [
      {
        ...base,
        id: "p1",
        session_id: "s1",
        job_id: "j1",
        requested_by: "curran@toirinc.com",
        company,
        contacts: [contact],
        sources: [source],
        gaps: ["A public business email was not found."],
        version: 1,
        status: "pending",
        execution: "not_started",
        operations: [
          operation,
          {
            ...operation,
            id: "op2",
            kind: "contact",
            contact_id: "c1",
            properties: {
              firstname: "Jane",
              lastname: "Example",
              jobtitle: contact.title,
            },
          },
        ],
        excluded_contact_ids: [],
        excluded_operation_ids: [],
        excluded_fields: {},
        decided_by: null,
        decided_at: null,
        error: null,
        memory_status: "pending",
      },
    ],
    jobs: [
      {
        ...base,
        id: "j1",
        session_id: "s1",
        requested_by: "curran@toirinc.com",
        kind: "enrich",
        origin: "chat",
        status: "completed",
        query: "Research Example Company",
        propose_crm: true,
        company,
        candidates: [],
        sources: [source],
        report: null,
        task_id: "worker1",
        research_run_id: null,
        deadline_at: null,
        progress: "Company and contact research complete",
        error: null,
        budget_day: null,
        parent_id: null,
      },
    ],
    automation: {
      ...base,
      id: "default",
      enabled: false,
      owner: "curran@toirinc.com",
      request:
        "Find US companies with practical AI integration needs and recent buying signals.",
      geography: "US",
      employee_min: 20,
      employee_max: 1000,
      fit_threshold: 70,
      daily_enrichments: 25,
      daily_discoveries: 10,
      timezone: "America/Los_Angeles",
    },
    capabilities: {
      ready: true,
      reasons: [],
      postgres: true,
      research: true,
      contacts: true,
      crm: true,
      brain: true,
      auth: true,
    },
  };
  let signedIn = true;
  const calls = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname,
      method = req.method();
    const data = req.postData() ? req.postDataJSON() : {};
    calls.push({ path, method, data });
    const respond = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (path === "/api/auth/logout") {
      signedIn = false;
      return route.fulfill({ status: 204 });
    }
    if (!signedIn)
      return respond({ detail: "Sign in to your sales workspace." }, 401);
    if (path === "/api/sales/workspace") return respond(state);
    if (path === "/api/sales/tasks/p1" && method === "PATCH") {
      Object.assign(state.tasks[0], data, {
        version: state.tasks[0].version + 1,
      });
      return respond(state.tasks[0]);
    }
    if (path === "/api/sales/tasks/p1/decisions") {
      await new Promise((resolve) => setTimeout(resolve, 450));
      Object.assign(state.tasks[0], {
        status: data.decision,
        execution: data.decision === "approved" ? "queued" : "not_started",
        decided_by: state.user.email,
        decided_at: now,
      });
      return respond(state.tasks[0]);
    }
    if (path === "/api/sales/automation") {
      Object.assign(state.automation, data, {
        updated_at: new Date().toISOString(),
      });
      return respond(state.automation);
    }
    if (path === "/api/sales/sessions/s1/messages") {
      const message = {
        ...base,
        id: `m${state.messages.length + 1}`,
        session_id: "s1",
        role: "user",
        content: data.content,
        task_ids: [],
        job_id: null,
        request_key: req.headers()["idempotency-key"],
      };
      state.messages.push(message);
      return respond(message, 202);
    }
    if (path === "/api/sales/sessions/s1" && method === "PATCH") {
      state.sessions[0].title = data.title;
      return respond(state.sessions[0]);
    }
    if (path === "/api/sales/sessions/s1" && method === "DELETE") {
      state.sessions[0].deleted = true;
      return route.fulfill({ status: 204 });
    }
    return respond({ detail: "Unimplemented test fixture request" }, 404);
  });
  await page.goto(`${origin}/#chat/s1`);
  await page
    .getByRole("heading", { name: "Review CRM changes for Example Company" })
    .waitFor();
  await page
    .getByRole("checkbox", { name: "Include description in company" })
    .uncheck();
  await page.getByRole("button", { name: "Save new version" }).click();
  await page.getByText("Proposal version 2", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Tasks", exact: false })
    .first()
    .click();
  const description = page.getByRole("checkbox", {
    name: "Include description in company",
  });
  if ((await description.isChecked()) || !(await description.isDisabled()))
    throw new Error("Saved exclusion was lost across views");
  const approving = page.getByRole("button", { name: "Approve CRM changes" });
  await approving.click();
  if (!(await page.getByRole("button", { name: "Approving…" }).isDisabled()))
    throw new Error("Approval was not disabled while awaiting the server");
  await page.getByRole("heading", { name: "You’re all caught up." }).waitFor();
  if (calls.filter((call) => call.path.endsWith("/decisions")).length !== 1)
    throw new Error("Duplicate approval");
  if (state.tasks[0].execution !== "queued" || state.tasks[0].version !== 2)
    throw new Error("Incorrect decision version or execution status");
  await page.getByRole("tab", { name: "History" }).click();
  await page.getByText("CRM execution: queued", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Research Example Company", exact: true })
    .click();
  await page.getByText("CRM execution: queued", { exact: true }).waitFor();
  const message = "Research another.example.com without adding it to my CRM";
  await page.getByRole("textbox", { name: "Message TOIR" }).fill(message);
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page.getByText(message, { exact: true }).waitFor();
  await page.reload();
  await page.getByText(message, { exact: true }).waitFor();
  if (state.messages.filter((m) => m.content === message).length !== 1)
    throw new Error("Message did not survive reload exactly once");
  await page
    .getByRole("button", { name: "Prospecting paused", exact: true })
    .click();
  await page.getByRole("spinbutton", { name: "Minimum fit score" }).fill("75");
  await page.getByRole("button", { name: "Save automation" }).click();
  await page.getByText("Automation settings saved.", { exact: true }).waitFor();
  if (state.automation.fit_threshold !== 75)
    throw new Error("Targeting settings not persisted");
  if (
    "geography" in
    calls.filter((call) => call.path.endsWith("/automation")).at(-1).data
  )
    throw new Error("Unsupported settings mutation");
  await page.getByRole("switch", { name: "Continuous prospecting" }).click();
  await page.getByRole("button", { name: "Prospecting active" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .waitFor({ state: "hidden" });
  if (
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    )
  )
    throw new Error("Mobile page overflows");
  await page.getByRole("button", { name: "Profile", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  const signIn = page.getByRole("link", { name: "Sign in with Scalekit" });
  await signIn.waitFor();
  if ((await signIn.getAttribute("href")) !== "/api/auth/login")
    throw new Error("Incorrect sign-in URL");
  if (await page.getByRole("article").count())
    throw new Error("Private proposal remains visible after sign-out");
  console.log(
    "PASS: proposal editing, cross-view persistence, approval locking, execution history, chat reload, automation, mobile layout, sign-out",
  );
}
