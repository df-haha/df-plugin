# dockerfile-backend.md — Backend Dockerfile

> **何時讀**：撰寫或修改 backend 的 `Dockerfile` 時。

> **範例棧**：本檔以 **FastAPI + uv（Python）** 為範例棧。其他棧（Node/Express、Go、Rust…）自行替換 base image、依賴安裝、啟動指令，但**下方「規則」「不要做」段落的原則（multi-stage、鎖版、非 root、TZ、機密不進 image、HEALTHCHECK）一律適用**。

要點：multi-stage、lock file 逐字安裝、非 root、含 `tzdata` 並設 `TZ`、內建 HEALTHCHECK。

---

## 模板（FastAPI + uv，範例棧）

```Dockerfile
# syntax=docker/dockerfile:1.7

# ============ Stage 1: builder ============
FROM python:3.14.0-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

# uv 鎖到 patch，禁 latest
COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /usr/local/bin/uv

WORKDIR /app

# 先裝依賴（layer cache 友善）
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# 再放 source（精準 COPY，不要 COPY . .）
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

# ============ Stage 2: runtime ============
FROM python:3.14.0-slim

ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# tzdata（時區）+ curl（供 healthcheck）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

# 非 root
RUN useradd -r -u 1000 -m appuser
WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 規則

- **multi-stage**：builder 用完整工具裝依賴，runtime image 不帶 build tool。
- **lock file 逐字安裝**：`uv sync --frozen`（`--no-dev` 跳過開發依賴）；對應 Node 棧用 `npm ci`。
- **base image 鎖 patch**：`python:3.14.0-slim`、`ghcr.io/astral-sh/uv:0.5.18` 都鎖到 patch literal，**禁** `latest`。需要走變數時用 build `ARG`。
- **TZ + tzdata**：runtime 設 `ENV TZ=Asia/Taipei` 並裝 `tzdata`、link `/etc/localtime`，確保 log 時戳對齊（ISO 8601 + offset）。
- **非 root**：`useradd -r -u 1000`，**禁**以 root 跑 app。
- **HEALTHCHECK**：image 內建（`/api/v1/health` 回 200），Coolify / compose 都認。
- **EXPOSE 8000**：給 compose 連用，**不**等於對外開 port（對外走 Coolify 反代）。

## migration 啟動時跑（與 deploy 連動）

DB migration **禁**在 build 階段跑，改在容器**啟動**時跑（entrypoint）；細節與 zero-downtime 拆 task 見 `deploy-and-rollback.md` 的 Migration 段。

## 不要做

- ❌ `COPY . .`（會帶進 `.env` / 測試 / `.git`，改精準 `COPY app ./app`）
- ❌ `ENV JWT_SECRET_KEY=...`（機密**禁**寫進 image，走 runtime env，見 `env-management.md`）
- ❌ `USER root`（攻擊面 + 違反最小權限）
- ❌ build 階段執行 `alembic upgrade head`（部署啟動時跑，見 `deploy-and-rollback.md`）
- ❌ `latest` tag

## `.dockerignore`

```
.venv
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.env*
!.env.*.example
.git
.github
tests/
docs/
```

## 多 worker / gunicorn（簡述）

預設 `uvicorn` 單 process。需要多 worker → 走 gunicorn + uvicorn worker：

```Dockerfile
CMD ["gunicorn", "app.main:app", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000"]
```

worker 數依 CPU 配額決定，`(2 × CPU) + 1` 為起跑線；更細的效能調校超出本 skill 範圍。
