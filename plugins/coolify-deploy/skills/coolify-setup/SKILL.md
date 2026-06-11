---
name: coolify-setup
description: Use when first installing the Coolify CLI on a new machine, configuring `~/.config/coolify/config.json` and contexts, applying for Coolify API tokens (read / read:sensitive / deploy / write / root), or writing a Claude Code permission template that denies destructive `coolify` commands and allows specific read-only subcommands. Triggers on phrases like "第一次裝 coolify CLI"、"coolify 怎麼設定"、"context 怎麼加"、"申請 token"、"read:sensitive 要不要勾"、"deny coolify"、"settings.json 怎麼擋 coolify delete"、"AI 不要刪我 app"。
---

# Coolify Setup

## Overview

第一次在一台機器上接 Coolify 要做三件事：**裝 CLI + 設 context、申請對的 token、給 Claude Code 設 permission 範本**。三件事互相關聯：token scope 決定 CLI 能做什麼，permission 範本決定 Claude Code 在 user 不在時能跑哪些 CLI。本 skill 把三條鏈整成一條線。

> **與姊妹 skills 的分工**：CLI 指令對照走 **coolify-logs** `references/cli-commands.md`；build log / token 使用紀律走 **coolify-logs** `references/deployment-logs.md`；部署 / DB / 安全紀律走 **coolify-deploy** / **coolify-db**。本 skill 只管首次設定。

## 三件事的順序

```
1. 裝 CLI                → references/cli-install.md
2. 申請 read token      → references/api-access.md（預設只引導這把）
       │
       ├─ 不寫 deploy / 不改 env → 結束
       └─ 真的要 deploy / 改 env / 改 schema
              ↓
              讀 references/api-access-write.md，申請第二把 write/deploy token
3. 寫 Claude permission  → references/claude-permissions.md
```

**預設不主動引導 write/deploy token**。理由：write token 一旦外洩，AI / 攻擊者可以刪 application、刪 database、改 env、強迫部署任意 commit —— 比 read:sensitive 還危險。除非使用者明確說「我要自動化部署」/「Claude 要幫我改 env」，否則只裝 read 那把。

## 讀哪份 reference

| 場景 | 讀這份 |
|------|--------|
| 還沒有 `~/.local/bin/coolify` / 要升級 CLI / 要加第二個 instance（zerozero + staging）/ 想知道 `~/.config/coolify/config.json` 長怎樣、權限要多少 | `references/cli-install.md` |
| 第一次申請 token：為什麼建議 read+sensitive、有什麼風險、必須加的護欄（API Allowed IPs / 過期 / 拆 team / 用完即撤）、不需要讀 build log 時的降級選項（純 read） | `references/api-access.md` |
| **使用者明確要求** Claude 自動化部署 / 改 env / 動 schema 時才讀：write/deploy token 申請、額外風險、單獨護欄 | `references/api-access-write.md` |
| 要在專案 `.claude/settings.json` 加一層 guardrail，擋掉 Claude Code 不小心呼叫 `coolify app delete` / `coolify env update` / `coolify server` 等破壞性指令 | `references/claude-permissions.md` |

## 安全紀律（永遠遵守）

1. **不 `cat` token 進 Claude 對話**。token 從 `.env`（gitignore + `chmod 600`）讀，或用 `pass` / `1Password CLI` 等密鑰管理工具；shell 用 `read -s -p`。
2. **`~/.config/coolify/config.json` 權限必 600**（CLI 自動建為 600，但要驗證）。CLI 把 token 明文存在這個檔 —— 等同 root 級存取，任何讀得到此檔的進程都拿得到所有 context 的 token。
3. **token 進過 git / transcript / chat / log 視為已洩漏** → 立即在 Coolify Security → API Tokens revoke 並換一把，不只是 `git rm` / 刪檔。
4. **設 token 過期** —— 別選「無期限」。Coolify 支援 7/30/60/90 天、1 年、無期限；預設選**和你最近一次 audit 重設 cycle 對齊**的長度（多數團隊 90 天）。
5. **拆 team**：production 與 staging 放不同 Coolify team，每把 token 的爆炸半徑只在該 team。
6. **permission 範本是 guardrail 不是安全邊界**：`curl` 直打 API、`python` script、絕對路徑跑 CLI 都能繞過 settings.json 的 matcher。真正的權限邊界是 **token scope 本身** —— deny 範本只是讓 AI 不小心叫到破壞性指令時被擋一次，提供額外的「停下來想一下」機會。

## 驗收（裝完 CLI + token + permission）

跑一遍下列指令：

```bash
# 1. CLI 在 PATH 內
which coolify       # /home/haha/.local/bin/coolify
coolify version     # 印出 server 版本（透過當前 context 連）

# 2. context 設好
coolify context list       # 至少有一個 context，default 標 ✓
coolify context verify     # 印「Connection successful」之類訊息

# 3. read token 工作
coolify app list           # 印出 application 表（即使是空）
coolify app list -s        # 即使加 -s 也只該看到 hidden 標記 / 不該印機密（read token 不該看到）

# 4. permission 範本生效（在裝了範本的專案目錄內）
# 跑下面這行 Claude Code 應跳 deny 警告（user 端視覺）：
echo "coolify app delete <dummy-uuid>"   # 不會真的執行，只是檢查 matcher
```

最後一步驗 deny matcher 的方式見 `references/claude-permissions.md` 的「實測 matcher」段。
