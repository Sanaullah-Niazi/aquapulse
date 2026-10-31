# ---------------------------------------------------------------
# The web app (M8) inside the Caddy web server.
# Stage 1 builds the React app into plain files with Node and pnpm.
# Stage 2 is the same Caddy as before (M1), plus those files in /app-dist.
# Build context: the repository root (see docker-compose.yml).
# ---------------------------------------------------------------
FROM node:22.21.1-slim AS build
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# The folder the app is served from (Caddy: /app/).
ARG VITE_BASE=/app/
RUN VITE_BASE=$VITE_BASE pnpm build

FROM caddy:2.11.7-alpine
COPY --from=build /web/dist /app-dist
