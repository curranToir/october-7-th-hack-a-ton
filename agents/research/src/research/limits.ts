import { Effort } from "@oh-my-pi/pi-catalog/effort";

/** Shared run-wide ceilings for research and contact workers. */
export const WORKER_LIMITS = Object.freeze({
  active_tasks: 1,
  searches: 60,
  pages: 200,
  model_turns: 60,
  deadline_seconds: 600,
});

/** Conservative application ceilings within GPT-5 mini's published limits. */
export const MODEL_CONFIG = Object.freeze({
  id: "gpt-5-mini",
  context_tokens: 272_000,
  completion_tokens: 16_384,
  reasoning_effort: Effort.Low,
});
