FROM oven/bun:1.3.14-slim@sha256:d56a2534ffd262e92c12fd3249d3924d296d97086da773f821d7d0477435ea04 AS dependencies
WORKDIR /app
COPY agents/research/package.json agents/research/bun.lock ./
# Native OMP bindings are optional platform packages, so retain them.
# Lifecycle scripts stay disabled; no model download.
RUN bun install --frozen-lockfile --production --ignore-scripts \
    && rm -rf node_modules/@huggingface node_modules/onnxruntime-node node_modules/onnxruntime-web node_modules/sherpa-onnx-node
FROM oven/bun:1.3.14-slim@sha256:d56a2534ffd262e92c12fd3249d3924d296d97086da773f821d7d0477435ea04
WORKDIR /app
ENV NODE_ENV=production HOME=/tmp/toir
COPY --from=dependencies /app/node_modules ./node_modules
COPY agents/research/package.json ./
COPY agents/research/src ./src
COPY agents/research/contracts ./contracts
USER 1000:1000
EXPOSE 8000
CMD ["bun", "src/server/index.ts"]
