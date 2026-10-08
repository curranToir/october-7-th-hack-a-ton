import { Tokenizer } from "@oh-my-pi/pi-agent-core/tokenizer";
import type { Context } from "@oh-my-pi/pi-ai";
import type { Source } from "../research/contracts";
import { ResearchError } from "../research/budget";

export const SOURCE_EXCERPT_CHARS = 4_000;
export const FINALIZE_INPUT_TOKENS = 220_000;
export const MAX_INPUT_TOKENS = 264_000; // Leaves 8k for provider framing below 272k.
const tokenizer = new Tokenizer();

/** Model-only projection. The host source registry keeps the full retrieved text. */
export function sourceExcerpts(sources: Source[], prior: unknown = null): Source[] {
  const quotes = new Map<string, string[]>();
  function collect(value: unknown) {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) { value.forEach(collect); return; }
    const item = value as Record<string, unknown>;
    if (typeof item.source_id === "string" && typeof item.quote === "string")
      quotes.set(item.source_id, [...(quotes.get(item.source_id) ?? []), item.quote]);
    Object.entries(item).filter(([key]) => key !== "sources").forEach(([, child]) => collect(child));
  }
  collect(prior);
  return sources.map(source => {
    // Include evidence buried below navigation/marketing copy, not just a prefix.
    const windows = [source.text.slice(0, 800)];
    for (const pattern of [/(?:employees?|headcount|employs|team of|company size)/gi, /(?:appointed|chief|funding|raised|partnership|integrat|automation)/gi]) {
      let count = 0;
      for (const match of source.text.matchAll(pattern)) {
        const start = Math.max(0, match.index! - 160);
        const window = source.text.slice(start, start + 400);
        if (!windows.some(existing => existing.includes(window))) windows.push(window);
        if (++count >= 4) break;
      }
    }
    let text = windows.join("\n[…]\n").slice(0, SOURCE_EXCERPT_CHARS);
    for (const quote of quotes.get(source.id) ?? []) {
      if (source.text.includes(quote) && !text.includes(quote)) text += "\n[…]\n" + quote;
    }
    return { ...source, text };
  });
}

/** Uses the SDK's native o200k count (conservative byte fallback if unavailable). */
export function boundedContext(context: Context): { context: Context; finalizing: boolean } {
  const serialized = JSON.stringify(context);
  if (!tokenizer.checkTokenBudget(serialized, MAX_INPUT_TOKENS).fits)
    throw new ResearchError("budget", "Research exhausted its input context budget. Saved evidence is available.");
  const finalizing = !tokenizer.checkTokenBudget(serialized, FINALIZE_INPUT_TOKENS).fits;
  return {
    context: finalizing ? { ...context, tools: [], systemPrompt: [...(context.systemPrompt ?? []), "The input context is near its limit. Do not call any more tools. Return the final JSON report now from verified evidence, with gaps for missing coverage."] } : context,
    finalizing,
  };
}
