import {
  context,
  propagation,
  trace,
  SpanStatusCode,
} from "@opentelemetry/api";
import {
  NodeTracerProvider,
  BatchSpanProcessor,
} from "@opentelemetry/sdk-trace-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
let provider: NodeTracerProvider | undefined;
export function initializeTelemetry(key: string | undefined) {
  if (!key || provider) return;
  provider = new NodeTracerProvider({
    spanProcessors: [
      new BatchSpanProcessor(
        new OTLPTraceExporter({
          url: "https://api.respan.ai/api/v2/traces",
          headers: { Authorization: `Bearer ${key}` },
          timeoutMillis: 10000,
        }),
        {
          maxQueueSize: 512,
          maxExportBatchSize: 64,
          scheduledDelayMillis: 2000,
        },
      ),
    ],
  });
  provider.register();
}
export const tracer = () => trace.getTracer("toir.research", "1.0.0");
export async function tracedTask<T>(
  headers: Record<string, string>,
  runID: string,
  taskID: string,
  work: () => Promise<T>,
  agentName: "research" | "contacts" = "research",
): Promise<T> {
  const parent = propagation.extract(context.active(), headers);
  return context.with(parent, () =>
    tracer().startActiveSpan(
      `${agentName}.task`,
      {
        attributes: {
          "service.name": `toir-${agentName}`,
          "toir.run_id": runID,
          "toir.task_id": taskID,
        },
      },
      async (span) => {
        try {
          return await work();
        } catch (error) {
          span.setStatus({
            code: SpanStatusCode.ERROR,
            message: "Research task failed",
          });
          throw error;
        } finally {
          span.end();
        }
      },
    ),
  );
}
export async function shutdownTelemetry() {
  await provider?.shutdown();
}
