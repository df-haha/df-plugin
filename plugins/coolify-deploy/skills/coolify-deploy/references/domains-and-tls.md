# domains-and-tls.md — 自訂網域 / TLS / CORS / Cookie / Preview env

> **何時讀**：綁自訂網域、設 TLS、調 CORS / cookie domain、或開 preview env 時。

Coolify 內建 Caddy / Traefik 反向代理 + Let's Encrypt 自動 TLS。

> **與 SERVICE_URL magic env 的關係**：自訂網域與 `SERVICE_URL_*`（見 `service-url.md`）是**兩種正交、可並存**的對外曝露機制。需要自己的網域（如 `api.<company>.com`）→ 用本檔的自訂網域綁定；只要 Coolify 給個自動網址 → 用 magic env。

---

## Domain 綁定

Coolify Application → Configuration → **Domains**：

```
https://api.<company>.com           # backend
https://app.<company>.com           # frontend
https://staging-api.<company>.com   # staging backend
https://staging-app.<company>.com   # staging frontend
```

DNS：

- A record / CNAME 指向 Coolify 機器 IP。
- 多 app 同 IP → 走 SNI（Coolify 反向代理依 host header 路由）。

## TLS

- Coolify 自動跑 Let's Encrypt（ACME），**不**需手動裝憑證。
- 啟用條件：DNS 已解析到 Coolify IP、80 / 443 port 對外開、Let's Encrypt rate limit 內（每域名 7 天 5 次）。
- 憑證自動續期（到期前 30 天）。

### 規則

- production 必走 HTTPS，**禁** `http://` 對外。
- **TLS 版本 ≥ 1.2**。
- **HSTS**：Coolify 反代預設啟用，**禁**關（對齊 OWASP）。

## CORS / origins 配對

domain 設定後同步 backend `CORS_ORIGINS`：

```
CORS_ORIGINS=["https://app.<company>.com"]
```

**禁** `["*"]`；每環境獨立配置。

## Cookie domain

httpOnly JWT cookie 的 `domain` 欄：

```python
response.set_cookie(
    "session",
    token,
    httponly=True,
    secure=True,
    samesite="lax",
    domain=".<company>.com",   # 跨子網域（api ↔ app）共享
)
```

- 跨子網域 cookie 走 `domain=".<company>.com"`（前置 dot）。
- 不跨子網域 → 不設 `domain`（預設只當前 host）。
- 前端認證相關的 `VITE_*` / `NEXT_PUBLIC_*` 一律視為公開、**禁**帶機密（見 `env-management.md`）。

## Preview env（每 PR 一個臨時環境，可選）

Coolify 支援 preview deployment（若版本支援）：

- 每開 PR 自動部署 `pr-NNN.<company>.com`，merge / close PR 自動回收。
- 走獨立 DB（若需要）— 啟動成本高，看專案決定。

啟用前評估：

- [ ] 機器資源夠（每 preview 占 1 個 backend + 1 個 frontend container）
- [ ] preview env 用 sandbox / mock 第三方（**禁**接 production 第三方）
- [ ] DNS wildcard（`*.<company>.com` → Coolify IP）
- [ ] Let's Encrypt rate limit 不撞牆（wildcard cert 較友善）

## 不要做

- ❌ 自簽憑證對外（瀏覽器警告 + HSTS 永久污染）
- ❌ HTTP redirect 到 HTTPS 之外的事（僅 301 → HTTPS）
- ❌ 同一 domain 同時綁多個 Coolify app（衝突）
- ❌ 關 HSTS
- ❌ 用 `*` CORS / 不限 cookie domain（攻擊面）
