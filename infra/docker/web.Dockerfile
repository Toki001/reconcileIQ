FROM node:22-bookworm-slim AS build
RUN npm install --global pnpm@12.10.1
ENV NEXT_TELEMETRY_DISABLED=1 RECONCILE_STANDALONE=1
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/generated/package.json packages/generated/package.json
RUN pnpm install --frozen-lockfile
COPY apps/web/ apps/web/
COPY packages/generated/ packages/generated/
RUN pnpm build

FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /app/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
