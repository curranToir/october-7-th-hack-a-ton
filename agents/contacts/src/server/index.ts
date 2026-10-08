import { MODEL_CONFIG } from "../../../research/src/research/limits";
import { createApp } from "./app";
import { contactHarness } from "../harness/omp";
import { missingCredentials, scalekitTransport } from "../../../research/src/tools/scalekit";
import { initializeTelemetry, shutdownTelemetry } from "../../../research/src/telemetry/respan";

initializeTelemetry(process.env.RESPAN_API_KEY);
const app = createApp({ missing: () => missingCredentials(), harness: (task, context) =>
  contactHarness(scalekitTransport(), process.env.RESPAN_API_KEY!, process.env.RESPAN_MODEL || MODEL_CONFIG.id)(task, context) });
const server = Bun.serve({ hostname: "0.0.0.0", port: Number(process.env.PORT || 8000), maxRequestBodySize: 2_000_000, fetch: app.fetch });
console.log("Contact research agent listening");
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await app.shutdown();
  server.stop(true);
  await shutdownTelemetry();
  process.exit(0);
}
process.on("SIGTERM", () => void stop());
process.on("SIGINT", () => void stop());
