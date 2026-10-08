import { ScalekitClient } from "@scalekit-sdk/node";
import { ResearchError, Budget } from "../research/budget";
import { Sources, publicURL } from "../research/sources";
export const CREDENTIALS = [
  "RESPAN_API_KEY",
  "SCALEKIT_ENVIRONMENT_URL",
  "SCALEKIT_CLIENT_ID",
  "SCALEKIT_CLIENT_SECRET",
] as const;
export function missingCredentials(
  env: Record<string, string | undefined> = process.env,
) {
  return CREDENTIALS.filter((k) => !env[k]?.trim());
}
export interface ToolTransport {
  execute(name: string, input: Record<string, unknown>): Promise<unknown>;
}
export function scalekitTransport(
  env: Record<string, string | undefined> = process.env,
): ToolTransport {
  const client = new ScalekitClient(
    env.SCALEKIT_ENVIRONMENT_URL!,
    env.SCALEKIT_CLIENT_ID!,
    env.SCALEKIT_CLIENT_SECRET!,
    { timeoutMs: 15000, toolTimeoutMs: 25000 },
  );
  return {
    execute: async (name, params) => {
      const result = await client.tools.executeTool({
        toolName: name,
        params,
        connector: env.SCALEKIT_CONNECTION_NAME || "exa",
        identifier: env.SCALEKIT_ACCOUNT_ID || "toir",
      });
      return result.data;
    },
  };
}
function code(error: unknown): number {
  if (!error || typeof error !== "object") return Number.NaN;
  const status = error as {
    grpcStatus?: unknown;
    httpStatus?: unknown;
    status?: unknown;
    statusCode?: unknown;
    code?: unknown;
  };
  // Scalekit 2.19 exposes getters, not status/code. Prefer its gRPC status:
  // the SDK can map an unfamiliar HTTP statusText to 500 while preserving
  // the original authentication/rate-limit status in grpcStatus.
  return (
    [
      status.grpcStatus,
      status.httpStatus,
      status.status,
      status.statusCode,
      status.code,
    ]
      .map(Number)
      .find((value) => Number.isInteger(value) && value > 0) ?? Number.NaN
  );
}
function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new ResearchError("cancelled", "Research was cancelled."));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}
export class ExaTools {
  constructor(
    private transport: ToolTransport,
    readonly sources: Sources,
    readonly budget: Budget,
    private progress: (message: string) => void,
  ) {}
  private async call(
    name: string,
    input: Record<string, unknown>,
    kind: "searches" | "pages",
    amount = 1,
  ): Promise<unknown> {
    for (let attempt = 0; attempt < 3; attempt++) {
      this.budget.consume(kind, amount);
      // Text returned by search/similarity also consumes the page budget. Reserve
      // requested slots before the provider call so failed calls cannot evade limits.
      if (kind === "searches")
        this.budget.consume("pages", Number(input.num_results));
      this.progress(
        name === "exa_crawl"
          ? "Reading source pages"
          : "Searching public company evidence",
      );
      try {
        const result = await this.transport.execute(name, input);
        this.budget.check();
        if (!result || typeof result !== "object")
          throw new ResearchError(
            "provider",
            "Scalekit returned an invalid search response.",
          );
        const data = result as Record<string, unknown>;
        if (data.error || data.isError)
          throw new ResearchError(
            "provider",
            "Scalekit could not retrieve this evidence. Check the connected account and try again.",
          );
        if (!Array.isArray(data.results))
          throw new ResearchError(
            "provider",
            "Scalekit returned an unexpected search response.",
          );
        const sources = data.results
          .map((v) =>
            v && typeof v === "object"
              ? this.sources.add(v as Record<string, unknown>)
              : null,
          )
          .filter(Boolean);
        return {
          sources,
          result_count: data.results.length,
          notice:
            data.results.length === 0
              ? "No matching sources were returned."
              : sources.length === 0
                ? "Search returned no usable page text. Try reading the result pages."
                : undefined,
          links: data.results
            .slice(0, 8)
            .filter(
              (v) => v && typeof v === "object" && typeof v.url === "string",
            )
            .flatMap((v) => {
              try {
                return [
                  {
                    url: publicURL(v.url),
                    title:
                      typeof v.title === "string" ? v.title.slice(0, 500) : "",
                  },
                ];
              } catch {
                return [];
              }
            }),
        };
      } catch (error) {
        if (error instanceof ResearchError) throw error;
        const status = code(error);
        if ([401, 403, 7, 16].includes(status))
          throw new ResearchError(
            "connection",
            "Scalekit authentication or the Exa connected account requires attention.",
          );
        if (
          [429, 500, 502, 503, 504, 4, 8, 13, 14].includes(status) &&
          attempt < 2
        ) {
          await wait(500 * 2 ** attempt, this.budget.signal);
          continue;
        }
        throw new ResearchError(
          "provider",
          [429, 8].includes(status)
            ? "Scalekit or Exa rate limit exhausted. Retry later."
            : "Scalekit could not retrieve this evidence. Check the connected account and try again.",
        );
      }
    }
  }
  search(query: string) {
    const count = Math.min(5, this.budget.remaining().pages);
    if (count < 1)
      throw new ResearchError("budget", "Research exhausted its pages budget.");
    return this.call(
      "exa_search",
      {
        query,
        num_results: count,
        include_text: true,
        max_characters: 7000,
        include_summary: false,
        include_highlights: false,
      },
      "searches",
    );
  }
  similar(url: string) {
    const count = Math.min(5, this.budget.remaining().pages);
    if (count < 1)
      throw new ResearchError("budget", "Research exhausted its pages budget.");
    return this.call(
      "exa_find_similar",
      {
        url: publicURL(url),
        num_results: count,
        include_text: true,
        max_characters: 7000,
      },
      "searches",
    );
  }
  read(urls: string[]) {
    const safe = [...new Set(urls.map(publicURL))].slice(0, 3);
    return this.call(
      "exa_crawl",
      {
        urls: safe,
        max_characters: 12000,
        include_summary: false,
        include_highlights: false,
        include_html_tags: false,
      },
      "pages",
      safe.length,
    );
  }
}
