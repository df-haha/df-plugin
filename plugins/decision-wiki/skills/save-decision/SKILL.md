---
name: save-decision
description: Use when a high-value repository decision has been explicitly settled, or when the user asks to 存決策, save decision, 記這個決策, 開決策卡, or 翻案.
---

# Save Decision

## Core rule

Record one explicitly settled architecture（架構）, behavior（行為）, or config（設定） decision as one reviewable Git（版本控制） draft（草稿）. A proposed draft has no decision authority.

## HARD STOP — `NOT_SETTLED`

Evaluate this predicate（判定條件） before generating an ID（識別碼） or performing any write:

If any current request or current-session statement describes the decision's current state as **undecided, unselected, still discussing, tentative, or options open**, that fact controls—even when the same prompt names an option or commands “write,” “formalize,” or “commit” it. Stop immediately. Permit only read-only Git status（版本控制狀態） and contract/index checks. Generate no ID; create no draft or formal card; change no index or status; run no validator（驗證器）; make no commit（提交）.

Return exactly:

```text
NOT_SETTLED
Open choice: <concise unresolved choice>
Files changed: none
Commit: none
```

Proceed only with actual human selection evidence: an affirmative statement from a person with decision authority that selects the outcome. Never invent an approver, source, rationale, benchmark, metric, or other decision number.

| Red flag or rationalization（合理化藉口） | Required interpretation |
|---|---|
| “Write, formalize, or commit option X now” or naming X | File-operation pressure is not selection evidence. |
| Deadline, manager request, or handoff（交接） urgency | Pressure cannot settle an open choice. |
| “Don't ask” or “we can correct it later” | Neither erases an explicit unsettled statement. |

## Workflow（工作流程）

### 1. Gate the repository and decision

1. Locate the Git root, inspect its dirty state（未提交狀態）, and preserve unrelated work.
2. On every invocation, re-read repository instructions, `docs/decisions/README.md`, and `docs/decisions/INDEX.md`. They are the runtime SSOT（執行期單一真相來源） for fields, lifecycle, sources, index shape, and validation. Never infer those from this skill.
3. If either decision contract file is missing, stop and invoke `setup-decision-wiki`; never create a partial Wiki（知識庫）.
4. If `NOT_SETTLED` did not fire, verify direct human evidence explicitly selects one high-value outcome. Split multiple settled decisions into separate runs.

### 2. Build a fact ledger before drafting

Build this exact fact ledger（事實帳本） before writing a file:

```text
selected_outcome:
supersedes:
depends_on:
conflicts_with:
related_to:
source_excerpt_bytes:
locator:
decision_time:
capture_time:
background:
alternatives_and_rejection_reasons:
rationale:
consequences:
outcomes:
```

Populate slots only from exact user text, read-only repository evidence, or the actual system clock（系統時鐘） for time. Domain knowledge（領域知識）, likely benefits, and plausible implications are not evidence. Missing narrative slots—background, alternatives and rejection reasons, rationale, consequences, or outcomes—must render as literal `Not recorded` or a repository-mandated equivalent.

Render narrative as a mechanical projection（機械投影）, never as prose completion:

- Background = `background`, verbatim; empty = `Not recorded`.
- Alternatives and rejection reasons = `alternatives_and_rejection_reasons`, verbatim; empty = `Not recorded`.
- Final decision = `selected_outcome`, verbatim; rationale = `rationale`, verbatim or `Not recorded`.
- Consequences = `consequences`, verbatim; empty = `Not recorded`.
- Later outcomes = `outcomes`, verbatim; empty = `Not recorded`.

A relationship target, locator, old-card title, or selected outcome does not supply unstated background, rejection reasons, rationale, consequences, or operational requirements. Repository evidence may populate only the exact fact it states; do not sentence-expand it.

For the four relationship lists, copy every explicitly human-confirmed supported edge into its named list. Keep the same target in multiple lists when multiple relation types were confirmed; never infer, merge, or deduplicate across relation types. Semantic similarity（語意相似度） populates no field.

### 3. Render one exact proposed draft

1. Use the actual clock for the UTC（世界協調時間） ID and `capture_time`. Use an explicit human decision time when supplied; otherwise use the actual current time. Never invent or round timestamps.
2. Copy the current README's exact frontmatter（檔頭） shape. Render all four ledger relationship lists in its required inline-array（行內陣列） syntax—for example, `supersedes: [DEC-...]` and `related_to: [DEC-...]`, never block lists.
3. If the user labels a span as source, excerpt, or quote, `source_excerpt_bytes` equals only those bytes. Its visible blockquote contains only those bytes with the minimum sensitive substring replaced by `[REDACTED]`; never prepend or append a decision summary, approval statement, locator, or explanation. Keep `locator` only in source metadata（中繼資料） and SHA-256（安全雜湊演算法） hash exactly the visible redacted text under the repository normalization rule.
4. Render body sections from the mechanical projection above, then source metadata plus `source_excerpt_bytes`. Never retain a full transcript（逐字紀錄） or invent provenance（溯源資訊）. Create only `docs/decisions/_draft/<id>.md` with `status: proposed`.
5. A reversal creates a new proposed card. Never revise the old decision body; after acceptance, change only its lifecycle status to `superseded`.

### 4. Validate before preview

After writing the proposed draft and its confirmed relationships, run the validator named by the current repository contract against the draft/corpus. Fix every draft-caused mechanical failure, including exact inline-array syntax, and rerun until it exits `0`. Never preview an invalid draft. If validation is unavailable, remains nonzero, or exposes only out-of-scope existing failures, stop with the exact output; do not edit unrelated files or ask for acceptance.

### 5. Preview verbatim and confirm sequentially

Only after pre-preview validation exits `0`, show the entire draft file verbatim（逐字） in one fenced code block（圍欄程式碼區塊）, from the opening `---` through the final body line. Never replace any content with “Body complete,” an ellipsis, a summary, or omitted sections.

Use separate questions and wait for a separate user turn（使用者回合） after each applicable gate:

1. **Gate 1（關卡一） — exact draft acceptance:** ask only whether the displayed exact draft is accepted for formal landing. Make no formal change before an explicit yes.
2. **Gate 2 — sensitive-redaction safety:** only after Gate 1 passes, and only for sensitive sources, ask separately whether the displayed redaction and retained provenance are safe. Make no formal change before an explicit yes.

Silence, pressure to skip questions, and an earlier general request to formalize satisfy neither gate.

If Gate 1 or Gate 2 is rejected, immediately remove only the proposed draft created by this run; leave the old card, `INDEX.md`, and every unrelated file byte-for-byte unchanged. Show `git status --short` proving the rejected draft and its locator are no longer present, then stop. If the user requests revision instead of acceptance, remove the rejected version first, rebuild only from the newly confirmed facts, rerun validation, and restart both applicable gates from the full preview. Never leave rejected sensitive content or provenance on disk between attempts.

### 6. Land, validate, then ask about commit

After all applicable gates pass, change the new card to `active`, move it out of `_draft/`, add exactly one `./<id>.md` index row, and—when superseding—change only the old card's status. Preserve the old accepted body byte-for-byte.

Run the repository validator again after formal landing. On a nonzero result, stop, report the exact failure, keep all changes uncommitted, and do not ask about commit. On exit `0`, review the complete diff（差異） and prove unrelated work is excluded.

Only then start **Gate 3 — commit authorization** in a new, separate question: ask whether to commit exactly the new card, the necessary old-card status change, and `docs/decisions/INDEX.md`. Never combine Gate 3 with draft acceptance or sensitive-redaction confirmation, and never treat an earlier commit demand as this post-validation authorization.

If Gate 3 passes, commit only that exact scope. Never commit or push（推送） automatically, include unrelated files, or push as part of this workflow.
