# api-access.md — Coolify API 認證申請（read / read:sensitive）

> **何時讀**：第一次接 Coolify、要申請 API 認證給 CLI 用、要讓 Claude / monitor 讀 build log 時。
> **write / deploy / root 級** 認證走 `api-access-write.md`，本檔不引導。

---

## Coolify API 權限模型

Coolify 官方 API 認證有 5 個 scope：

| Scope | 能做什麼 | 用途 |
|-------|----------|------|
| `read` | 讀 application / service / database / deployment / context 等**非 sensitive** 欄位 | 列清單、看 status、跑 deploy(透過 deploy scope) |
| `read:sensitive` | `read` + 加上：env values 明文、build log（`application_deployment_queues.logs`）、SSH 私鑰（`/security/keys`）、其他 hidden 欄位 | **唯一**能撈 build log 的權限 |
| `write` | 改 application 設定 / env / domains 等（但**不含** delete / 部署） | 自動化改 env 用 |
| `deploy` | 觸發部署（`coolify app start`）、cancel 部署 | CI / 自動化部署 |
| `root` | 全部 + 刪除 / server 管理 / private key | 不建議發 |

**重要設計**：`read:sensitive` 本身**已包含 `read`**。建立時單勾 `read:sensitive` 即可，不要再加 `read`。

**Scope 是 team-scoped**：只能存取「建立時所在 team」的資源，但該 team 內所有專案都讀得到 → 把 production 與 staging 拆成不同 team，爆炸半徑就只剩該 team。

---

## 預設引導：申請一把 `read:sensitive`

99% 的使用情境是「我要讓 Claude / CLI 看 build log 找出為什麼部署失敗」。**這需要 `read:sensitive`** —— 一般 `read` 在打 `/api/v1/deployments/{uuid}` 時 controller 會 `makeHidden(['logs'])` 把 log 整個拿掉（symptom：`coolify deploy get <uuid>` 印不出 logs 欄）。

申請步驟：

1. **登入 Coolify Web UI** → 右上角頭像 → Security → API
2. **選對的 team**：production 用 production team，不要混 staging
3. **Name**：`<your-name>-read-sensitive-2026Q2`（含日期方便輪替）
4. **Scope**：勾 `read:sensitive`，**不要** 再勾 `read`（已含）
5. **Expiration**：選 **90 天**（不要選「No expiration」）
6. **API Allowed IPs**（Settings → Advanced，全域 allowlist 非 per-scope）：加上跑 monitor / Claude 那台機器的 IP / VPN range
7. **建好** → **立即複製字串**（只顯示一次）→ 寫進 `~/.<project>/.env` 或密鑰管理工具

---

## ⚠️ `read:sensitive` 是高風險憑證 —— 風險與護欄

這把 scope 能讀整個 team 所有專案的：

- **env values 明文**（DB 密碼、JWT secret、第三方 API key、SMTP password、Sentry DSN）
- **SSH 私鑰明文**（Coolify 管理的 server SSH key，存在 `/security/keys`）
- **build log**（含 `--build-arg` / build-time env 的 hidden entry，雖預設 hidden 但 `--show-sensitive` flag + 對應 endpoint 拿得到）

沒有 `write` 不代表低風險 —— **拿到 DB 密碼即可繞過 Coolify 直接連 DB，拿到 SSH 私鑰即可 SSH 進 host**。

護欄（**必須**全部做）：

1. **API Allowed IPs**（最強的一條）：只允許跑 monitor / 寫 wrapper 的那台機器 IP。外洩到別的來源也用不了。Coolify Allowed IPs 是 **全域**，建議找 Settings → Advanced 設好，然後**所有** 認證都受惠。
2. **過期**：選有限期（90 天起跳），別選「No expiration」。在日曆排輪替提醒（半年提早 review 一次）。
3. **存放**：字串進**環境變數 / `.env` 檔**，該檔 **`.gitignore` + `chmod 600`**；別放在 shell history / commit message / chat / log。
4. **拆 team**：production 專案放獨立 team，爆炸半徑就只剩該 team。
5. **用完即撤**：一次性 debug 完，到 Coolify Security → API 把它刪掉。下次要時再生一把。
6. **CI（GitHub Actions / GitLab CI / 自架 runner）使用紀律**：CI secret store **不是安全邊界**。即使放進 `secrets.COOLIFY_LOG_TOKEN`，仍有多條洩漏路徑：
   - `set -x` / `bash -x` 把 expanded 命令印進 job log（mask 對部分字串可能漏）
   - workflow step 的 `outputs` / `echo "::set-output"` 把值寫進 step log（mask bypass 是已知 issue）
   - third-party action 的 print / debug log（mask 只對主 job 生效，subprocess 不一定）
   - 失敗的 job 把 env / process tree dump 進 artifact / cache
   - pull-request-from-fork workflow 的 PR title / commit message 可能 inject 觸發 echo

   → 如非必要，**不要在 CI 用 `read:sensitive`**。CI 端的部署走 Coolify **webhook**（內建、不需 token；見 `api-access-write.md` 的 deploy scope 段）。真要在 CI 撈 build log：用最短過期（≤ 24 hr）、`permissions: read-all` 限縮 job scope、`environment:` 加 protection rule 強制人工 review、跑完最後一步 revoke（透過 Coolify API 自動 revoke）。

   **GitHub mask 不是安全邊界** —— 它只是「方便看 log 不要刺眼」的 UI 功能。把 token 給 CI 前，先確認 workflow 沒有任何 `${{ secrets.X }}` 流入會被 print 的地方。

> **一旦進過任何明文檔 / 對話 / log，視為已洩漏 → revoke + 換新，不要只是刪檔。**

---

## 護欄落實 checklist（裝完跑一遍）

```bash
# 1. 存環境變數，不是字面
grep -r 'COOLIFY_LOG_TOKEN' ~/.bashrc ~/.zshrc ~/.profile 2>/dev/null
# 預期：沒有字面值；只看到 `source ~/.env` 之類的 indirection

# 2. .env 權限正確
stat -c '%a %n' ~/.env-coolify       # 600
grep -E 'COOLIFY.*=' ~/.env-coolify   # 確認 key 在這
git check-ignore ~/.env-coolify       # 已被 gitignore

# 3. 沒在 git 任一 commit
cd <your-repo>
git log --all -p -S 'COOLIFY' | head -50  # 不該看到字面

# 4. Coolify Security 頁有正確 expiration
# Web UI → Security → API → 確認 expires_at 不為 null
```

---

## 降級選項：純 `read`（不需讀 build log 時）

如果你的用途**只是**：

- 列 app / status
- 觸發部署（搭配 `deploy` scope 另一把）
- 寫 monitor 看 deploy 是否 healthy（不撈失敗 log）

那麼一把純 `read` 就夠，**不需要** `read:sensitive`。風險顯著降低（讀不到 env 明文 / SSH 私鑰 / build log）。

但實務上「監測部署失敗」總會想知道「為什麼失敗」，遲早要看 log → 不如一開始就分兩把：

| 用途 | Scope |
|-----|------|
| 日常 | `read` + `deploy` |
| 讀 log | `read:sensitive`（只在部署失敗要撈 log 時動） |

monitor 流程：日常用低權限那把輪詢；只有 `status == failed` 時才升 `read:sensitive` 撈 log（見 coolify-logs `references/deployment-logs.md`）。

---

## 與 CLI / scripts 配合

```bash
# 環境變數命名建議（與 coolify-logs.py 預設一致）
export COOLIFY_URL='https://coolify.example.com'
export COOLIFY_TOKEN='<read + deploy>'        # 日常
export COOLIFY_LOG_TOKEN='<read:sensitive>'   # 撈 log 用，僅在需要時 source

# coolify CLI 預設讀 ~/.config/coolify/config.json 內的 context；
# 想一次性用 dev / log 那把時用 --token：
coolify --token "$COOLIFY_LOG_TOKEN" deploy get <uuid> --format json | jq .

# coolify-logs.py 從 COOLIFY_LOG_TOKEN 讀
python scripts/coolify-logs.py --app <app-uuid>
```

`--token` 覆寫 context，但 inline 在 command 內會進 shell history → 用 env 變數 reference（`--token "$VAR"`），不要寫字面。

---

## 我需要 write / deploy / root 嗎？

| 想做 | Scope |
|-----|------|
| 看 app 清單 / status / runtime log | `read` |
| 看 build log（部署失敗 debug） | `read:sensitive` |
| 觸發部署（CI / 自動化） | `read` + `deploy` |
| 改 env / 改 domains / 加 app | `write` —— **走 `api-access-write.md` 申請** |
| 刪 app / 刪 db / 改 server / 改 private-key | `root` —— **不建議發**，這些操作走 Web UI 手動 + 雙確認 |

90% 個人 / 小團隊情境只需要 `read:sensitive`，剩下 deploy 走 webhook 自動觸發（Coolify 內建 GitHub push webhook，不需 CLI 端 deploy scope）。

明確要走 write 路徑時才讀 `api-access-write.md`。
