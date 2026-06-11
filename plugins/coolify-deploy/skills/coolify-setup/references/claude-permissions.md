# claude-permissions.md — Claude Code 對 `coolify` CLI 的 permission 範本

> **何時讀**：要在專案 `.claude/settings.json` 加 deny / allow 規則，避免 Claude Code 不小心呼叫破壞性 `coolify` 指令時。

> ⚠️ **這是 guardrail 不是安全邊界**：`curl` 直打 API、Python script、絕對路徑（`/home/<user>/.local/bin/coolify`）跑 CLI、shell function alias 都可繞過 settings.json matcher。真正的權限邊界是 **API scope 本身**（見 `api-access.md` / `api-access-write.md`）—— deny 範本只是讓 AI 不小心叫到破壞性指令時被擋一次，給人類「停下來想一下」的機會。

依 Coolify CLI **v1.6.2** 的指令樹寫成（用 `coolify --help` / `coolify <cmd> --help` 對照）。新版有變動要重 review。

---

## 完整範本（寫入專案 `.claude/settings.json`）

```json
{
  "permissions": {
    "deny": [
      "Bash(coolify app delete*)",
      "Bash(coolify database delete*)",
      "Bash(coolify service delete*)",
      "Bash(coolify project delete*)",
      "Bash(coolify github delete*)",
      "Bash(coolify private-key remove*)",
      "Bash(coolify private-key add*)",
      "Bash(coolify server remove*)",
      "Bash(coolify server add*)",
      "Bash(coolify context delete*)",
      "Bash(coolify context add*)",
      "Bash(coolify context set-token*)",
      "Bash(coolify app env update*)",
      "Bash(coolify app env create*)",
      "Bash(coolify app env delete*)",
      "Bash(coolify database env update*)",
      "Bash(coolify database env create*)",
      "Bash(coolify database env delete*)",
      "Bash(coolify service env update*)",
      "Bash(coolify service env create*)",
      "Bash(coolify service env delete*)",
      "Bash(coolify app update*)",
      "Bash(coolify database update*)",
      "Bash(coolify app create*)",
      "Bash(coolify database create*)",
      "Bash(coolify service create*)",
      "Bash(coolify deploy cancel*)",
      "Bash(coolify deploy name*)",
      "Bash(coolify deploy uuid*)",
      "Bash(coolify deploy batch*)",
      "Bash(coolify app start*)",
      "Bash(coolify app deploy*)",
      "Bash(coolify database start*)",
      "Bash(coolify service start*)",
      "Bash(coolify app stop*)",
      "Bash(coolify database stop*)",
      "Bash(coolify service stop*)",
      "Bash(coolify app restart*)",
      "Bash(coolify database restart*)",
      "Bash(coolify service restart*)",
      "Bash(coolify app env list* --show-sensitive*)",
      "Bash(coolify app env list* -s*)",
      "Bash(coolify database env list* --show-sensitive*)",
      "Bash(coolify database env list* -s*)",
      "Bash(coolify service env list* --show-sensitive*)",
      "Bash(coolify service env list* -s*)",
      "Bash(coolify app env get*)",
      "Bash(coolify database env get*)",
      "Bash(coolify service env get*)"
    ],
    "allow": [
      "Bash(coolify version)",
      "Bash(coolify config)",
      "Bash(coolify context list*)",
      "Bash(coolify context get*)",
      "Bash(coolify context verify*)",
      "Bash(coolify context version*)",
      "Bash(coolify context use*)",
      "Bash(coolify app list*)",
      "Bash(coolify app get*)",
      "Bash(coolify app logs*)",
      "Bash(coolify app deployments*)",
      "Bash(coolify app env list*)",
      "Bash(coolify app storage list*)",
      "Bash(coolify app previews*)",
      "Bash(coolify database list*)",
      "Bash(coolify database get*)",
      "Bash(coolify database env list*)",
      "Bash(coolify database storage list*)",
      "Bash(coolify database backup*)",
      "Bash(coolify service list*)",
      "Bash(coolify service get*)",
      "Bash(coolify service env list*)",
      "Bash(coolify service storage list*)",
      "Bash(coolify deploy list*)",
      "Bash(coolify deploy get*)",
      "Bash(coolify server list*)",
      "Bash(coolify server get*)",
      "Bash(coolify server domains*)",
      "Bash(coolify resource list*)",
      "Bash(coolify project list*)",
      "Bash(coolify project get*)",
      "Bash(coolify github list*)",
      "Bash(coolify github get*)",
      "Bash(coolify github branches*)",
      "Bash(coolify github repos*)",
      "Bash(coolify private-key list*)",
      "Bash(coolify teams list*)",
      "Bash(coolify teams get*)",
      "Bash(coolify teams current*)",
      "Bash(coolify teams members*)"
    ]
  }
}
```

---

## 設計決定

### 為什麼用白名單 allow 而不是 wildcard

`Bash(coolify * get*)` 看似乾淨 —— 但**過寬**：sensitive token 下，`coolify app env get` 加 `-s` 即印出 env 明文（DB 密碼、JWT secret）。白名單明確列舉「無副作用的列舉與查看」（`list` / `get` / `logs` / `verify` 等）。

### 為什麼 `env list` 在 allow 但要再加 `-s` / `--show-sensitive` / `env get` 進 deny

`env list` 不加 flag 預設不印 value（只列 key），列出 key 是合理的 debug 動作 → 留在 allow。但 `coolify app env list <uuid> --show-sensitive`（或短 flag `-s`）會印出明文 value，若該 context 的 token 剛好有 `read:sensitive` scope 即 secret exfiltration → 必須明確 deny。`env get` 也走 sensitive 路徑同樣 deny。三個 service 類別（app / database / service）都各有 deny 規則。

**deny 優先於 allow**：matcher 規則「deny match 就擋」（見下方優先序段）→ 即使 `Bash(coolify app env list*)` 在 allow，跑 `coolify app env list <uuid> -s` 會被 `Bash(coolify app env list* -s*)` 的 deny 先擋下。

### 為什麼 stop / restart / start / deploy / cancel 進 deny

`stop` / `restart` 不是 destructive（不會刪資料）但會 **造成服務中斷**。Claude Code 在追 bug 時可能誤判「重啟一下試試」，導致 production downtime。

`start` / `deploy name` / `deploy uuid` / `deploy batch` / `app start`（其實是 `deploy` 的 alias，見 coolify-logs `references/cli-commands.md`）會**觸發新部署**，等同於 push 一次新版上 production —— AI 在 debug / experiment 時自動觸發 deploy 會踩到 maintenance window、可能上錯 commit。explicit deny 強制 user 手動跑或在 Web UI 操作。

`deploy cancel` 取消跑到一半的部署 → 可能造成「部分 service 已切流量、另一半還沒」的混合狀態 → 一律走 Web UI 由人類確認。

### 為什麼 env list 在 allow 但 env update/create/delete 在 deny

`env list` 預設不印 value（要加 `--show-sensitive` 才印，且需 sensitive scope），列出 key 是合理的 debug 動作。改 env 則是寫操作 → deny。

### 為什麼 private-key add/remove 在 deny

private-key 是 Coolify 用來 SSH 進管理的 server 的鑰匙。改動會直接影響 Coolify 能不能管 server。即便有 write scope 也不該透過 CLI 自動化。

### 為什麼 context add / set-token 在 deny

加 context / 改 token 等同於「裝新的存取入口」，AI 自動跑會繞過人類對 token 來源的審視。改 context 是人類操作。

---

## Deny / allow 的優先序

Claude Code 的 matcher 規則（依官方文件）：
1. **deny 優先**：deny match 就擋，不再看 allow
2. **allow 較具體者勝**：兩條 allow 都 match 時，prefix 較長者優先
3. **無 match → 走 ask / default**：根據 user 設定

實務驗證：`Bash(coolify app delete*)` 在 deny，`Bash(coolify app get*)` 在 allow → `coolify app delete <uuid>` 被擋、`coolify app get <uuid>` 通過。✓

---

## 實測 matcher

寫入範本後在該專案目錄跑：

```bash
# 1. 確認 settings.json 合法 JSON
jq . .claude/settings.json
# 2. 確認 permissions.deny / permissions.allow 結構正確
jq '.permissions | keys' .claude/settings.json
# 預期：[ "allow", "deny" ]
```

然後在 Claude Code 互動視窗試（**不會真的執行**，user 端會看到 deny prompt）：

| 指令 | 預期 |
|------|------|
| `coolify app list` | 直接通過（在 allow） |
| `coolify app get <uuid>` | 直接通過 |
| `coolify app delete <uuid>` | Deny prompt，user 必須手動允許 |
| `coolify app stop <uuid>` | Deny prompt |
| `coolify app env update --key X --value Y <uuid>` | Deny prompt |
| `coolify deploy list` | 通過 |
| `coolify deploy get <uuid>` | 通過 |
| `coolify deploy cancel <uuid>` | Deny prompt |
| `coolify context add ...` | Deny prompt |
| `coolify private-key remove ...` | Deny prompt |

若 matcher 沒有如預期表現，檢查 settings.json 的 deny prefix 是否與你跑的指令 prefix exact match。matcher 是 **prefix match**，不是 regex —— `Bash(coolify app delete*)` 會 match `coolify app delete <anything>`，但不會 match `coolify app delete-all`（少個空格） / `coolify  app delete`（雙空白）。

---

## 範本的覆蓋面：deny 為何要列這麼多

CLI v1.6.2 共有 9 個 top-level subcommand × 各自 ~10 個動作 ≈ 100+ 個指令組合。本範本 deny 約 30 條 + allow 約 40 條，覆蓋：

- **所有寫操作**：create / update / delete / add / remove / stop / restart / cancel / set-token
- **所有對 server / private-key / context 的改動**（即便 read 看起來無害，但這層改動是改連線目標，AI 自動跑風險高）
- **所有 env 改動**

剩下沒列的（如 `coolify completion`、`coolify --debug` 之類）會走 settings.json 的預設行為（ask / allow，看 user 全域設定）。

---

## 與 user-level / global 設定的關係

本範本是 **project-level**（寫進 `.claude/settings.json`）。user-level（`~/.claude/settings.json`）若已有 `coolify *` 的 allow 規則，**user-level 較具體者勝**。Coolify deploy 是 project-scoped 行為，**寫在 project-level 比較合理**：

- 不同專案可能對應不同 Coolify team
- 不同專案的 deploy 紀律不同（production vs experiment）

如果要跨專案共用，把整段搬到 `~/.claude/settings.json` 也行，但記得：跨專案就跨 team，deny 清單可能不夠（如：實驗專案允許 stop 但 production 不允許 → 寫進 project-level）。

---

## 維護紀律

1. **CLI 升級後 review**：跑 `coolify --help` 對 deny 清單對照新版有沒有新 subcommand。新加的 destructive 動作要補進 deny。
2. **plugin v2.0 重 init 後驗 matcher**：把本範本套進測試專案、跑上面實測表確認 8/8 通過。
3. **發現新的繞過**（如 alias / function 繞過 prefix match）→ 補進「真正的權限邊界是 API scope」段落，提醒使用者。
