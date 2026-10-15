# Upstream: line-desktop-mcp

## Baseline

| Field | Value |
|---|---|
| Repository | https://github.com/bensonmaxai/line-desktop-mcp |
| Tag | v3.3.5 |
| Commit | `555c7c72a7f52e86bce26513fd7214b489614aed` |
| Import date | 2026-10-08 |

## Excluded from vendor

- `.git/` — version control history (patches are stored separately)
- `doc_media/` — ~65 MB screenshots, not needed at runtime
- `docs/` — all except `THIRD_PARTY.md` (license attributions)
- `node_modules/` — installed at setup time via `npm ci --ignore-scripts`

No upstream tests depend on excluded `docs/` files.

## MCP tool snapshot (v3.3.5 + patches)

From `src/extensions/line-extensions.mjs` (31 tools; our patches add no new tools):

```
check_line_send_target
clear_line_draft
confirm_line_chat_view
confirm_line_reply_source_target
copy_line_message
export_line_chat_history
get_line_capabilities
get_line_chat_messages
get_line_draft
get_line_local_messages
get_line_poll_state
get_line_reply_source_target
get_line_status
get_line_ui_state
get_line_workflow
list_line_recent_chats
open_line_chat
open_line_chat_feature
prepare_line_direct_chat
prepare_line_group_chat
prepare_line_send_target
prepare_line_workflow
search_line_chat_messages
send_file_manual
send_message_auto
send_message_manual
set_line_draft
stage_line_forward
stage_line_reply
translate_line_message
verify_line_message
```

## Patches

Applied in order; each patch depends on the previous. All are in `patches/`.

### 0001-trusted-builds.patch

**Purpose**: `LINE_MCP_TRUSTED_BUILDS` environment variable — local trust list
that unions with upstream `line_client_builds.json` to support LINE versions not
yet reviewed by the upstream author.

**Files**:
- `src/extensions/python/line_client_compatibility.py` — `_read_trusted_builds()`, `_merge_builds()`, modified `verify_client_build()`
- `src/extensions/line-client-status.mjs` — accept `TRUSTED_BUILDS_INVALID` exit code
- `src/extensions/line-local-reader.mjs` — `TRUSTED_BUILDS_INVALID` in both error whitelists
- `test/python/test_daily_agenda_trusted_builds.py` — 21 tests
- `test/daily-agenda-trusted-builds.test.mjs` — 4 tests

**Behavior**: env unset = upstream unchanged. Set to valid absolute path JSON
(`{"builds":[{"version","bytes","sha256",...}]}`) = union. Invalid file/format/
conflicts = `TRUSTED_BUILDS_INVALID` (fail-closed, no fallback to upstream-only).

### 0002-main-database.patch

**Purpose**: `LINE_MCP_MAIN_DATABASE` environment variable — explicit main
database filename selection for multi-account LINE installations.

**Files**:
- `src/extensions/python/line_main_database.py` — new module with `select_main_database()`
- `src/extensions/python/line-reader.py` — uses `select_main_database()` instead of inline filter
- `src/extensions/line-local-reader.mjs` — `MAIN_DATABASE_INVALID`, `MAIN_DATABASE_NOT_FOUND` in both error whitelists
- `test/python/test_daily_agenda_main_database.py` — 19 tests
- `test/daily-agenda-main-database.test.mjs` — 4 tests

**Behavior**: env unset = upstream unchanged (single non-album/keep/chatStats DB
required, else AMBIGUOUS). Set = must be bare filename (no `/`, `\`, `..`,
absolute paths, NUL, control chars); must not start with `album_`, `keep_`,
`chatStats_`; must case-sensitively match a file in the filtered list.

### 0003-recent-chats-offset.patch

**Purpose**: Optional `offset` parameter for `recentChats` pagination, enabling
retrieval of all conversations when total exceeds the 50-per-request limit.

**Files**:
- `src/extensions/python/line_scoped_core.py` — `validate_recent_scope()` + `read_recent_chats()` offset support
- `src/extensions/line-local-reader.mjs` — `validateRecentScope()` offset support
- `test/python/test_daily_agenda_recent_offset.py` — 12 tests
- `test/daily-agenda-recent-offset.test.mjs` — 7 tests

**Behavior**: offset omitted = upstream unchanged (default 0). Set to integer
0..999 = `chats[offset:offset+limit]`, `hasMore = len(chats) > offset+limit`.
Not exposed in MCP tool schema (`additionalProperties: false` rejects it from
external callers); only available to direct-import callers (our collector).

## Reapply patches to a fresh upstream

```bash
git clone https://github.com/bensonmaxai/line-desktop-mcp.git
cd line-desktop-mcp
git checkout v3.3.5   # or the target upgrade tag
git apply patches/0001-trusted-builds.patch
git apply patches/0002-main-database.patch
git apply patches/0003-recent-chats-offset.patch
npm ci --ignore-scripts
npm test
LINE_MCP_PYTHON=/path/to/python.exe npm run test:python  # Windows only
```

## Upgrade security diff checklist

When upgrading to a new upstream version, human review is required on these files
before accepting the merge. Changes to any of these may affect trust boundaries,
data access scope, or the integrity of our patches:

1. **Version verification** — `src/extensions/python/line_client_compatibility.py`, `src/extensions/python/line_client_builds.json`, `src/extensions/python/line-reader-status.py`, `src/extensions/line-client-status.mjs`
2. **Database selection** — `src/extensions/python/line-reader.py` (the `run()` function's database glob/filter logic)
3. **Memory/key access** — `src/extensions/python/line_session_locator.py`, `src/extensions/python/line_encrypted_snapshot.py`
4. **Scope validation & result projection** — `src/extensions/python/line_scoped_core.py`, `src/extensions/line-local-reader.mjs` (both `validateRecentScope` and `readLocalLineRecentChats`)
5. **Send/automation modules** — `src/extensions/line-plain-send.mjs`, `src/automation/`, `src/extensions/line-forward-*.mjs` (any new send capability)
6. **Error code whitelists** — `src/extensions/line-local-reader.mjs` `allowedErrors` and `allowed` sets (new codes must be evaluated)
7. **MCP tool schema** — `src/extensions/line-extensions.mjs` tool descriptors (new tools, changed parameters)
8. **Dependencies** — `package.json`, `npm-shrinkwrap.json` (new or changed deps)
