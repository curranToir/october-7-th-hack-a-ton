FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS build
WORKDIR /build
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci --no-audit --no-fund
COPY apps/web ./apps/web
COPY coms ./coms
RUN npm run build:web

FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /build/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /build/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /build/apps/web/public ./apps/web/public
USER 1000:1000
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
