# daily-agenda — Tasks

> rev5。每個 Phase 先寫測試再實作（TDD）。Gate 未過不進下一 Phase。
> Windows 實機步驟標 🪟；資安相關標 🔒（派 `security-executor`）。

## ═══ Phase 0 — 實測（不產出正式程式碼；不匯入 vendor） ═══

| # | 實測 | 通過條件 | 失敗時 | 阻擋 |
|---|---|---|---|---|
| 0.1 🪟 | `requiresUserInteraction` 於 `default`／`dontAsk`／`bypassPermissions`；提示是否顯示 `target_display`／`content_display` | 三種模式都跳出本人確認，且顯示完整內容 | 不採用任何模型可代打的備案；回報使用者重新決定送出功能 | 阻擋 Phase 5 |
| 0.2 🪟 | `claude -p --tools "" --strict-mcp-config --safe-mode --no-session-persistence`：工具清單為空、stdin 輸入上限、對抗測試（要求讀外部 sentinel 檔、呼叫工具） | 工具清單為空、對抗測試全部無效、可處理 D-9 預算量 | 回報使用者；不以「限制工具種類」的子代理替代 | 阻擋 Phase 4 |
| 0.3 🪟 | 送出工具序列（自己的測試對話）：普通訊息、引用回覆、既有草稿、取消、結果不確定 | 每種情境的工具序列、冪等鍵、回執欄位都有紀錄 | 只支援普通訊息，引用回覆延後 | 阻擋 Phase 5 |
| 0.4 🪟 | v3.3.5 exact tag 對應的 CUA Driver 版本＋不送出的草稿冒煙測試 | 版本確定、草稿出現在正確對話 | 換用上游驗證紀錄中的版本重測 | 阻擋 Phase 5 |
| 0.5 🪟 | hook 與 plugin `.mcp.json` 以 `node` 啟動、`${CLAUDE_PLUGIN_ROOT}` 展開 | 皆可用 | hook 改用 `cmd /c node` 重測 | 阻擋 Phase 3 |
| 0.6 🪟 | MCP client 呼叫 df-graph 公開工具，沿用既有登入 | 讀到清單、全文、行程 | 改用 df-graph 的 `uv run` 命令直接啟動 server | 阻擋 Phase 2 |
| 0.7 🪟 | 7／14／30 天最大資料量；`recentChats` 超過 50 個對話時的分頁 | 取得各量級數字；能取得全部對話 | 擴充 vendor 分頁介面（列入 Phase 1）後重測 | 阻擋 Phase 3 |
| 0.8 🪟 | `claude -p` 經工作排程器無互動執行（cwd、登入、權限、逾時、電源） | 能產出彙整並正常結束 | 排程功能延後到 1.x | 阻擋 Phase 6 排程部分 |
| 0.9 🪟 | D-11：暫時信任檔、Authenticode 簽章讀取 | 簽章可讀且發行者正確、試讀後暫時檔已刪 | 回報使用者；不以路徑／大小比對替代 | 阻擋 Phase 2 |
| 0.10 🪟 | 讀 LINE 程式記憶體是否觸發防毒／EDR | 無告警，或有告警但可由 IT 白名單 | README 標示「需 IT 核可」；LINE 改為選用來源 | 不阻擋（揭露） |

**Gate 0**：結果寫入 `phase0-findings.md`；任何「失敗時」需要改設計的，回寫 plan.md 並經使用者同意。

## ═══ Phase 1 — vendor line-desktop-mcp＋df-graph 小改 ═══

- [ ] 1.1 匯入 v3.3.5 原始碼至 `vendor/line-desktop-mcp/`（保留 LICENSE、THIRD_PARTY），寫 `vendor/UPSTREAM.md`（基準 tag／commit、工具清單快照、patch 清單、重套與升版安全審查步驟）
- [ ] 1.2 🔒 先寫測試：信任清單聯集；檔案缺失／格式錯誤／重複項 → fail-closed
- [ ] 1.3 🔒 實作 patch 0001（`LINE_MCP_TRUSTED_BUILDS`）
- [ ] 1.4 🔒 先寫測試：`LINE_MCP_MAIN_DATABASE` 拒絕路徑分隔符、`..`、不存在檔名、album／keep／chatStats；未設定且多份時仍 `MAIN_DATABASE_AMBIGUOUS`
- [ ] 1.5 🔒 實作 patch 0002（若 0.7 需要，另加 patch 0003 分頁）
- [ ] 1.6 上游 `npm test`、`npm run test:python` 全綠；patch 可對乾淨 v3.3.5 重套
- [ ] 1.7 🔒 security review 所有 patch
- [ ] 1.8 df-graph 先寫測試（`test_tools.py`）：預設行為不變；`immutable_ids=True` 標頭；`internetMessageId` 欄位；`calendar_create(transaction_id=…)` 請求內容
- [ ] 1.9 實作 D-13＋bump df-graph 版本＋`df-graph-upstream-note.md`（給原作者的說明草稿，由使用者決定何時送出）

**Gate 1**：1.6、1.8 全綠＋1.7 無未處理高風險；🪟 以 patched vendor 重現 spike 結果；🪟 `immutable_ids=True` 下移動一封信後 ID 不變。

## ═══ Phase 2 — setup 第 0–5 步 ═══

- [ ] 2.1 `state.py`＋測試（config、世代目錄、CURRENT 原子替換、發布與補發、未提交世代清除）
- [ ] 2.2 setup 第 0 步：資料外送說明＋同意紀錄（`config.consent`）；未同意則結束且 `/agenda` 拒絕執行＋測試
- [ ] 2.3 `envcheck.py`＋測試（Claude Code／Node／uv 版本、LINE 未執行的白話訊息、簽章讀取）
- [ ] 2.4 `runtime.py`＋測試（暫存目錄安裝→驗證→rename、雜湊驗證、版本戳記）
- [ ] 2.5 🔒 `trust.py`＋測試（D-11）
- [ ] 2.6 `line_collect.mjs --probe`＋`line_detect.py`＋測試（多份 DB 試解、全失敗、多份成功）
- [ ] 2.7 `graph_client.py`＋`collect.py --probe`＋測試（df-graph 未註冊或版本過舊時的白話訊息）
- [ ] 2.8 setup 第 5 步：列出 30 天內對話 → 勾選排除 → 寫 `excluded_chats`；初始 `known_chats` 寫入第一個世代
- [ ] 2.9 `skills/agenda-setup/SKILL.md` 第 0–5 步＋`commands/agenda-setup.md`；第 4 步委派 `/df-graph-setup` 並說明權限
- [ ] 2.10 🪟 本機跑 setup 0–5

**Gate 2**：AS-1、AS-2、AS-3、AS-5、AS-12（setup 部分）通過。

## ═══ Phase 3 — 收集＋setup 第 6–7 步 ═══

- [ ] 3.1 `collect.py`＋測試（各來源 checkpoint、2 小時重疊、immutable ID＋`internetMessageId` 去重、31 天切段、對話過濾、新對話、局部失敗不推進）
- [ ] 3.2 bundle.jsonl 格式（不可信欄位標記）＋測試（SC-3：排除對話零殘留）
- [ ] 3.3 `ctx_index.py`＋測試（支援格式、略過 symlink／junction）
- [ ] 3.4 setup 第 6–7 步（ctx、輸出格式／預設路徑、判斷準則說明＋頻率、排程詢問）

**Gate 3**：AS-4 通過。

## ═══ Phase 4 — 分析＋彙整 ═══

- [ ] 4.1 `analyze.py`＋測試（D-12 啟動參數、stdin／stdout、逾時、非法 JSON）
- [ ] 4.2 model.json schema＋`validate.py`＋測試（來源 ID 存在性、超長欄位、提示注入 fixture 下的不變量）
- [ ] 4.3 `ledger.py`＋測試（固定代碼、各狀態轉移含 collapsed → open、報表代碼不是最新時拒絕）
- [ ] 4.4 `render.py`＋測試（摺疊區、檔頭狀態與報表代碼、XSS fixture 含藏在原文中的惡意字串、CSP、同日重跑檔名）
- [ ] 4.5 提交＋發布＋補發＋crash-injection 測試（SC-10）
- [ ] 4.6 setup 第 8 步：掃 7／14／30 天 → 分析程序 → 準則草稿 → 確認存 `criteria/v1.md`
- [ ] 4.7 `skills/agenda/SKILL.md`＋`commands/agenda.md`：補發檢查 → collect → 分析程序 → validate → ledger → render → 提交 → 發布；標記完成子流程（US-13）；D-9 分批
- [ ] 4.8 `/agenda` 遇 `LINE_BUILD_UNVERIFIED` 就地走 D-11（排程模式依 D-14 跳過）
- [ ] 4.9 marketplace 條目＋`plugin.json` 0.1.0（無 actions）＋README（外送說明、權限、端點防護、條款確認）＋Windows CI workflow

**Gate 4**：AS-6、AS-7、AS-11、AS-12、AS-13、AS-14、SC-2、SC-7、SC-8、SC-9、SC-10、SC-11 通過；🪟 本機產出真實彙整由使用者抽查。

## ═══ Phase 5 — 對外動作 ═══

- [ ] 5.1 runtime 加裝 CUA Driver（版本依 0.4）
- [ ] 5.2 🔒 `actions/server.mjs`＋測試（prepare／commit／status、完整 payload、顯示欄位比對、雜湊、期限、原子 rename 所有權、並行 commit、各當機點恢復為 uncertain、`transaction_id` 重試沿用）
- [ ] 5.3 🔒 `guard.mjs` 非代理送出攔截＋測試
- [ ] 5.4 `skills/agenda-reply/SKILL.md`：擬稿 → prepare → 顯示完整預覽 → commit（本人確認）→ 偵測時間 → 詢問建事件（同流程）
- [ ] 5.5 行事曆去重＋測試（Q-1）
- [ ] 5.6 🔒 security review（actions、guard、reply skill）
- [ ] 5.7 `.mcp.json`＋plugin 0.2.0

**Gate 5**：AS-8、AS-9、SC-4、SC-5 通過（🪟 對自己的測試對話實送一次）。

## ═══ Phase 6 — 修正迴圈＋排程 ═══

- [ ] 6.1 state 計數與觸發＋測試（10 次或 14 天先到先算；可設定）
- [ ] 6.2 `/agenda` 修正子流程：我先說／你先整理／跳過；準則版本 +1
- [ ] 6.3 `schedule.py`＋測試（`schtasks`、D-14 無互動規則、執行鎖、逾時不提交）
- [ ] 6.4 `uninstall.py`＋測試
- [ ] 6.5 plugin 1.0.0

**Gate 6**：AS-10、AS-15 通過。

## 端對端驗收

依 spec.md「端對端驗收」7 步，在乾淨 Windows 帳號執行並記錄於 `e2e-report.md`。完成後刪除 `C:\Users\haha.huang\.line-mcp-spike\`。
