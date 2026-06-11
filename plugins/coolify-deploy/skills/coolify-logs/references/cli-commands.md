# cli-commands.md — Coolify CLI 指令對照

> **何時讀**：要用 `coolify` CLI 撈 log、查部署狀態、切 context、或寫 wrapper script 時。

> 依 Coolify CLI **v1.6.2** 驗證（`~/.local/bin/coolify` 跑 `coolify <cmd> --help` 實機核對）。指令樹會隨版本變；不確定時跑 `coolify <cmd> --help` 確認，不要憑記憶猜 flag。

CLI 安裝、context / token 怎麼設見姊妹 skill **coolify-setup** `references/cli-install.md`。本檔只列指令對照。

---

## 指令樹（v1.6.2）

```
coolify
├── app          (= apps / application)   list / get / logs / start(=deploy) / restart / stop / env /
│                                          storage / deployments / previews / create / update / delete
├── database     (= db)                   list / get / start / stop / restart / env / storage / backup /
│                                          create / update / delete
├── service                               list / get / start / stop / restart / env / storage / create / delete
│                                          ⚠️ 沒有 logs / 沒有 exec
├── deploy                                list / get / batch / cancel / name / uuid
├── server                                list / get / add / validate / remove / domains
├── resource                              list
├── project                               list / get / create
├── context                               list / add / delete / get / set-default / set-token / update / use /
│                                          verify / version
├── github       (= gh / github-app)      list / get / create / update / delete / branches / repos
├── private-key  (= key / keys)           list / add / remove
├── teams        (= team)                 list / get / current / members
├── config                                印出 ~/.config/coolify/config.json 路徑
├── completion                            shell 補全
└── version                               CLI 版本（注意：是 subcommand，不是 --version flag）
```

---

## 全域 flags（每個指令都吃）

| Flag | 用途 | 例 |
|------|------|---|
| `--context <name>` | 暫時切 context（多 Coolify instance 時） | `coolify --context staging app list` |
| `--format <fmt>` | 輸出格式：`table` / `json` / `pretty`（預設 table） | `coolify deploy get <uuid> --format json` |
| `-s` / `--show-sensitive` | 顯示 sensitive 欄位（env value / log 等，**需 token 有 `read:sensitive` 權限**） | `coolify app env list <uuid> --show-sensitive` |
| `--token <t>` | 覆寫 context 內的 token | 一次性用 dev token：`coolify --token "$DEV_TOKEN" app list` |
| `--debug` | 開 debug 輸出（HTTP request 細節） | 排查 `--show-sensitive` 為何沒生效 |

`-s` 與 token 權限的關係：CLI 端的 `-s` 只控制「**該不該印**」，server 端是否願意給 sensitive 欄位看 token 的 scope。Token 沒 `read:sensitive` 即使加 `-s` 也只會收到 hidden 後的 response。

---

## 最常用：app + deploy

### 列出 application

```bash
coolify app list
coolify app list --format json | jq -r '.[] | "\(.uuid)\t\(.name)\t\(.status)"'
```

### 看 application 詳情

```bash
coolify app get <app-uuid>
coolify app get <app-uuid> --format json | jq '.fqdn'
```

### 看 runtime log

```bash
coolify app logs <app-uuid>              # 最近 100 行
coolify app logs <app-uuid> -n 500       # 最近 500 行
coolify app logs <app-uuid> -f           # 持續 follow（同 tail -f）
```

`-f` 不會自動退；用 `^C` 中斷。`-n` 是 `--lines` 簡寫。

⚠️ Container 沒 running 時會回 `Application is not running` —— 對 one-shot / 已 exit 的 service 這個 endpoint 無用。

### 觸發部署

```bash
coolify app start <app-uuid>                       # 觸發部署，走正常 queue
coolify app start <app-uuid> --instant-deploy      # 跳 queue 立即部署
coolify app start <app-uuid> --force               # 強制 rebuild（cache 不用）
```

`start` 是 `deploy` 的 alias —— 兩個指令完全等價。

### 部署相關

```bash
coolify deploy list                                # 目前在跑的所有部署
coolify deploy get <deployment-uuid>               # 單筆部署詳情
coolify deploy get <deployment-uuid> --format json # 含 build log（需 read:sensitive token）
coolify deploy cancel <deployment-uuid>            # 取消跑到一半的部署
coolify deploy name <resource-name>                # 用名字部署（不必查 uuid）
coolify deploy uuid <resource-uuid>                # 用 uuid 部署
coolify deploy batch <name1> <name2> ...           # 一次部署多個
```

### 看「某 app 的歷史部署清單」

CLI 沒有直接的 subcommand；走 API 直撈：

```bash
# 需要 read token（不需 sensitive）
TOKEN=$(coolify context get default --format json | jq -r '.token')
URL=$(coolify context get default --format json | jq -r '.url')

curl -sH "Authorization: Bearer $TOKEN" \
  "$URL/api/v1/deployments/applications/<app-uuid>?skip=0&take=10" | jq
```

或用 `coolify-logs.py --app <uuid>`，會自動抓最新一筆 deployment uuid 再去拿 log（見 `deployment-logs.md`）。

---

## env 變數

```bash
coolify app env list <app-uuid>                       # 列 env keys（value 預設遮）
coolify app env list <app-uuid> --show-sensitive      # 連 value 一起印（需 read:sensitive）
coolify app env create <app-uuid> --key X --value Y   # 新增（建議走 UI，CLI 方便寫 wrapper）
coolify app env update <app-uuid> --key X --value Y2  # 改
coolify app env delete <app-uuid> --key X             # 刪
```

⚠️ `--show-sensitive` 同 `-s`，在 transcript / log 內會印出完整 secret，**禁** 在 AI 對話 / shared shell 用。要看 value 時加 `2>&1 >/dev/null` 或直接到 Coolify Web UI 看（瀏覽器 session 不會留進 AI 對話）。

`coolify database env` / `coolify service env` 同形狀。

---

## context 切換（多 Coolify instance）

```bash
coolify context list                       # 列所有 context（顯示 default 與否）
coolify context list --format json         # 含 token（被遮罩）
coolify context use <name>                 # 切 default
coolify context verify                     # 測當前 context 能不能連得到 + token 對不對
coolify context version                    # 查 Coolify server 版本（用當前 context 連）
coolify context set-token <name> <token>   # 換 token（rotation 用）
coolify context add <name> --url <url> --token <token>     # 新增
```

每次 `coolify ...` 加 `--context <name>` 可一次性切換，不改 default。寫 wrapper script 時建議顯式帶 `--context`，不依賴 user 的 default state。

---

## `--format json` + `jq` pipeline 例

```bash
# 列出所有 app 的 (name, uuid, status, fqdn) 為一張表
coolify app list --format json \
  | jq -r '.[] | [.name, .uuid, .status, .fqdn] | @tsv'

# 過濾出某個 project 的 app
coolify app list --format json \
  | jq -r '.[] | select(.project_uuid == "<project-uuid>") | .name'

# 取最新一筆部署的 status
coolify deploy list --format json | jq -r '.[0] | "\(.id)\t\(.status)\t\(.application_name)"'
```

---

## service / database 的 log 怎麼看（CLI 沒提供）

`coolify service` **沒有** `logs` 子指令；`coolify database` 也沒有。要看 service / db 的 log：

1. **Coolify Web UI** → 點 service / db → Logs tab（最簡單）
2. **SSH 到 host** → `docker logs --tail 200 -f <container-name>`（container 名通常是 `<service-name>-<random-suffix>`，用 `docker ps` 找）
3. **service 內部寫 log 到 mount 的 file** → 在 compose 端規劃 `/var/log/<service>` 進 named volume，外部用 sidecar / Scheduled Task tail 出來

對 PostgreSQL，建議的長期方案是設 `log_destination = 'stderr'` 走 docker logs（Coolify Web UI 就看得到），或在 PG container 內 mount log volume。

---

## 寫 wrapper script 時的紀律

- **每個 `coolify` 呼叫顯式帶 `--context`**，不依賴 default
- **token 從 env 讀**（`COOLIFY_TOKEN`），不用 `--token "$T"` inline（避免進入 shell history / ps output）
- **`--format json`** 比 parse table 穩定 —— table 格式無 schema 保證
- **錯誤碼**：CLI 對 not-found / network error 回非 0 exit；wrapper 一律 `set -euo pipefail`
- **rate limiting**：Coolify API 沒明確 rate limit，但短時間大量 polling 會吃 Coolify host CPU；monitor 用 30s+ 間隔
- **未知子指令一律先 `coolify <cmd> --help` 確認 flag**，不要憑記憶猜（dev-workflow rule 10）
