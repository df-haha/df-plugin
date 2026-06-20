# Sensitivity profiles 細節

`docs/devlog/config.json` 的 `profile` 欄位控制 `scan` 子指令掃什麼。掃描是「最後一道網」，
真正的防線是寫的時候就不放可識別資訊（doctrine #2）。

## 各 profile 掃描內容

### `none`
完全不掃。只適合確定不含任何機密/個資的玩具 repo。不建議當預設——誰都會手滑 commit 一次 key。

### `secrets-only`（預設）
掃通用機密樣式：
- 私鑰標頭 `-----BEGIN ... PRIVATE KEY-----`
- AWS access key（`AKIA…`）
- GitHub token（`ghp_/gho_/ghu_/ghs_/ghr_…`）
- Slack token（`xox…`）
- Google API key（`AIza…`）
- 通用賦值 `api_key=… / secret=… / token=… / password=…`（值長度 ≥16）

適合大多數 repo——你本來就不該把 key commit 進去。

### `pii`
在 `secrets-only` 之上再加台灣個資樣式：
- 身分證字號（`[A-Z][12]\d{8}`）
- 手機（`09xxxxxxxx`）
- email
- 長數字串（≥9 位，可能是病歷號/身分證；軟性提示，會誤判日期戳，看到請判斷）

適合處理個資的 repo（如諮商紀錄系統）。注意：pattern 抓不到「人名」「地址」這類自由文字 PII，
所以 doctrine #2（寫時就用佔位符）才是主防線。

### `custom`
= **secrets + pii + 你自己的樣式**（選 custom 不會犧牲內建 PII 規則）。在 `config.json` 加：
```json
{
  "profile": "custom",
  "id_prefix": "ADR",
  "custom_patterns": [
    "(?<![A-Za-z0-9])MRN-[0-9]{8}(?![A-Za-z0-9])",
    "(?i)\\binternal[_-]?customer[_-]?id\\b\\s*[:=]\\s*\\w+"
  ]
}
```
`custom_patterns` 是 Python regex 字串（記得跳脫反斜線）。**`custom_patterns` 在任何非 `none` profile 都會套用**，
所以你也可以維持 `profile: pii` 同時加自家病歷號樣式，不一定要切到 `custom`。
寫樣式時邊界建議用 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` 而非 `\b`——`\b` 在中文旁會失效（這也是內建規則踩過的坑）。

## scan 不是合規控制（重要）

scan 只是「盡力而為的最後一道薄網」，**不是個資法的合規防線**。即使 profile=pii，它仍**一定漏**：
- 中文/英文姓名、暱稱、家屬關係
- 地址、學校、工作場所、診所名稱
- 出生年月日、就診/會談日期（尤其與其他欄位組合後可識別）
- 病情、診斷、創傷、自傷/他傷、性議題、用藥等自由文字
- 「42 歲、某縣市、某職業、某特定事件」這類組合型再識別資訊
- 截圖、transcript、commit message、PR 討論裡的個案細節（scan 預設只掃 `docs/devlog/**/*.md`）

→ 主防線永遠是 doctrine #2（**寫的時候就不要放任何個案事實**）。含特種個資的 repo，scan 之外**必須**加 repo 層治理，見 `adopting-in-a-repo.md` 的「PII repo 額外治理」。

## 把 scan 掛到 push 前

scan 是 repo 採用 devlog 時要接上的「安全 gate」。兩個接法（建議都上）：

**1. 本地 pre-push hook**（`.git/hooks/pre-push`，或用 husky/lefthook 管理）：
```bash
#!/usr/bin/env bash
python3 "<plugin>/scripts/devlog.py" scan "$(git rev-parse --show-toplevel)" || {
  echo "devlog scan 攔截：有機密/個資疑慮，push 已中止"; exit 1; }
```

**2. CI required check**（GitHub Actions 等）——這層**不可**被 `--no-verify` 繞過：
```yaml
- name: devlog sensitivity scan
  run: python3 path/to/devlog.py scan "$GITHUB_WORKSPACE"
```
本地 hook 是體驗（快、擋在 push 前），CI 是底線（擋住繞過本地 hook 的人）。

## 萬一機密/PII 已經寫進 commit 了

LOG.md 雖然是 append-only，但**這條規則不適用於誤寫機密/PII 的補救**——因為單純再開一個 commit 刪掉內容，
舊 commit 仍可 `git log -p` 撈回。正確處置（security purge 例外）：

1. 立刻停止 push（若還沒 push 更好）。
2. 用 `git filter-repo`（或 BFG）把該內容從**整段歷史**移除，然後 force-push。
3. **輪換**已外洩的機密（key/token 立即作廢重發）。
4. 自己補一筆 ADR 記錄「為何做了 history rewrite」（這本身是一個值得回溯的決策；當然 ADR 內容也不要再貼出那個機密）。

換句話說：append-only 是常態紀律，security purge 是明確的例外流程，兩者並存。
