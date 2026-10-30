FROM node:24.22.1-bookworm-slim AS base
WORKDIR /app
RUN npm install -g pnpm@12.15.0

FROM base AS build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build && mkdir /app/worker-deps && cp -RL node_modules/zod node_modules/csv-parse node_modules/undici /app/worker-deps/

FROM node:24.22.1-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000 PAPER_TRACKER_HOME=/app/instance DATA_DIR=/app/instance/data CATALOG_PATH=/app/catalog/catalog.sqlite SESSION_SECRET_FILE=/app/instance/secrets/session_secret ENCRYPTION_KEY_FILE=/app/instance/secrets/encryption_key
COPY --from=build /app/.next/standalone ./
RUN rm -rf node_modules/zod node_modules/csv-parse node_modules/undici
COPY --from=build /app/worker-deps ./node_modules
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
COPY --from=build /app/dist ./dist
COPY --from=build /app/catalog ./catalog
COPY --from=build /app/data/journals/default-follows.json ./dist/data/journals/default-follows.json
COPY --from=build /app/data/journals/README.md ./data/journals/README.md
COPY --from=build /app/third_party ./third_party
USER node
EXPOSE 3000
CMD ["node", "server.js"]
