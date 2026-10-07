import type { WorkspaceState, Task } from "./types.ts";

export function createDemoWorkspace(): WorkspaceState {
  const now = new Date().toISOString();
  const base = { status: "pending" as const, createdAt: now };
  const tasks: Task[] = [
    {
      ...base,
      id: "task-maya",
      sessionId: "prospects",
      agent: "Prospecting agent",
      title: "Add this contact to your CRM?",
      subject: "Maya Chen",
      subtitle: "Head of Operations · Northstar Logistics",
      finding:
        "A relevant prospect with a verified company profile. Their team is expanding operations across the West Coast.",
      source: "Company contact page",
      sourceDetail:
        "Example research note: Northstar Logistics lists Maya Chen as Head of Operations. Her team is expanding its regional operations. This is fictional demo data, not a live research result.",
      steps: [
        "Find a relevant contact",
        "Check CRM for duplicates",
        "Create contact in HubSpot",
      ],
      scope: "Creates one contact with name, role, company and source.",
      restriction: "No email will be sent.",
      approveLabel: "Approve & add to CRM",
    },
    {
      ...base,
      id: "task-eli",
      sessionId: "prospects",
      agent: "Prospecting agent",
      title: "Add this company to your CRM?",
      subject: "Waypoint Supply",
      subtitle: "Logistics & supply chain · Portland, OR",
      finding:
        "A growing regional distributor that fits your ideal customer profile. No existing company record was found in the demo CRM.",
      source: "Company overview",
      sourceDetail:
        "Example research note: Waypoint Supply is a fictional distributor with a growing operations team. The agent matched its company profile against the example prospect criteria.",
      steps: [
        "Research the company",
        "Check existing accounts",
        "Create company in HubSpot",
      ],
      scope:
        "Creates one company record with its name, industry and research notes.",
      restriction: "No contacts will be messaged.",
      approveLabel: "Approve & add company",
    },
    {
      ...base,
      id: "task-pipeline",
      sessionId: "pipeline",
      agent: "Pipeline agent",
      title: "Update this opportunity?",
      subject: "Meridian Systems",
      subtitle: "Discovery completed · $24,000 opportunity",
      finding:
        "The discovery meeting notes indicate that the team is ready for a product demonstration. The opportunity can move to the next stage.",
      source: "Discovery meeting notes",
      sourceDetail:
        "Example meeting summary: the buyer confirmed a timeline and requested a product demonstration. This sample note illustrates the evidence an agent would include with an approval request.",
      steps: [
        "Review meeting notes",
        "Match the opportunity",
        "Move stage to Demo scheduled",
      ],
      scope:
        "Updates the stage of one opportunity and adds the meeting summary.",
      restriction: "The deal value and owner stay the same.",
      approveLabel: "Approve & update stage",
    },
    {
      ...base,
      id: "task-followup",
      sessionId: "followups",
      agent: "Customer agent",
      title: "Save this follow-up task?",
      subject: "Harbor & Co.",
      subtitle: "Customer check-in · Due tomorrow",
      finding:
        "The account has an open onboarding question. A follow-up task will help the account owner keep the conversation moving.",
      source: "Account activity",
      sourceDetail:
        "Example account activity: the customer asked for help with onboarding. The agent proposes creating an internal follow-up task for the account owner.",
      steps: [
        "Review account activity",
        "Identify the next step",
        "Create an internal follow-up task",
      ],
      scope: "Adds one follow-up task assigned to the account owner.",
      restriction: "No customer-facing message will be sent.",
      approveLabel: "Approve & save task",
    },
  ];
  return {
    version: 1,
    profile: {
      name: "Alex Morgan",
      email: "alex@example.com",
      role: "Workspace owner",
    },
    workspace: {
      name: "Company Brain",
      description: "A shared workspace for your company’s agents.",
    },
    preferences: {
      theme: "light",
      compactSidebar: false,
      timeZone: "America/Los_Angeles",
      defaultAgent: "Auto",
      autoAdvance: true,
      requireApproval: true,
      notifyTasks: true,
      notifyCompleted: true,
      sound: false,
      saveHistory: true,
    },
    sessions: [
      {
        id: "prospects",
        title: "Find new prospects",
        agent: "Prospecting agent",
        createdAt: now,
        messages: [
          {
            id: "message-1",
            role: "user",
            content: "Find logistics companies that could use our software.",
            createdAt: now,
          },
          {
            id: "message-2",
            role: "assistant",
            content:
              "I found two promising prospects and checked for duplicates in your CRM.\n\nEach proposed action is ready for your review.",
            taskIds: ["task-maya", "task-eli"],
            createdAt: now,
          },
        ],
      },
      {
        id: "pipeline",
        title: "Weekly pipeline review",
        agent: "Pipeline agent",
        createdAt: now,
        messages: [
          {
            id: "message-3",
            role: "assistant",
            content:
              "I reviewed your pipeline and found an opportunity that is ready for its next stage. Take a look before I update it.",
            taskIds: ["task-pipeline"],
            createdAt: now,
          },
        ],
      },
      {
        id: "followups",
        title: "Customer follow-ups",
        agent: "Customer agent",
        createdAt: now,
        messages: [
          {
            id: "message-4",
            role: "assistant",
            content:
              "Harbor & Co. could use an onboarding check-in. I prepared a follow-up task for your review.",
            taskIds: ["task-followup"],
            createdAt: now,
          },
        ],
      },
      {
        id: "research",
        title: "Research Northstar",
        agent: "Research agent",
        createdAt: now,
        messages: [
          {
            id: "message-5",
            role: "user",
            content: "What do we know about Northstar Logistics?",
            createdAt: now,
          },
          {
            id: "message-6",
            role: "assistant",
            content:
              "Northstar Logistics is an example account in this workspace. The operations team is expanding across the West Coast.\n\nThe prospecting session contains a proposed contact for your review.",
            createdAt: now,
          },
        ],
      },
    ],
    tasks,
    connections: [
      {
        id: "hubspot",
        name: "HubSpot",
        description: "Contacts, companies and opportunities",
        connected: true,
        initials: "H",
      },
      {
        id: "slack",
        name: "Slack",
        description: "Team conversations and updates",
        connected: false,
        initials: "S",
      },
      {
        id: "drive",
        name: "Google Drive",
        description: "Documents and company knowledge",
        connected: false,
        initials: "G",
      },
    ],
  };
}
