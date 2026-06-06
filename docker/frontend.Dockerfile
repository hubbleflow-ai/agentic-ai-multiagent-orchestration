# Next.js production build for the dashboard.
# Build context is ./frontend (set in docker-compose), so paths are relative
# to the frontend directory.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# Use `npm install` (not `npm ci`) so the build succeeds even when no
# lockfile is committed yet. Once we commit a package-lock.json, switch
# to `npm ci` for reproducible builds.
RUN npm install --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000
CMD ["npm", "start"]
