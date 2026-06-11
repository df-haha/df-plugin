---
name: coolify-logs
description: Use when reading Coolify build/runtime logs, debugging deploy failures, using the Coolify CLI (`coolify app logs`、`coolify deploy get`、`coolify deploy list`、`--format json`、`--context`), wiring a "deploy failed → auto-fetch log" monitor, or following the two-token discipline for `read:sensitive` API tokens. Triggers on phrases like "看 build log"、"部署失敗為什麼"、"coolify app logs"、"看不到 log"、"service 沒有 logs 指令"、"coolify 沒有 exec"、"撈 log monitor"、"read:sensitive token"、"one-shot 跑完看不到結果"。
---

# Coolify Logs

## Overview

Coolify 的 log 看似簡單（Web UI 一個 Logs 頁），但**從 CLI / API 安全撈 log** 有非顯而易見的設計限制：build log 預設讀不到、service 沒有 logs 子指令、整個 CLI 沒有 `exec`、Coolify「Success」綠燈不等於 one-shot service 成功。本 skill 把 CLI 對照表、build log 的 token 紀律、debug 流程一條鏈整理。

> **與姊妹 skills 的分工**：CLI 安裝、token 怎麼申請、permission 範本走 **coolify-setup**；DB / migrate / 角色相關的 log 解讀走 **coolify-db**。本 skill 只管 log 與 CLI 操作。

## CLI 三條「沒有」

寫 debug 工具 / wrapper 前先記住，否則會花時間找不存在的東西：

1. **`coolify` 沒有 `exec` 子指令**（也沒有 ssh / shell）。要進 container 跑指令只能 SSH 到 Coolify host 跑 `docker exec`，或走 Scheduled Task（見 coolify-deploy `references/deploy-and-rollback.md` 的長駐 idle container pattern）。
2. **`coolify service ...` 沒有 `logs` 子指令**（`service` 是 one-click services，logs 透過 web UI 或 host 上 docker logs）。`coolify app logs <uuid>` 只對 **application** 服務有效。
3. **`coolify app logs` 是 runtime log，不是 build log**。Application 沒在 running 時會回 `Application is not running`；build log 走 `coolify deploy get <uuid> --format json --show-sensitive` —— 而且需要 read:sensitive token（見 `references/deployment-logs.md`）。

## 讀哪份 reference

| 場景 | 讀這份 |
|------|--------|
| 想知道 `coolify app logs` / `coolify deploy list` / `coolify deploy get` 每個 flag 怎麼用、context 怎麼切、`--format json` 配 `jq` 的 pipeline、service 沒 logs 怎麼辦 | `references/cli-commands.md` |
| 部署失敗要撈 build log：為什麼一般 token 讀不到、`read:sensitive` token 的風險、two-token least-privilege 模型、`scripts/coolify-logs.py` 用法、「失敗自動撈 log」monitor 流程 | `references/deployment-logs.md` |
| One-shot service / migrate container 跑完看不到 log | 本檔下方「One-shot service 驗證」 + `references/cli-commands.md` |

## One-shot service 驗證

Coolify 的 deployment 綠燈反映的是 **healthcheck 結果** —— 對長駐 service 才有意義。one-shot service（`restart: "no"`）跑完就退、沒有 healthcheck，Coolify UI 仍可能標 success（因為 deploy 階段完成）。**不能信任綠燈代表 one-shot 跑成功**。

正確驗收：

1. **看 exit code**：在 host 跑 `docker ps -a --filter "name=*-migrate" --format "table {{.Names}}\t{{.Status}}"`，Status 應顯示 `Exited (0)`。Exit 非 0 → migrate 失敗，下游 app service（`depends_on service_completed_successfully`）會跟著不啟動。
2. **查實際變更**：跑了 schema migration → 用 Adminer / `psql` 查 `information_schema.tables` 看新表在不在。跑了資料 backfill → `SELECT COUNT(*) FROM ... WHERE new_field IS NOT NULL`。
3. **查 migrate container log**：`docker logs <container>` 看 stdout（host 上）；或設計 migrate service 寫 log 到 mount 進 named volume 的 file（見 coolify-db `references/one-shot-migration.md` 的 debug 撇步）。

`coolify app logs` 對 one-shot service 沒用 —— Application 已 exit，runtime log endpoint 直接回「not running」。

## Debug 流程（部署出問題）

```
1. 開 Coolify UI → Application Deployments 頁，看最新一筆狀態
       │
       ├─ Building... → 等
       ├─ Failed at build step → 看 build log（read:sensitive token + coolify-logs.py drop-hidden）
       ├─ Failed at healthcheck → 看 runtime log（coolify app logs <uuid>），對照 healthcheck 設定
       └─ Success but feature broken → runtime log + DB schema 雙路徑：
              ├─ runtime log 有 stack trace → 應用層 bug
              └─ runtime log 是「column does not exist」/ 401 / connection refused → 走 coolify-db skill 查 migration 是否上、roles/grants 是否齊
```

→ build log 看 `references/deployment-logs.md`、runtime log 看 `references/cli-commands.md`、DB 層走姊妹 skill **coolify-db**。

## scripts/coolify-logs.py 概覽

`read:sensitive` token 配本 skill 附的 `scripts/coolify-logs.py`，**預設丟掉 Coolify 標 `hidden=true` 的 entry**（那些含 `--build-arg`、build-time env 明文），再對殘留行跑 pattern 遮罩。輸出可安全貼給 AI / 寫進 transcript。完整用法（含 `--include-hidden` 的二次確認、failure-mode debug）見 `references/deployment-logs.md`。

腳本只用 Python 標準庫，無需 pip。

## token 紀律快速版

撈 build log 一定要 `read:sensitive` token（Coolify controller 在 token 無 `read:sensitive` 時 `makeHidden(['logs'])`）。但 `read:sensitive` 同時能讀整個 team 的 env 明文 + SSH 私鑰，是高風險憑證 —— 完整護欄（API Allowed IPs / 過期 / 拆 team / 用完即撤）走姊妹 skill **coolify-setup** `references/api-access.md`（write/deploy 另指 `api-access-write.md`），本 skill 只負責「怎麼用」。

**最小規矩**：

- token 從 env 讀（`COOLIFY_LOG_TOKEN`），**禁** 寫死 / 寫進 git / echo 到 transcript
- 一次性 debug 完立即在 Coolify Security → API Tokens 刪除
- monitor 流程「偵測失敗只用 `read`、撈 log 才升 `read:sensitive`」（見 `references/deployment-logs.md`）
