---
name: devlog
description: 在任何 repo 維護一份決策日誌（devlog）：把架構/技術決策、trade-off、open question 的決議，記成可被 grep 與 LLM 回溯的 ADR 索引。當使用者做出或想記錄一個技術/架構決策、權衡取捨、解掉一個 open question、問「我們當初為什麼這樣決定 / 為何不用 X / 這個架構怎麼來的」、說「把這個決定記下來 / log this decision / 記一筆 ADR」、跑 /log-decision、或要在新 repo 建立決策追蹤機制時，務必使用本 skill。也涵蓋查詢過往決策、以及避免把機密/個資寫進 git 的 sensitivity gate。
---

# devlog — repo 決策日誌

## 這是什麼、為什麼存在

專案在迭代時會不斷做決策、權衡、改變 spec、修 bug，但這些「為什麼」通常散落在 commit message、PR 討論、聊天記錄裡，過幾週就沒人（包含 LLM）記得當初為何這樣決定。devlog 是一個**LLM 維護、人審閱**的決策索引：知識**編譯一次、持續維護**，未來要回溯「何時、為何、放棄了什麼」時有單一入口可查，而不是每次從零拼湊。

靈感來自 Karpathy 的 *LLM Wiki* 模式（index + log + 由 schema 規範的 LLM 維護流程），但專注在**開發決策**這個 domain。

## 心智模型：三個組成

```
docs/devlog/
  LOG.md                      # 脊椎：時間序、append-only、固定前綴可 grep
  decisions/ADR-NNNN-slug.md  # 每筆決策一檔（兩種模式，見下）
  config.json                 # 這個 repo 的 sensitivity profile 等設定
```

- **LOG.md** 是導航入口。每筆一行 `## [YYYY-MM-DD] decision | ADR-NNNN | <title>`。回溯時**先 grep LOG.md** 找到相關 ADR，再 drill into 細節。
- **ADR 檔**是細節。分兩種模式（關鍵設計，決定要不要重複內容）：
  - **pointer 模式**：這個決策的權威全文已存在別處（spec 段落、既有 decisions 檔、PR）。devlog 只寫 1–3 行 digest + `refs:` 連過去，**不複述完整理由**。→ 避免 two-sources-of-truth drift。
  - **full 模式**：這個決策在別處沒有完整紀錄（口頭拍板、review verdict、跨檔的綜合判斷）。devlog 的 body **就是**權威全文（Context / Options 含 trade-off / Decision / Consequences）。
- **config.json** 決定 sensitivity gate 強度（`profile`）與 id 前綴。

## 三條 doctrine（為什麼重要）

1. **devlog 是決策回溯的單一權威來源。** 其他記憶機制（session 日誌、自動 memory）只當輔助參考。決策要回溯時看這裡，不是去翻聊天歷史——這樣回溯才可靠、可重現。
2. **任何 devlog 檔 / commit / git 歷史，永遠不得含機密或個案可識別資訊。** git 是 append-only、會被 clone、進 CI/code search/LLM context；一旦寫進去，事後刪除仍可從歷史撈回。引用資料一律用佔位符（`<client-id>`、`<tenant>`、`<redacted>`）。這就是為什麼有 sensitivity gate。
3. **狀態只存一處。** ADR 的 `status`（proposed/accepted/superseded/rejected）只寫在該檔 frontmatter；LOG.md 只記不可變事實（哪天開了哪筆），**不重複寫狀態**，否則兩邊必然 drift。

## 主工作流：記錄一筆決策

當使用者做出/想記錄一個決策時：

1. **判斷模式**：這個決策在別處有沒有權威全文？有 → `pointer`；沒有 → `full`。
2. **跑 helper 建檔**（不要手寫檔案或手算 id，交給腳本以免飄）：
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" new <repo-root> \
     --title "<簡短決策標題>" \
     --mode full|pointer \
     --status proposed|accepted \
     --slug <short-english-slug> \
     --refs "PR#6, specs/001/plan.md" \
     --resolves "Q-1" \
     --tags "phase-2, encryption"
   ```
   - `$CLAUDE_PLUGIN_ROOT` 在透過 `/log-decision` 觸發時已設好；若直接用 skill 而未設，請定位 `devlog/scripts/devlog.py`（在 plugins 目錄下）。
   - 標題用中文沒問題，但 `--slug` 請給一個簡短英文 slug（檔名用），例如 `cursor-pagination`。
   - 腳本會配下一個 `ADR-NNNN`、建檔、把對應行 append 到 LOG.md，並印出檔案路徑（JSON）。
3. **填 body**：打開印出的檔案，填模板裡的區段。
   - **full 模式**：Context（痛點）→ Options considered（**每個選項的 trade-off，這是回溯精華，別省**）→ Decision → Consequences → Links。
   - **pointer 模式**：Decision digest（1–3 行）+ Trade-off digest（1–3 行）+ Authoritative source（指向 refs）。
4. **過 sensitivity gate**：
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" scan <repo-root>
   ```
   有命中 → 改用佔位符後重掃，直到乾淨。
5. **commit 約定**：與這次決策相關的 commit，message 末行加 `Devlog: ADR-NNNN`；確定不需記決策的 commit 加 `Devlog: none`。這比事後掃 commit 猜哪些漏記可靠。

> 一筆決策大概就一個檔 + 一行 LOG。不要為了「完整」把每個瑣碎改動都記成 ADR——只記**未來的人會想問「為什麼」的決策與取捨**。

## 工作流：查詢過往決策

使用者問「我們當初為何…/為什麼不用 X/這架構怎麼來的」時：

1. `grep -i "<關鍵字>" docs/devlog/LOG.md` 找候選 ADR（也可 grep `decisions/`）。
2. 讀對應的 `decisions/ADR-NNNN-*.md`。pointer 模式就順著 `refs` 去讀權威全文。
3. 綜合後回答，並**附上 ADR 編號**當引用。
4. 若這次查詢產生了有價值的新結論（例如釐清了一個沒記過的決策），可主動建議補一筆 ADR——好答案不該只活在對話裡。

## 在新 repo 採用 devlog

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" init <repo-root> --profile <none|secrets-only|pii|custom>
```
這會建 `docs/devlog/`（LOG.md + config.json + decisions/）。接著：
- 在該 repo 的 CLAUDE.md / AGENTS.md 加一段指向 devlog 的 doctrine（見 `references/adopting-in-a-repo.md`）。
- 把 sensitivity gate 掛到 push 前（pre-push hook 或 CI required check），細節見 `references/adopting-in-a-repo.md`。
- profile 怎麼選見下節。

完整採用步驟（含 CLAUDE.md 範本、CI 接法、受控 seed 既有決策的方法）見：
**`references/adopting-in-a-repo.md`**

## Sensitivity gate（profile）

`scan` 依 `config.json` 的 `profile` 決定掃什麼：

| profile | 掃描內容 | 適用 |
|---|---|---|
| `none` | 不掃 | 純玩具 repo |
| `secrets-only`（預設） | 機密：private key、AWS/GitHub/Slack/Google key、`key=...` 賦值 | 多數 repo |
| `pii` | secrets + 台灣身分證/手機/email/長數字串 | 含個資的 repo（如諮商系統） |
| `custom` | secrets + pii + `config.json` 的 `custom_patterns` | 有自家機密樣式 + 個資的 repo |

> `custom_patterns` 在任何非 `none` profile 都會套用（不會排擠內建規則）。

> ⚠️ **devlog 是標準化/導航工具，不是個資合規控制。** scan 是「盡力而為的最後一道薄網」：
> 它**一定漏**自由文字 PII——中文姓名、地址、生日、病情/創傷描述、以及「年齡+地區+職業+事件」這類組合型再識別資訊。
> 真正的防線是 doctrine #2（**絕不把個案事實寫進 devlog**）。**含特種個資的 repo**（如本諮商系統）不能只靠 scan，
> 必須另加 repo 層治理（CI required check、成熟 scanner、CODEOWNERS 隱私審、誤寫 purge runbook），見
> `references/adopting-in-a-repo.md` 的「PII repo 額外治理」。各 profile 細節見 `references/sensitivity-profiles.md`。

## 範圍紀律：現在「不做」什麼

devlog 第一版**只做 decision 型別**。以下是刻意延後的，沒有明確 trigger 不要主動加（避免過度設計）：

- **bug / incident 型別** — decision 以外的型別。incident（含 PII 鑑識）尤其要等真上線、且要先設計 sanitized-metadata + 受控 vault，不可把鑑識細節塞進 repo。
- **INDEX.md** 分類表 — 條目少時用 grep LOG.md 生成即可。
- **/devlog-lint** 對帳、**hook 自動捕捉** — 等手動流程被證明會漏再加。
- **qmd / 專用搜尋** — grep 夠用前不做。

需要加這些時，是另一次有意識的決策（本身也該記一筆 ADR）。

## 參考檔

- `references/adopting-in-a-repo.md` — 在新 repo 完整採用（CLAUDE.md 範本、CI gate、受控 seed）
- `references/sensitivity-profiles.md` — 各 profile 細節、custom pattern、誤寫 PII 的補救（security purge）
