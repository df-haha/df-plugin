# LINE 送出工具序列（依原始碼推論）

> 依 line-desktop-mcp v3.3.5 原始碼推論，待 Phase 0 0.3 實機確認。

## 工具序列（普通純文字訊息）

### 1. 目標身分預檢（選用但建議）

**工具**: `check_line_send_target`
**參數**: `{ chatName, chatType: "direct"|"group", expectedChatRef? }`
**回傳**: `{ status: "IDENTITY_UNIQUE"|"BLOCKED"|"IDENTITY_BOUND_REQUIRES_UI", chatRef, kind }`

- 只做本機身分唯一性查詢，不操作 GUI、不送出。
- v1 實作只處理 `IDENTITY_UNIQUE`；`IDENTITY_BOUND_REQUIRES_UI`（未聊天好友同名衝突）和 `BLOCKED` 一律視為失敗。
- `chatType` 是**必要參數**，預設為 `direct`。代理的 `line_reply` target 必須攜帶 `chat_type`。

### 2. 送出並取得本機回執

**工具**: `send_message_auto`
**參數**: `{ chatName, message, chatType: "direct"|"group", idempotencyKey, expectedChatRef?, expectedOwnSenderRef? }`
**成功回傳**: `{ status: "RECORDED_LOCAL", operationId, sourceRef, sourceMessageId, sourceTimestamp, reused }`
**失敗回傳**: 拋出例外，code 為以下之一：

| 錯誤碼 | 意義 | 是否可能已送出 |
|---|---|---|
| `LINE_SEND_UNCERTAIN` | 已按 Return 但無法確認本機紀錄 | 可能 |
| `LINE_DRAFT_PENDING` | 草稿已暫存但未按 Return | 否 |
| `LINE_SEND_FAILED` | 未嘗試按 Return | 否 |
| `LINE_SEND_TIMEOUT` | 操作逾時 | 依是否已按 Return |
| `LINE_SEND_KEY_CONFLICT` | 同一 idempotencyKey 對應不同內文 | 否 |

### 3. 送出結果不確定時：只查詢，不重送

**絕不重新呼叫 `send_message_auto`**。原因：上游防重送日誌
（`%LOCALAPPDATA%/line-desktop-mcp/sends/<hash>.json`）以
`sha256(ownSenderRef|chatRef|idempotencyKey)` 為鍵。若日誌狀態為
`NOT_SENT` 或日誌缺失（子程序在 writeRecord 前當機），
`send_message_auto` 的重播**會發起新的送出**（原始碼 line-plain-send.mjs:231+）。

代理的 `action_status` 改為：
- 用 `get_line_local_messages`（或 `verify_line_message`）讀取目標對話的近期訊息。
- 比對 `senderRef === ownSenderRef && text === message && sourceTimestamp ∈ [committed_at - 5s, committed_at + window]`。
- 這與上游 `matchReceipt`（line-plain-send.mjs:41-54）的邏輯相同。
- 找到精確匹配 → 標 `sent`。找不到 → 標 `failed`。查詢失敗 → 維持 `uncertain`。

## 冪等性

- 代理的 `nonce`（`newNonce()`）作為 `idempotencyKey` 傳給 `send_message_auto`。
- 上游日誌保證同一 key 不會派送第二次（前提：日誌已寫入且狀態非 `NOT_SENT`）。
- 代理的 `commit_action` 使用 `rename(pending→committing)` 確保只有一個請求能取得所有權。
- 如果 commit 在 adapter 呼叫後、done 寫入前當機，啟動恢復將標為 `uncertain`。

## 上游綁定流程（expectedChatRef）

- 代理在 `prepare_action` 時接收 `chat_ref`（`chat:<24hex>`），對應 LINE 對話的穩定 ID。
- `send_message_auto` 的 `expectedChatRef` 可選參數在送出前驗證目標身分未變。
- 代理一律傳入 `expectedChatRef`，增加安全性。
- `expectedOwnSenderRef` 綁定目前登入帳號；代理目前不要求使用者提供（可從 `check_line_send_target` 回傳取得，但 v1 先不做此最佳化）。

## 回執欄位

成功回傳的 `RECORDED_LOCAL` 包含：
- `sourceRef`: `message:<24hex>` 格式，本機訊息 ID。
- `sourceMessageId`: LINE 內部訊息 ID。
- `sourceTimestamp`: 紀錄時間戳（ms）。
- `sourceStatus`: 訊息狀態（0 = 正常）。
- `reused`: `true` 時表示這是先前同一 key 的重播結果，未發送新訊息。

## 限制

- `message` 長度 1–10000 字元，不含 NUL。
- `chatName` 最長 200 字元。
- 不可 NFC 正規化內容——回執比對用 `m.text === message`（嚴格相等）。
- 操作截止時間 30 秒（25 秒輸入 + 5 秒回執驗證）。

## 引用回覆（UNVERIFIED, blocked by 0.3）

引用回覆需要完整的 `source` 物件（`sourceRef`、`text`、`sender`、`date`、`time`）
與短效一次性 `sourceToken`。流程更複雜，v1 暫不支援。

## 不支援

- 檔案、貼圖、收回訊息。
- 跨對話轉發。
- 群組 @mention（需要 UI 選取真正的 mention token）。
