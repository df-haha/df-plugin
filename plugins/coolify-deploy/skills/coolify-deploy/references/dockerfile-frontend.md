# dockerfile-frontend.md — Frontend Dockerfile

> **何時讀**：撰寫或修改 frontend 的 `Dockerfile` 時。

> **範例棧**：本檔以 **Vite → nginx** 與 **Next.js → standalone** 為範例棧。其他前端棧自行替換，但**「規則（共通）」「不要做」的原則一律適用**。

兩條路線擇一：**Vite → nginx（SPA）**、**Next.js → standalone output**。

---

## Vite 路線（SPA + nginx）

```Dockerfile
# syntax=docker/dockerfile:1.7

# ============ Stage 1: builder ============
FROM node:22.13.0-bookworm-slim AS builder

WORKDIR /app

# NODE_ENV=development 確保 npm ci 安裝 devDependencies（含 tsc / vite 等 build 工具）。
# Coolify 會把 compose 的 environment: 自動展開成 --build-arg，若 compose 設了
# NODE_ENV=production，builder stage 預設會跳過 devDeps 導致 build 失敗（exit 127）。
# runtime stage（nginx）不跑 Node，不需改回；NODE_ENV=production 在 runtime 才設或不設均可。
ENV NODE_ENV=development

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ============ Stage 2: runtime（nginx）============
FROM nginx:1.27-alpine

ENV TZ=Asia/Taipei
RUN apk add --no-cache tzdata \
    && cp /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

`nginx.conf` SPA fallback：

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  gzip on;
  gzip_types text/plain text/css application/javascript application/json image/svg+xml;

  # SPA fallback：所有未命中改回 index.html
  location / {
    try_files $uri $uri/ /index.html;
  }

  # 靜態資源強 cache（Vite 帶 hash）
  location ~* \.(js|css|woff2?|ttf|eot|svg|png|jpg|jpeg|gif|ico)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  # index.html 不 cache（隨版本變）
  location = /index.html {
    add_header Cache-Control "no-store, must-revalidate";
  }
}
```

---

## Next.js 路線（standalone output）

`next.config.ts` 必設 `output: "standalone"`：

```ts
const nextConfig = { output: "standalone" };
export default nextConfig;
```

```Dockerfile
# syntax=docker/dockerfile:1.7

# ============ Stage 1: builder ============
FROM node:22.13.0-bookworm-slim AS builder

WORKDIR /app

# NODE_ENV=development 確保 npm ci 安裝 devDependencies（含 tsc / next 等 build 工具）。
# 理由同 Vite 路線：Coolify 把 compose environment: 自動展開成 --build-arg，
# 若 compose 有 NODE_ENV=production，npm ci 會 skip devDeps，next build 找不到指令（exit 127）。
# runtime stage 已設 NODE_ENV=production（第二個 FROM 區塊），不受此影響。
ENV NODE_ENV=development

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build

# ============ Stage 2: runtime ============
FROM node:22.13.0-bookworm-slim

ENV TZ=Asia/Taipei \
    NODE_ENV=production
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Next standalone：.next/standalone 自帶 minimal node_modules
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# 非 root
RUN useradd -r -u 1000 -m appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost:3000/ || exit 1

CMD ["node", "server.js"]
```

---

## 規則（共通）

- **multi-stage**：builder 帶 `node` + 完整 toolchain；runtime image 不帶 build tool。
- **base image 鎖 patch**：`node:22.13.0-bookworm-slim`、`nginx:1.27-alpine` 鎖到 patch，**禁** `latest`。
- **lock file 逐字安裝**：`npm ci`（**禁** `npm install`）。
- **TZ + tzdata**：runtime 設 `TZ=Asia/Taipei` 並裝 `tzdata`。
- **非 root**：Vite 路線 nginx 自身不需另開 user；Next 路線必 `useradd`。
- **build-time env 視為公開**：`VITE_*` / `NEXT_PUBLIC_*` 走 `ARG` + `ENV`，部署時由 Coolify build-time env 注入；**禁帶任何機密**（會編譯進 bundle，等於公開，見 `env-management.md`）。
- **EXPOSE**：給 compose 連用，**不**等於對外開 port。

## 不要做

- ❌ `npm install`（破壞版本鎖定）
- ❌ `COPY . .` 在裝依賴前（壞 layer cache）
- ❌ `development` mode 跑 production（`npm run dev` 帶 watcher / sourcemap，效能差且洩 source）
- ❌ build-time env 帶機密（`*_PUBLIC_*` / `VITE_*` 進 bundle）
- ❌ `latest` tag
- ❌ `.dockerignore` 沒排 `node_modules` / `.env*` / `.git`（會造成巨型 build context）
- ❌ **builder stage 繼承 `NODE_ENV=production`**：Coolify 會把 compose 的 `environment:` 自動展開成 `--build-arg`，若 compose 設了 `NODE_ENV=production`，`npm ci` / `yarn install` / `pnpm install` 都會**跳過 devDependencies**，導致 `tsc`、`vite`、`next` 等 build 工具找不到（exit code 127）。**務必在 builder stage 第一行加 `ENV NODE_ENV=development`** 覆寫，runtime stage 再設回 `production`。
