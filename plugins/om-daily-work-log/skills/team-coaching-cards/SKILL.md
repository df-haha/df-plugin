---
name: team-coaching-cards
description: 主管端 — 將 team-daily-fetcher 產出的「待釐清疑問」轉換為可寄給屬下的「澄清問題卡」md。每位屬下一張卡，每張卡含主管看到的工作摘要 + 3-5 個 CC 可查證的提問（接 git/spec/tasks 出處）。觸發時機：/hi Phase 3.X、或主管說「產卡」「澄清卡」「coaching cards」「產問題卡」「給屬下出問題」「team coaching」。產卡後詢問主管 review，確認後 reply 屬下原日報寄出。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, Skill, mcp__df-graph__mail_search, mcp__df-graph__mail_list_recent
---

# Team Coaching Cards — 主管端澄清問題卡產生器

把 team-daily-fetcher 已抓好的屬下日報 + Q2 任務分派對齊度判斷，轉成**可直接 reply 屬下日報的澄清問題卡**。

---

## MANDATORY EXECUTION PROTOCOL

此 skill 包含 6 個 Phase。Phase 1 → 6 必須循序執行。

### 開始前必做

1. `ToolSearch("select:TaskCreate,TaskUpdate,AskUserQuestion")` 載入工具（如未載入）
2. `TaskCreate` 建 6 個 task：
   | # | Phase |
   |---|-------|
   | 1 | Phase 1：讀資料來源 |
   | 2 | Phase 2：套用三項禁區規則 |
   | 3 | Phase 3：每位屬下產卡 |
   | 4 | Phase 4：寫入卡片 md |
   | 5 | Phase 5：主管 review |
   | 6 | Phase 6：reply 屬下原日報寄出 |
3. 每 Phase 開始 `TaskUpdate` 標 in_progress、結束標 completed

---

## Phase 1：讀資料來源

讀以下三個來源（必讀，缺一不可）：

### 1.1 屬下日報（已由 team-daily-fetcher 抓進 data/daily_reports/）
```bash
ls -1 data/daily_reports/{target_date}/*_daily_work_log_{target_date}.md
```
對每位屬下：用 Read 工具讀 md 內容，抽：
- 工作項目清單（依 H2/H3 標題）
- AI 用量 ccusage 表（看當日成本）
- 待處理事項

### 1.2 任務分派 / 追蹤文件（source-of-truth；config 驅動）
```bash
# 讀 config.paths.tracking_files 列出的任務/進度文件（若 config 有設定）
Read <config.paths.tracking_files 的每個檔>
```

> 🔴 **HARD GATE：讀「最新一週」的逐人列，不是讀整檔頭尾。**
> 追蹤檔通常是逐週累積（可能數百行、回溯數月）。**只讀頭尾會抓到過期數字**（吃過虧：把 3 月的 25% 當基準、把支線當主項）。
> 強制步驟：
> 1. `grep -nE "週報|準會議|會議實際" <檔>` 找出**最新一週**的段落起始行。
> 2. `Read(offset=最新週起始行)` 把那一週的**降本項目表 + 增效項目表完整讀進來**。
> 3. 對**每位屬下**抽出其 tracked 列的：**達成率% / RAG / 預計完成日 / 🔴卡關 / 追蹤事項**（逐欄，不是憑印象）。
> 4. 注意**一人可能有多個 tracked 主項**（例：靠行對帳 + 鋼廠買貨各一列）——全部抽，不要只抓一個。

抽每位屬下的：
- 任務線清單（**最新一週逐人列的 tracked 主項，含達成率/RAG/卡關**）
- tracking_files 追蹤項目對應（若 config 有設定）
- 主管已知前提（如：某專案架構 = 上級已拍板、某任務優先序 = 主管要求）

> ⚠️ 若手動跑 cockpit（未 invoke team-daily-fetcher skill）導致 daily_proposal 的「🎯 團隊引導」對齊度是弱的/缺的 → **本 1.2 的 HARD GATE 不可跳過**，必須自己把最新週逐人列讀齊，否則卡片會對齊到「日報講了什麼」而非「追蹤檔要交付什麼」。

### 1.3 team-daily-fetcher 已產出的對齊度判斷
從 daily_proposal/daily_proposal_{target_date}.md 抽「🎯 團隊引導」區塊：
- 每位屬下的對齊度標記（✅ / ⚠️ 偏離 / ⚪ 必要）
- 已經識別的「待釐清疑問」條列

> 若 daily_proposal_{target_date}.md 不存在或缺「🎯 團隊引導」區塊 → 提示主管先跑 /hi Phase 1.5，或退出此 skill

---

## Phase 2：套用三項禁區規則（HARD CONSTRAINT）

**絕對不能違反**的三項禁區：

### 禁區 1：語氣 — 提問非質疑
- ❌ 禁用詞：「為什麼」「為何」「跟誰對齊」「為何未」「跑偏」「失職」
- ✅ 改用：「請說明」「目前狀態」「想了解」「請查 X 並整理」

### 禁區 2：接受主管已知前提
從 tracking_files / 任務分派文件 + Phase 1 對話脈絡識別主管已知前提，**不再追問**：
- 某專案的架構/技術選型 = 上級已拍板（不問「為何這樣決定」）
- 某任務的優先序 = 主管已指示（不問「為何先做這個」）
- 某模組已整合完成（不問整合歷程）
- 某工作暫緩 = 主管已同意（不問「為何停」，但可問「何時切回」）

### 禁區 3：禁問 token / AI 成本
- ❌ 禁問：「你今天用了多少 token」「為什麼花這麼多錢」「AI 成本是不是太高」
- 例外：若 team-daily-fetcher 已標記某人 7d 滾動 ≥70%（🔴 高用量），可在卡片末尾加 **soft note**「本週 AI 用量已達 X% 配額，建議節制」，但不列為提問

### Phase 2 自我檢查 checklist
產卡前對每題跑一次：
- [ ] 不含「為什麼」「跟誰對齊」
- [ ] 不問 token / AI 成本（除非 soft note）
- [ ] 接受主管已知前提
- [ ] **可用 CC 查證**（題目出處可指向 git log / commit / spec.md / plan.md / tasks.md）
- [ ] 🔴 **對得到 tracked 卡關（相關性 gate）**：本題必須對應到 1.2 抽出的「最新一週逐人列」的 **🔴卡關 / 追蹤事項 / 達成率缺口** 其一；**若題目指向追蹤檔沒有的支線**（如某個不在表內的功能/雜務），則該題**必須明標 `scope 釐清`**（問「這是新指派/支線/前置？與 tracked 主項的優先序？」），不得當成一般進度題混入。

> ⚠️ **為何加這條**：原 checklist 只防「可不可查證」，不防「相不相關」。一個綁了 git commit、語氣溫和的問題可以通過全部 gate，卻在問追蹤檔根本沒列的支線（吃過虧：問 D029 支線、漏掉 tracked 的🔴磅單持久化）。**可查證 ≠ 對得準**；每張卡至少要有過半題目命中該成員的 tracked 卡關，否則回 Phase 1.2 重抽。

任一題失敗 → 重寫該題

---

## Phase 3：每位屬下產卡

對每位屬下產 1 張卡，每張卡 3-5 題。每題格式：

```markdown
#### Q{N}. {標題 — 用「目前狀態」「想了解」「請說明」開頭}
請查 {repo}: {file 1} / {file 2} / {file 3}，整理：
- {可查證項目 1}
- {可查證項目 2}
- {可查證項目 3}
```

### evidence_hint 三層穩定度（v2-5）
卡片 frontmatter 的 `evidence_hint` 應指向**穩定錨點**：

| 穩定度 | 形式 | 範例 |
|--------|------|------|
| ★★★（推薦） | git:`<commit_sha>:<path>#L<n>-L<m>` | `git:abc1234:src/foo.py#L10-L25` |
| ★★（可選） | commit hash only | `git:abc1234` |
| ★（退而求其次） | `<path>#L<n>` 不綁 commit | `tasks.md#L100` — 會漂移，禁用為 sole evidence |

---

## Phase 4：寫入卡片 md

**檔案路徑**：`daily_proposal/team_coaching_cards_{target_date}.md`

> ⚠️ **嚴格區分 reference cards**：此 skill 只寫 `daily_proposal/team_coaching_cards_*.md`，**永不**寫到 `daily_proposal/reference_cards/*.md`（reference cards 是手動鎖定的範本，不被自動覆寫）

### 衝突處理（v2-1 supersede chain）
若 `daily_proposal/team_coaching_cards_{target_date}.md` 已存在：
1. 讀現存檔，抽每張卡的 `card_id` + `card_version`
2. 若主管想**修改某張卡**：
   - 新卡 `card_version` = 舊卡 + 1
   - 新卡 `supersedes_card_id` = 舊卡 card_id
   - 舊卡 `superseded_by_card_id` = 新卡 card_id、`review_status` 改 `superseded`
3. 若主管想**新增一張卡**（如新加站區 manager）：直接 append，不動現有卡

### 檔案結構（multi-document YAML + markdown）

```markdown
---
# Bundle metadata
target_work_date: {date}
created_at: {ISO 8601}
created_by: {supervisor display_name}
file_type: runtime_cards   # 注意：reference 才標 reference_card_bundle
final_locked: false        # runtime cards 可被覆寫
delivery_method: outlook_reply
---

# 主管澄清問題卡 — {target_date} 盤點

> 使用方式：（複製本檔卡 1/2/3 的 markdown 段落 → 在 Outlook reply 屬下原日報 → 附 .md 檔）
>
> 三項禁區檢查（Phase 2 已過濾）

---

```yaml
# Card 1
card_id: <UUID v4 — 用 python -c "import uuid; print(uuid.uuid4())">
card_version: 1
target_work_date: {date}
employee:
  member_id: {config.team.members[].member_id}   # 對應 cockpit config 的成員 slug（穩定）
  name: {名字}
  email: {config.team.members[].email}            # 必填！send_coaching_cards 的 compose/reply-fallback
                                                  # 需要它做嚴格 email 比對；缺則該卡無法寄出
  display_name: {OM-XXXXX 名字}
  employee_code: {OM-XXXXX}
review_status: draft
sent_at: null
replied_at: null
review_thread_id: null
review_message_id: null
reply_message_id: null
supersedes_card_id: null
superseded_by_card_id: null
final_locked: false
managed_sections:
  - supervisor_qa
questions:
  - id: Q1
    title: {標題}
    evidence_hint: "{穩定度 ★★★ 的指向}"
```

## 卡 N — {名字}

### 主管看到的本週工作
- {date}：{摘要}
- ...

### 主管想了解的 N 件事

#### Q1. {標題}
{內容}

### 回覆建議
每題用 CC 查證後回 100-200 字，附上 commit hash + 檔案路徑佐證。
```

---

## Phase 5：主管 review

寫完檔後，**必須**問主管：

```
卡片已產出在 {daily_proposal_dir}/team_coaching_cards_{target_date}.md
- 卡 1（{成員 1}）：N 題
- 卡 2（{成員 2}）：N 題
- 卡 3（{成員 3}）：N 題

要修改任一題嗎？或全部 OK 寄出？
```

用 `AskUserQuestion`：
1. 全部 OK，寄出 → 進 Phase 6
2. 修改某張卡 → 主管指定卡 N Q M，重產該題（套 Phase 2 三禁區）→ 回 Phase 5
3. 砍掉某題 → 直接刪 → 回 Phase 5
4. 跳過寄出 → 結束 skill（卡片仍保留在檔案）

---

## Phase 6：reply 屬下原日報寄出

主管確認後，呼叫 send_coaching_cards.py：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_coaching_cards.py \
  {daily_proposal_dir}/team_coaching_cards_{target_date}.md \
  --mode reply
```

> `--mode reply`（預設）：用 Outlook Reply 屬下原日報，thread 自動串
> `--mode compose`（fallback）：找不到原日報時開新郵件
> `--auto-send`：直接發送（預設**只開草稿**，主管按發送）

腳本完成後：
- 卡片 frontmatter 自動更新：`review_status: sent`、`sent_at`、`review_thread_id`、`review_message_id`
- 跳出 Outlook 草稿視窗，主管檢查無誤後按發送

---

## 終止條件

- ✅ 6 個 task 全 completed
- ✅ 卡片 md 已寫入 daily_proposal/team_coaching_cards_{target_date}.md
- ✅ 主管確認寄出 OR 主動跳過
- ✅ 寄出時 frontmatter 已更新 review_status
