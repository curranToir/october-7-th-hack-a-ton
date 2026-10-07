export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");
  const response = await fetch(`/api${path}`, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    let message = "The request could not be completed. Please try again.";
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        const detail = payload.detail;
        if (typeof detail === "string" && detail.length < 600) message = detail;
        else if (Array.isArray(detail)) message = "Check your research brief and company size limits, then try again.";
      }
    } catch { /* An upstream failure may return non-JSON. */ }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export function safeHttps(value: string): string | undefined {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Connection interrupted. Your run may still be in progress. Reconnect or retry the same request.";
}
