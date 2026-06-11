# observability.md — Log / Metric / 錯誤追蹤 / Healthcheck

> **何時讀**：設定觀測、接 Sentry、看 production log、或定義 healthcheck 端點時。

三層：**Log**（Coolify 內建 stdout 收集）、**Metric**（Coolify 內建 + 可選外接）、**錯誤追蹤 / APM**（Sentry 或同等）。

> 集中式 log（Seq / CLEF）的架構、SDK、first-run 密碼等細節是 **`seq.md` 的 SSOT**，本檔只引用、不重寫。

---

## Log

### 應用層輸出

- 一律 stdout / stderr（**禁**寫檔）— Coolify / docker 自動收。
- **結構化 JSON line** 格式（一行一筆 JSON）。
- 時戳統一 **ISO 8601 + offset**（例 `2026-05-07T14:23:00+08:00`，對齊 `TZ=Asia/Taipei`）。
- **禁** log 機密（token / cookie 全值 / connection string / 密碼）。

### Coolify 內查看

Coolify Application → Logs 頁，即時 stream + 搜尋。

### 長期保存 / 集中查詢

- 集中式查詢推薦 **Seq**（CLEF HTTP 推送）— 設定見 `seq.md`。
- 更長期保存 / 跨 service 集中：視需要外接 Loki + Grafana（自架）或商用 Datadog / Better Stack——**外接細節超出本 skill 範圍**。
- 對應外接 client 放專案自己的 client 層（如 `app/clients/<provider>/`），不在本 skill 規範。

## Metric

- **Coolify 內建**：Application → Resources 頁，CPU / Memory / Network 即時。
- **應用層 metric（可選）**：需要 RED metrics（Rate / Errors / Duration）→ 接 Prometheus + Grafana 或同等；`/metrics` endpoint **內網限定、禁對外開**。外接細節超出本 skill 範圍。

## 錯誤追蹤（Sentry）

production 建議啟用（staging 可接，development 不接）。

```python
# backend（FastAPI 範例）
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

if settings.SENTRY_DSN:                      # APP_ENV=production / staging 才設
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        integrations=[FastApiIntegration()],
        send_default_pii=False,              # PII 不外送
    )
```

```ts
// frontend 範例
import * as Sentry from "@sentry/react";

if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    environment: import.meta.env.VITE_APP_ENV,
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
  });
}
```

### 規則

- `SENTRY_DSN` / `VITE_SENTRY_DSN` 走 env 注入（DSN 視為機密，見 `env-management.md`）。
- **`send_default_pii=False`**：PII（個資）不外送 Sentry。
- 機密過濾：用 `before_send` hook 二次過濾 stack trace，避免機密隨 error 外送。

## Healthcheck 端點

`GET /api/v1/health`（FastAPI 範例）：

```python
@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    # 簡單 DB ping，不查業務邏輯
    await db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", version=settings.APP_VERSION)
```

- 用於 docker / Coolify healthcheck（對齊 `compose.md` / `dockerfile-backend.md`）。
- 回 200 + 簡單 JSON `{status: "ok"}`。
- **禁**重操作（查 N 個第三方）；健康檢查超過 1s 視為已不健康。

## Alert（可選）

關鍵 alert 設於 Sentry / Grafana / 其他：

- error rate 連 5 分 > 1% → page oncall
- response p95 連 5 分 > 1s → warning
- DB connection saturation > 80% → warning
- healthcheck fail → Coolify 自動回滾 + 通知
