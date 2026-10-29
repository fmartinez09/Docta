FROM node:22.17.0-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci
COPY apps/web ./apps/web
ENV NEXT_TELEMETRY_DISABLED=1 DOCTA_DEV_MANAGED=1
RUN npm run build:web
RUN npm prune --omit=dev && rm -rf apps/web/.next/cache \
    && mkdir -p apps/web/.next/cache && chown node:node apps/web/.next/cache
USER node
CMD ["npm", "run", "start", "--workspace", "@docta/web", "--", "--hostname", "0.0.0.0", "--port", "3000"]
