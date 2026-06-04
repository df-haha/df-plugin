# df-graph

Microsoft 365 / Graph MCP server（信箱 + 行事曆 + 人員）。純雲端 Microsoft Graph API、OS 無關、
每人一次 device-code 登入即可使用 —— 用來取代需要 Windows + Outlook Desktop 的 COM 方案
（`outlook-local`）。設計原則：**無狀態、id-based、讀取零淨化（`mode="full"`）**。

## 上游與貢獻者（provenance）

- **作者／上游 repo**：宗霖（`dfroy00/df-graph`，private）。本 plugin 內 `server/` 為該 repo 的
  **vendored 副本**（不含 `test_tools.py` / `uv.lock` / `.github`），方便透過 marketplace 散佈、
  免去各人 clone private repo。
- **同步紀律**：對 `server/*.py` 的任何修改（例如新增工具）須**回報上游宗霖**避免長期分叉；
  上游有更新時，重新 vendor 對應檔案。

## 安裝

啟用 plugin 後，跑 onboarding skill：

```
/df-graph-setup
```

它會（1）確認本機有 `uv`，（2）以 **user-scope** 註冊 MCP server：

```
claude mcp add df-graph --scope user -- \
  uv run --with mcp --with msal python "${CLAUDE_PLUGIN_ROOT}/server/server.py"
```

> ⚠️ **刻意不透過 plugin `.mcp.json` 註冊**。user-scope 註冊讓工具名保持乾淨的
> `mcp__df-graph__*`；若走 plugin `.mcp.json` 會變成 `mcp__plugin_df-graph_df-graph__*`。
> 與既有 om-daily-work-log 安裝 outlook-local 的前例一致。

（3）跑 `server/login.py`（device-code，瀏覽器登入一次，token 存 `~/.df-graph/`），
（4）跑 `server/selftest.py`（真實 Graph 唯讀檢查），（5）提醒重啟讓工具生效。

## 權限 scope（4 個，員工可自批）

預設 scope（本租戶實測員工可自行同意，**不需 IT admin consent**）：

| scope | 用途 |
|---|---|
| `Mail.ReadWrite` | 讀信、建草稿、移動、標記 |
| `Mail.Send` | 寄信 |
| `Calendars.ReadWrite` | 行事曆 CRUD |
| `Calendars.ReadWrite.Shared` | `findMeetingTimes` / `getSchedule` 讀同事 free/busy |

以下 scope **需 IT admin consent，故預設不掛**（掛了會讓整個登入卡管理員核准）：
`People.Read`（`resolve_person` 姓名→email）、`User.ReadBasic.All`、`Files.ReadWrite`（OneDrive）。
待 IT 核准後，於 `server/auth.py` 把需要的項目加回 `SCOPES` 並重跑 `login.py`。

## 治理註記

- Azure app 的 `client_id` / `tenant_id` 為**非機密**值，目前掛在**宗霖個人帳號**下。
  現階段自用 OK；**正式鋪設建議由 IT 接手列管 app**。
- 三項皆可用環境變數覆寫，方便換 tenant / 換 IT 列管的 app：
  `DF_GRAPH_CLIENT_ID`、`DF_GRAPH_TENANT_ID`、`DF_GRAPH_AUTHORITY`、`DF_GRAPH_SCOPES`。
- token 快取只存本機（`~/.df-graph/`），原子寫入、權限 600。

## 工具一覽

- **信箱（讀）**：`mail_list_recent` / `mail_search` / `mail_get` / `folder_list` / `mail_download_attachment`
- **信箱（寫）**：`mail_send` / `mail_draft` / `mail_reply` / `mail_forward`
- **信箱（整理）**：`mail_mark_read` / `mail_move` / `mail_delete`
- **行事曆**：`calendar_list/get/create/update/delete` / `calendar_find_times` / `calendar_get_schedule` / `calendar_rsvp` / `calendar_forward`
- **人員**：`resolve_person`（需 People.Read，預設未掛）

讀信用 `mail_get(mode="concise")` 省 token；要保留 HTML（含 HTML comment marker）時用 `mode="full"`。
`folder` 參數只吃 Graph well-known name（inbox/drafts/sentitems…）或 folder id，**不吃**顯示路徑；
要對非 well-known 資料夾操作，先用 `folder_list` 把顯示名 resolve 成 id。
