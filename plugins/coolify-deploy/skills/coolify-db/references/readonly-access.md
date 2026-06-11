# readonly-access.md — 給 AI / Claude 安全唯讀 DB 存取

> **何時讀**：要讓 Claude / AI / 內部分析工具能查 production DB 但不能寫時。

兩條可選路徑：**平台級**（dbhub MCP，AI 走 MCP 就能查、不改 app）或 **應用級**（後端開 `/admin/query` 三個 endpoint，外加 audit log）。本檔比較兩者 + 給出共通的「真正防線在 DB 層」實作。

---

## 兩方案決策表

| 維度 | 平台級：dbhub MCP | 應用級：`/admin/query` endpoint |
|------|-------------------|--------------------------------|
| 適合場景 | 任何專案（不挑語言）、想 AI 直接 SQL、不想動 app code | 已有 API 服務、要 audit log、要對外（公司內部）也提供分析功能 |
| AI 端怎麼連 | `claude mcp add --transport http dbhub https://...`，AI 自動可查 schema + 跑 SQL | AI 透過 app 的 REST endpoint，需要 access token |
| 組成 | DB 加 `claude_ro` reader role + dbhub container + 反代加 Basic Auth + claude.ai 註冊 MCP | `/admin/tables` `/admin/columns` `/admin/query` 三 endpoint + regex 第一層 + LIMIT wrap 第二層 + reader role 第三層 + audit log file rotate 90d |
| 上線成本 | 低（新加兩個 container：dbhub + nginx-proxy） | 中（要寫三 endpoint + audit + 測試） |
| 改 app code | ❌ 不用 | ✅ 要 |
| 多用戶 / RBAC | 走反代 Basic Auth | 走 app 自己的 auth |
| 對 AI prompt 注入的攻擊面 | 中（MCP 介面是 SQL；reader role + DB read-only 是兜底） | 中（同上，regex 是 UX 層快擋而不是安全層） |
| audit log | 看 PG `log_statement = 'all'` + reader role 過濾 | 應用層自己寫，含 user/timestamp/query/duration |

兩者**不互斥**：dbhub 給 AI / Claude 自助；`/admin/query` 給內部分析師、含 audit。同一個 reader role 兩邊共用即可。

---

## 真正的防線：在 DB 層強制 read-only

regex 阻擋 `INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER` 是 **UX 層**（給 AI 一個快速錯誤訊息、不浪費跑一遍），**不是安全邊界**。可繞的方式很多：

- 大小寫混搭（`inSerT`）
- comment 切斷（`IN/**/SERT`）
- multi-statement（`SELECT 1; INSERT ...` —— 部分 driver 支援）
- 透過 SELECT 呼叫帶副作用的 function（DML / sequence 寫的會被 PG `transaction_read_only` 擋；但 `pg_advisory_lock` / `pg_notify` / `dblink_exec` / FDW write 路徑**不算寫 WAL**，read-only transaction **不保證**擋住，見下方「副作用 function 攻擊面實測矩陣」）
- DO block 內 EXECUTE（`DO $$ BEGIN EXECUTE 'DELETE FROM ...'; END $$`）

**真正擋住寫的，是 DB 層四件事一起**：

1. **連線用 reader role**（GRANT 只有 SELECT，見 `db-roles-and-init.md`）。任何 INSERT/UPDATE/DELETE 直接 `permission denied`。
2. **`ALTER ROLE reader SET default_transaction_read_only = on`** —— 擋 DML / DDL / `nextval()` / `LOCK TABLE` 等**寫 WAL** 的副作用。⚠️ 不擋 advisory lock / `pg_notify` / dblink / SECURITY DEFINER function 路徑 → 配合下方矩陣 REVOKE EXECUTE。
3. **每筆 query 跑前顯式 `SET LOCAL default_transaction_read_only = on; SET LOCAL statement_timeout = '10s'; SET LOCAL idle_in_transaction_session_timeout = '5s';`** —— 第二、三層守備（10 秒上限避免 DoS、idle 上限避免占連線）。
4. **REVOKE 不必要的 function / schema**：reader 對 `public` 全收回；對 `pg_catalog` 只留 metadata 必要（schema introspection 用）。如果用到 PostGIS / extensions，逐個白名單。

regex 那層保留——擋大部分明顯案例、讓 LLM 收到 friendly error 不浪費 token；但安全認證上**不能說「我有 regex 所以安全」**。

### 副作用 function 攻擊面實測矩陣（⚠️ VOLATILE 不等於擋）

PG 的 `default_transaction_read_only = on` 擋的是「會寫 WAL」的操作（DML、DDL、sequence advancement、temp table 寫入）。它**不擋**所有副作用 —— `VOLATILE` 是 planner 用來決定能否預先求值的標記，**和 read-only 是兩個維度**。

以下矩陣是要在你自己的 PG 版本實測過才能信，**不要假設**：

| Function / 操作 | read-only transaction 會擋嗎 | 風險 |
|----------------|------------------------------|------|
| `INSERT / UPDATE / DELETE` | ✅ 擋（permission denied for read-only transaction） | 無 |
| `CREATE / ALTER / DROP` | ✅ 擋 | 無 |
| `nextval('seq')`（advance sequence） | ✅ 擋（PG 把 sequence advancement 算寫） | 無 |
| `LOCK TABLE` | ✅ 擋（SHARE 以上模式） | 無 |
| `txid_current()` / `pg_current_xact_id()` | ❌ 不擋（PG 設計上不算寫 WAL） | 低（只是讀） |
| `pg_advisory_lock(n)` / `pg_try_advisory_lock` | ❌ 不擋（session-level lock，不寫 WAL） | **中：DoS** —— AI 拿到後可洗 advisory lock 阻塞 writer pool |
| `pg_notify(channel, payload)` | ❌ 不擋（LISTEN/NOTIFY 不寫 WAL） | **中：情報外洩** —— 透過 channel 把資料推出去；若有 LISTEN 服務會被觸發 |
| `dblink_exec` / `postgres_fdw` write path | ⚠️ 視遠端 transaction 而定 | **高：橫向跳板** —— 走 FDW 寫遠端 DB 不受本機 read-only 限制 |
| `COPY TO PROGRAM`（superuser only） | ✅ 擋（且 reader 無權限） | 無（前提：reader 不是 superuser） |
| `SECURITY DEFINER` 自訂 function（owner 是 writer） | ❌ 不擋（function body 用 owner 權限執行） | **高：寫權繞道** —— 若 owner 有寫權，function body 可繞 reader 的 read-only |

→ 結論：reader role 必須 **REVOKE EXECUTE** 上面打 ❌ / ⚠️ 的 function；或在 endpoint 層用 SQL parser allowlist 擋 function call（regex 不夠，因為 function name 可用 `pg_advisory_lock` / `"pg_advisory_lock"` / `pg . advisory_lock` 等寫法繞）。

```sql
-- 對 reader role 收嚴 function 權限（在 03_grants.sh 或 migration 補）
REVOKE EXECUTE ON FUNCTION pg_advisory_lock(bigint) FROM reader;
REVOKE EXECUTE ON FUNCTION pg_try_advisory_lock(bigint) FROM reader;
REVOKE EXECUTE ON FUNCTION pg_notify(text, text) FROM reader;
-- 全部 public schema 內 SECURITY DEFINER function（手動列）
-- dblink / postgres_fdw extension 若不需要，直接 DROP EXTENSION 從根擋
```

對 reader role 把 `public.*` function 都 REVOKE 掉是 baseline（即使有人定義了奇怪的 SECURITY DEFINER function 也用不到）。所有上線後新加 extension / function 要重新 audit reader 權限 —— 寫進 migration checklist。

---

## 平台級實作：dbhub MCP

### compose 段

```yaml
dbhub:
  image: bytebase/dbhub:0.7.4              # pin patch
  environment:
    # DSN 串接由 dbhub 內部處理；變數逐項給，避免在文件留下 proto://user:pass@host 整串
    DBHUB_DRIVER: postgres
    DBHUB_HOST: db
    DBHUB_PORT: 5432
    DBHUB_USER: ${READER_USER}
    DBHUB_PASSWORD: ${READER_PASSWORD}
    DBHUB_DATABASE: ${POSTGRES_DB}
    DBHUB_SSLMODE: disable                 # compose 內網
    TZ: Asia/Taipei
  depends_on:
    db:
      condition: service_healthy
  expose:
    - "8080"
  restart: unless-stopped

dbhub-proxy:
  image: nginx:1.27-alpine
  environment:
    SERVICE_URL_DBHUBPROXY_80:
    TZ: Asia/Taipei
  volumes:
    - ./dbhub-proxy/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    - ./dbhub-proxy/.htpasswd:/etc/nginx/.htpasswd:ro    # 從 build context COPY；或走 init container
  expose:
    - "80"
  depends_on:
    - dbhub
  restart: unless-stopped
```

> dbhub 實際 env 變數名以該版本 image 文件為準（不同版本曾用 `JDBC_URL` / `DSN` / 拆欄三種寫法）。重點：**禁** 在文件 / yaml / commit 留下完整的 `proto://user:pass@host` 串 —— 用拆欄式由 image 內組，或在 init container 從 env 組好寫進 dbhub 設定檔。

`dbhub-proxy/nginx.conf`：

```nginx
server {
  listen 80;
  location / {
    auth_basic           "dbhub";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass           http://dbhub:8080;
    proxy_set_header     Host $host;
    proxy_set_header     X-Forwarded-For $remote_addr;
  }
}
```

`.htpasswd` 用 `htpasswd -B -c` 生成（bcrypt）。Basic Auth 是 baseline；若 Coolify 版本支援 SSO，把 dbhub 改用 OAuth proxy 更好。

### 在 Claude / AI 端註冊

```bash
# 先把 user:pass 編成 Basic Auth header，避免明文出現在 shell history
AUTH_B64=$(printf '%s' "${DBHUB_USER}:${DBHUB_PASS}" | base64)
claude mcp add --transport http dbhub https://dbhub.<your-domain>.com \
  --header "Authorization: Basic ${AUTH_B64}"
```

`DBHUB_USER` / `DBHUB_PASS` 從本機 `.env`（gitignore + chmod 600）讀，不寫死。

### 對 AI 的指令 prompt 框架

把「禁寫」寫進 system prompt 是給 LLM 看的 friendly hint，**不是安全層**。安全層是上面那四件 DB 設定。AI 可能照樣寫 INSERT，DB 會直接 `permission denied`、LLM 收到 error 自我修正。

---

## 應用級實作：`/admin/query` endpoint

### 三個 endpoint

| Endpoint | 用途 |
|----------|------|
| `GET /admin/tables` | 列 schema 內所有 table 名（從 `information_schema.tables` 查） |
| `GET /admin/columns?table=<name>` | 列指定 table 的欄位（`information_schema.columns`） |
| `POST /admin/query` body `{sql: "..."}` | 跑唯讀 SQL，回 rows + meta（columns / row_count / duration） |

### `/admin/query` 處理鏈

```python
# FastAPI 範例骨架
import re, time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin")

# 第一層：regex 阻擋（UX 層，給 LLM friendly error）
_WRITE_RE = re.compile(
    r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|"
    r"copy|vacuum|analyze|reindex|comment|do|call)\b",
    re.IGNORECASE,
)
_MULTI_STMT_RE = re.compile(r";\s*\S")  # 多 statement

class QueryReq(BaseModel):
    sql: str

@router.post("/query")
async def query(
    req: QueryReq,
    user = Depends(require_admin),               # app 自己的 auth
    db: AsyncSession = Depends(get_reader_db),   # ← 關鍵：reader role 的連線 pool
):
    sql = req.sql.strip()
    if _WRITE_RE.search(sql):
        raise HTTPException(400, "write operations not allowed")
    if _MULTI_STMT_RE.search(sql):
        raise HTTPException(400, "multiple statements not allowed")

    # 第二層：強制 LIMIT wrap
    if "limit" not in sql.lower():
        sql = f"SELECT * FROM ({sql.rstrip(';')}) _t LIMIT 1000"

    # 第三層：transaction-local read only + 嚴格 timeout
    start = time.monotonic()
    try:
        async with db.begin():
            await db.execute(text("SET LOCAL default_transaction_read_only = on"))
            await db.execute(text("SET LOCAL statement_timeout = '10s'"))
            await db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '5s'"))
            result = await db.execute(text(sql))
            rows = [dict(r._mapping) for r in result.fetchall()]
            columns = list(result.keys())
    except Exception as e:
        await audit_log(user, sql, error=str(e), duration_ms=int((time.monotonic()-start)*1000))
        raise HTTPException(400, f"query failed: {e}")

    duration_ms = int((time.monotonic() - start) * 1000)
    await audit_log(user, sql, row_count=len(rows), duration_ms=duration_ms)
    return {"columns": columns, "rows": rows, "row_count": len(rows), "duration_ms": duration_ms}
```

`get_reader_db` 是另一個 SQLAlchemy session factory，**連線字串用 reader role 的密碼**（與業務 writer pool 分離），這樣即使 endpoint code 有 bug，DB 端也擋。

### Audit log（90 天 rotate）

寫到 file（mount 進 named volume），daily rotate，90 天清理：

```python
import json, datetime, pathlib
LOG_DIR = pathlib.Path("/var/log/admin-query")

async def audit_log(user, sql, **extra):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fname = LOG_DIR / f"{datetime.date.today().isoformat()}.jsonl"
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user": user.email,
        "sql": sql,
        **extra,
    }
    with fname.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

不寫進 DB —— 避免 audit log 表自己被 query。清理走 entrypoint 啟動時跑的 cron / startup script：

```bash
find /var/log/admin-query -name "*.jsonl" -mtime +90 -delete
```

### 「已驗證勿重測」防護行為表

`/admin/query` 上線後最常被 AI 反覆觸發的測試：「我能不能用 `;` 多 statement？能不能用大小寫繞？」 —— 這些都是已知擋住的攻擊面，不需要每次都實測（每次都打到 audit log + DB error log 也是噪音）。

維護一份「行為表」on README / endpoint doc：

| 嘗試 | 預期結果 | 哪一層擋 |
|------|----------|----------|
| `INSERT INTO users ...` | 400 write not allowed | regex |
| `inSerT INTO ...` | 400 write not allowed | regex case-insensitive |
| `SELECT 1; INSERT ...` | 400 multiple statements | regex multi-statement |
| `SELECT pg_sleep(60)` | 10s 後 timeout | DB statement_timeout |
| `SELECT nextval('s')` | permission denied / read-only | DB role + transaction read-only |
| `SELECT * FROM users` 無 LIMIT | 自動 LIMIT 1000 | endpoint LIMIT wrap |
| 跨 schema `SELECT * FROM other_schema.t` | permission denied 若 schema 沒 GRANT USAGE | DB grants |

AI / 使用者看了表就知道哪些已驗證、別重複觸發。

---

## 共通：Coolify env 配置

平台級 / 應用級都需要在 Coolify Application Env 設：

| Key | 說明 |
|-----|------|
| `READER_USER` | `claude_ro` 或 `reader`，與 init scripts 一致 |
| `READER_PASSWORD` | 16+ 字元隨機，標 `is_secret` |
| `ADMIN_QUERY_AUDIT_DIR` | 應用級才需要，預設 `/var/log/admin-query` |

reader 的密碼 rotation 流程同一般 secret rotation：Coolify env 改 → redeploy → 確認 healthy → 撤舊。
