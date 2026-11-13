# syntax=docker/dockerfile:1.7
# The RepoAtlas web app (Next.js) for self-hosting. Build from the repository root:
#   docker build -f infra/docker/web.Dockerfile -t repoatlas-web .
#
# The app is built in standalone mode (NEXT_OUTPUT=standalone), so the runtime stage holds only
# the server and the files it needs, and runs as a user without privileges. The base image is
# pinned by digest (see api.Dockerfile for how to update it).
#
# The browser calls the API on the web app's own origin (NEXT_PUBLIC_API_URL=/, fixed at build
# time); the server forwards /api/v1/* to REPOATLAS_API_ORIGIN, which is read at run time, so
# one image works with any API address. REPOATLAS_ACCESS_TOKEN (run time) turns on the gate.
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone \
    NEXT_PUBLIC_API_URL=/
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
LABEL org.opencontainers.image.title="repoatlas-web" \
      org.opencontainers.image.source="https://github.com/rajatsxngh/RepoAtlas" \
      org.opencontainers.image.licenses="MIT"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# The files belong to root; the server runs as the image's "node" user and can change none.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "server.js"]
