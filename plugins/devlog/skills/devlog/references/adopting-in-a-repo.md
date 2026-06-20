# 在新 repo 採用 devlog

一次性設定，之後這個 repo 就有決策回溯能力。

## 1. init

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" init <repo-root> --profile <none|secrets-only|pii|custom>
```
建立 `docs/devlog/`（LOG.md + config.json + decisions/）。profile 怎麼選見 `sensitivity-profiles.md`。

## 2. 在 CLAUDE.md / AGENTS.md 加 doctrine

讓這個 repo 的 LLM 知道 devlog 存在、何時用、紅線在哪。貼一段（依 repo 調整）：

```markdown
## 決策回溯：devlog

- 本 repo 的決策回溯單一權威來源是 `docs/devlog/`（LOG.md + decisions/）。
  要回溯「為何這樣決定 / 為何不用 X」時先 grep LOG.md，不要翻聊天歷史。
- 做出值得未來回溯的技術/架構決策、權衡、或解掉 open question 時，用 `/log-decision` 記一筆。
- 紅線：devlog 任何檔/commit/git 歷史，永不得含機密或個案可識別資訊；引用一律用佔位符。
- profile：<這個 repo 用哪個 profile，例如 pii>。
```

## 3. 把 sensitivity gate 掛到 push 前

見 `sensitivity-profiles.md` 的「把 scan 掛到 push 前」。本地 pre-push + CI required check 兩層都上。

## 4. PII / 特種個資 repo 額外治理（必備）

如果這個 repo 含個資（尤其台灣個資法第 6 條特種個資，如醫療/諮商紀錄），**devlog 內建的 scan 不足以當合規防線**（scan 一定漏自由文字 PII）。採用前必須補上以下 repo 層治理，缺一不可：

- **明文紅線**：在 CLAUDE.md / CONTRIBUTING 寫死「devlog 只能記**已去識別化的架構決策**，禁止寫入任何個案事實」；pointer 模式只能指向**已去識別化**的來源。
- **CI required check（不可繞過）**：把 `devlog.py scan` + 成熟 secret/PII scanner（gitleaks / trufflehog）設為 PR 必過檢查；掃 staged diff、devlog、**commit message**，必要時掃 git history。`--no-verify` 只能繞本地，繞不過 CI。
- **CODEOWNERS 隱私審**：`docs/devlog/` 列入 CODEOWNERS，變更需指定的隱私/資安 reviewer 簽核。
- **PR template 個資檢查項**：PR 範本加一條「□ 本 PR（含 devlog 變更）不含任何個案可識別資訊」。
- **誤寫 purge runbook**：寫好「PII 不慎進 commit」的處置 SOP（`git filter-repo` 移除整段歷史 + force-push + 輪換外洩機密 + 依個資法評估通報），見 `sensitivity-profiles.md` 末段。
- **法規對齊**：保密義務＝心理師法 §17、紀錄保存十年＝§25；通報判定不由 LLM 或單一工程師拍板。

> 定位提醒：devlog 是「決策標準化 + 導航」工具，**不是個資合規系統**。上述治理是把它安全地放進特種個資 repo 的前提。

## 5.（選用）受控 seed 既有決策

如果 repo 已經跑了一陣子、有些重要決策散落各處，可以**受監督地**回填——但嚴格收斂，否則會把舊 log 的雜訊（甚至 PII）整段搬進來：

- **只回填**「正在被引用、且原址沒有完整紀錄」的決策。
- **硬上限**（例如 ≤8 筆）。每筆問一句：「未來的人/LLM 真的會回來查這個嗎？」否則不收。
- 已在別處有權威全文的（spec 段落、既有 decisions 檔），用 **pointer 模式**只連過去，**不重寫**。
- **逐筆過 scan**，禁止無人值守批次 ingest 舊檔（最容易把 PII/機密整段帶入）。
- bug / incident 一律不回填（第一版不做這些型別）。

seed 完跑一次 `scan <repo-root>` 確認乾淨。

## 6. 驗證

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" next-id <repo-root>   # 看配號是否接續
python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" scan <repo-root>      # 應乾淨
grep -c "^## \[" docs/devlog/LOG.md                                    # 看有幾筆
```
