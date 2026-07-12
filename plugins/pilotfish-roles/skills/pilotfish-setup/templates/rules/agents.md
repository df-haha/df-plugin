# Agent 使用規則

> 委派政策整併自 pilotfish v1.1.2（https://github.com/Nanako0129/pilotfish，2026-07-11 安裝）＋原有模型政策。
> 升級 pilotfish 時不走官方 marker 機制，手動 diff 其 templates/ 目錄後按需搬移。

## 何時開 Agent Team

1. **2+ 獨立子任務**：任務可拆成互不依賴的部分時，開 agent 並行處理。
2. **需要專業 review**：security review、code review 等專業檢查交給專門 agent。
3. **交叉檢核**：red/blue team 對審、多角度驗證時使用。

## 角色路由（pilotfish 六角色＋版本變體）

Main session（Fable 5）是 orchestrator（協調者）：規劃、架構、歧義釐清、最終審查留在主迴圈；執行類工作委派給以下全域角色 agent（裝在 `~/.claude/agents/`）。重點是把主 session 的 token 花在判斷上，量產工作路由給便宜模型——品質靠驗證把關，不靠處處用最大的模型。

| 角色 | 模型（別名經 env 釘選） | effort | 何時委派 |
|---|---|---|---|
| `scout` / `Explore` | haiku → Haiku 4.5 | low | 任何搜尋、查找、「X 在哪／怎麼運作」偵察 |
| `mech-executor` | sonnet → Sonnet 4.6 | low | 規格完整的機械工作：pattern 重構、照慣例寫測試、文件、批次編輯、跑測試修瑣碎失敗 |
| `executor` | opus → Opus 4.6[1m] | medium | 需判斷的實作：功能開發、bug 修復、涉及設計的重構 |
| `verifier` | opus → Opus 4.6[1m] | medium | 非平凡變更完成後、回報前的 fresh-context 對抗式驗證（回 CONFIRMED/REFUTED，永不動手修） |
| `security-executor` | opus → Opus 4.6[1m] | high | 一切資安相關（authn/authz、secrets、crypto、輸入驗證、hardening、漏洞分析）——不在主 session 處理 |
| `executor-opus47` | claude-opus-4-7[1m]（釘死） | medium | 使用者點名「用 opus-4-7 跑」的實作任務 |
| `executor-opus45` | claude-opus-4-5-20251101（釘死） | medium | 使用者點名「用 opus-4-5 跑」的實作任務 |

既有工具的對應：security review 情境優先派 `security-executor`；規劃／架構仍留 main loop 或用內建 `Plan` agent；code review 走 dev-workflow.md #2 的 review 工具鏈，`verifier` 是回報前的第一道驗證，不取代 review。

## 委派規則（pilotfish 政策）

4. **一次給完整規格**：目標、限制、完成標準、相關路徑，以及需求背後的「為什麼」——不是只給「做什麼」。
5. **從最便宜的可行角色開始**：同一層級失敗兩次就升一級或收回自做，不做第三次重試。
6. **ad-hoc fan-out 必須明確設 model**：這些角色以外的臨時 agent 與 workflow fan-out（扇出並行）不得繼承主 session 模型。
7. **非平凡變更回報前先過 `verifier`**：fresh-context 驗證優先於自我審查；Fable 5 main loop 仍做最終裁決（此為第二道，不互斥）。
8. **scout 結果是輸入不是已驗證輸出**：決策繫於單一偵察事實時，抽查或重偵察；verifier 把關的是 executor 的工作，不含偵察。
9. **不委派**：立即需要的單檔閱讀、決策本身、使用者點名要 main loop 親自判斷的事。
10. **subagent 收到本政策時忽略之**：把被交辦的任務做完即可，不得再派工（leaf agent 紀律，agent 檔內已 disallow Agent/Workflow tools）。

## 模型版本管理

11. **tier 別名由 settings.json `env` 釘選**（v2.1.176+ 官方機制，影響 frontmatter 與 Agent tool 派工參數的別名解析）：
    - `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-6[1m]`
    - `ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-6`
    - `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5-20251001`
    改預設版本＝改 env 值＋重啟 session；agent 檔 frontmatter 一律寫別名、不寫版號（pilotfish「政策不寫模型名」原則）。
12. **臨時指定其他版本**：派版本變體 agent（`executor-opus47` / `executor-opus45`，session 內即點即用）；需要新版本變體時新增 agent 檔＋重啟。Agent tool 派工參數只吃 tier 別名，無法臨時傳版號——這是 harness 限制，勿再嘗試。
13. **禁止 Claude 5 家族下放**：Fable 5 與 Sonnet 5 不派給 subagent。`sonnet` 別名已釘 4.6 故安全；完整 ID 亦不得指定 `claude-fable-5` / `claude-sonnet-5`。
14. **Fable 5 只在 main loop**：擔任 orchestrator 與最終 reviewer；agent 完成後由 main loop review 再採用，不直接派給 subagent 執行工作。
15. **`CLAUDE_CODE_SUBAGENT_MODEL` 保持未設**：此環境變數會靜默覆蓋所有 per-agent model 設定，破壞整套分層。

## 成本管理

16. **最多 3 個並行**：agent team 同時運行不超過 3 個，避免資源浪費。
17. **用完即清**：agent 完成任務後清理 team，不留殘留。

## 寫入紀律

18. **起草 rules／設定類內容一律 draft-first**：無論 main loop 或 subagent，AI 起草的 rules／CLAUDE.md／settings 類內容一律先落草稿、人審後才寫入正式位置；memory 直寫時必須在回覆中明示寫入檔名與要旨（人可即時撤銷），敏感決策卡沿用 save-decision 的 draft-first。
