# api-access-write.md — Coolify write / deploy scope 申請

> **何時讀**：**只在使用者明確要求** Claude 自動化部署 / 改 env / 加 application / 改 domain 時。

> ⚠️ **預設不主動引導申請這把**。理由：write scope 一旦外洩，AI / 攻擊者可以改 env（注入惡意 connection string）、加 domain（接管流量）、強迫部署任意 commit。比 `read:sensitive` 更危險（後者只能讀）。

如果你只是想撈 build log debug / 跑 monitor 看 status —— 那走 `api-access.md` 就好，本檔不看。

---

## 哪種 scope 對應哪種需求

| 想做 | 需要 |
|-----|------|
| 觸發部署（CI / 自動化） | `deploy` |
| 改 application 設定 / env / domain / port 等 | `write` |
| 取消跑到一半的部署 | `deploy` |
| 加新 application / database / service | `write` |
| 刪 application / database / service / private-key | **`root` —— 強烈不建議發** |

**deploy < write** —— 兩者不互含。`deploy` 只能觸發既有 app 的部署，不能改設定。需要 CI 自動部署即 `deploy`；要改 env / domain 才需要 `write`。

---

## 申請 `deploy` scope（CI / 自動化部署）

> 若 CI 是 GitHub Actions / GitLab CI，**首選**走 Coolify 內建 webhook（不需 API token），CI 只要 push code 即可，Coolify 自動 webhook 觸發 build → 部署。這條路完全不需 deploy scope 的 token，最安全。
> 只有當你**真的需要從 CI 跑 `coolify app start`** 顯式觸發（如：跑 monorepo 內 N 個 app 的批次部署、或要在部署前後跑 hook script）才走下面流程。

申請：

1. **登入 Coolify Web UI** → Security → API
2. **Name**：`ci-deploy-<env>-2026Q2`
3. **Scope**：勾 `read` + `deploy`（read 用於 monitor status，deploy 用於觸發）
4. **Expiration**：選 **30 天**（CI scope 要短，輪替頻繁）
5. **API Allowed IPs**：加 GitHub Actions runner range（或自架 runner IP）
6. **建好** → 立即進 GitHub repo Settings → Secrets → 新增 `COOLIFY_DEPLOY_TOKEN`

護欄：

- **CI workflow 不 echo 這把字串**（即使 GitHub Actions mask 也可能在 set-output / debug log 漏）
- **每次部署成功後不持久化** —— GitHub secrets 是給 workflow runtime 用，不下載到 local
- **輪替**：30 天到期前換新；舊的 revoke

---

## 申請 `write` scope（改 env / domains / 加 app）

⚠️ 這把若給 Claude / AI 用，等同把整個 team 的設定權交給 AI。**強烈建議：write scope 不長期存在，**只在執行特定一次性任務（如 batch 改 N 個 app 的 env、加新 domain）時：

1. **臨時建一把 `write` scope** 設 7 天過期
2. **在 IP allowlist 加上你正在跑指令的機器 IP**（執行完立即移除）
3. **跑完任務立即在 Coolify revoke**

不要長期保留 `write` scope 的字串 —— 沒有「日常使用」這個情境。

---

## ⚠️ `write` / `deploy` 風險與護欄

| 風險 | 內容 |
|------|------|
| 改 env 注入惡意 connection string | 把 `DATABASE_URL` 改指向攻擊者控制的 DB，下次部署 app 就連去 |
| 加 domain 接管流量 | 加 `your-victim.com` 指向自己 server（DNS 對到 Coolify IP 才會生效，但 race condition 仍有風險） |
| 強迫部署任意 commit | 若 GitHub repo 不是 protected branch，攻擊者改 default branch 後觸發 deploy |
| 取消生產部署 | `coolify deploy cancel` 可造成 service outage |

護欄（**全部**做）：

1. **IP allowlist 必開**（同 read:sensitive）
2. **短過期**（7-30 天，不超過 30 天）
3. **拆 team**：write scope 限縮在 staging team，production 走 Web UI + 雙人確認，不發 production write scope
4. **每次申請寫進 audit log**（公司內部 wiki / decision log），日後 review 知道誰在何時申請了什麼
5. **配合 Claude permission deny**（見 `claude-permissions.md`）：即使有 write scope，AI 端 settings.json 也擋掉 `coolify app delete*` / `coolify env update*` 等高危指令

---

## 不要發 `root`

`root` 包含 server 管理、private key 操作、刪 application / database。任何使用情境都能用 read:sensitive + write 拆開達成 —— `root` 唯一的優勢是「一把通吃」，但**這正是不該發的理由**。

如果你發現自己在想「給個 root 比較方便」，停下來檢查：

- 真正需要的操作是什麼？（多半是看 build log → read:sensitive 就夠）
- 是不是少量一次性操作？（走 Web UI 手動）
- 是不是已經有 deploy webhook？（不需 root 就能部署）

99% 個人 / 小團隊 / 中型 SaaS 沒有任何情境需要 root scope。

---

## 撤銷流程（任何 scope 都一樣）

```
Coolify Web UI → Security → API → 找到 scope → Revoke
```

revoke 後立即：

1. 從所有環境變數 / `.env` / `~/.config/coolify/config.json` 刪除
2. 從 CI secret / GitHub repo secret 刪除
3. 從 shell history 清掉（`history -d <line>` + `history -w`）
4. （若曾經 echo 到 transcript / chat）視為「已洩漏」，連帶 audit log

撤銷不需通知 Coolify team admin —— 你建的、你管。
