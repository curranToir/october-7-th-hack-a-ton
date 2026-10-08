import type { Usage } from "./contracts";
export class ResearchError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export class Budget {
  readonly usage: Usage;
  readonly deadline: number;
  constructor(
    prior: Record<string, number>,
    deadline: string,
    readonly signal: AbortSignal,
    private readonly limits = { searches: 30, pages: 60, model_turns: 30 },
  ) {
    this.usage = Object.fromEntries(
      ["searches", "pages", "model_turns", "input_tokens", "output_tokens"].map(
        (k) => [k, prior[k] ?? 0],
      ),
    ) as Usage;
    this.deadline = Math.min(Date.parse(deadline), Date.now() + 600_000);
  }
  check() {
    if (this.signal.aborted)
      throw new ResearchError("cancelled", "Research was cancelled.");
    if (Date.now() >= this.deadline)
      throw new ResearchError(
        "deadline",
        "Research reached its deadline. Saved evidence is available for an explicit retry.",
      );
  }
  consume(kind: "searches" | "pages" | "model_turns", amount = 1) {
    this.check();
    if (this.usage[kind] + amount > this.limits[kind])
      throw new ResearchError(
        "budget",
        `Research exhausted its ${kind.replace("_", " ")} budget.`,
      );
    this.usage[kind] += amount;
  }
}
export function safeError(error: unknown): string {
  if (error instanceof ResearchError) return error.message;
  return "Research failed while calling a provider. Check credential configuration and the correlated trace, then retry explicitly.";
}
