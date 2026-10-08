import { MODEL_CONFIG } from "../research/limits";
import { createApp } from "./app";
import { ompHarness } from "../harness/omp";
import { subjectHarness } from "../subjects/harness";
import { missingCredentials, scalekitTransport } from "../tools/scalekit";
import { initializeTelemetry, shutdownTelemetry } from "../telemetry/respan";
initializeTelemetry(process.env.RESPAN_API_KEY);
// Defer credential-dependent construction so the pod can report actionable setup status.
const app = createApp({
  missing: () => missingCredentials(),
  subjectHarness: (task, context) =>
    subjectHarness(
      scalekitTransport(),
      process.env.RESPAN_API_KEY!,
      process.env.RESPAN_MODEL || "gpt-5.4",
    )(task, context),
  harness: (task, context) =>
    ompHarness(
      scalekitTransport(),
      process.env.RESPAN_API_KEY!,
      process.env.RESPAN_MODEL || MODEL_CONFIG.id,
    )(task, context),
});
const server = Bun.serve({
  hostname: "0.0.0.0",
  port: Number(process.env.PORT || 8000),
  maxRequestBodySize: 2_000_000,
  fetch: app.fetch,
});
console.log("Research agent listening");
async function stop() {
  await app.shutdown();
  server.stop(true);
  await shutdownTelemetry();
  process.exit(0);
}
process.on("SIGTERM", () => void stop());
process.on("SIGINT", () => void stop());
