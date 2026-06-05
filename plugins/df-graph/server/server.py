"""df-graph MCP server（stdio）：通用的 Microsoft 365 / Graph 工具。

乾淨、無狀態、id-based —— 給任何 skill / 對話 / 自動化使用，不綁特定 skill。
信箱（讀/寄/回覆/轉發）+ 行事曆 CRUD。未來可在此擴 Teams / Files / Excel。

啟動（任一支援 MCP stdio 的 AI client 皆可用，路徑由安裝流程按各人填入）：
  uv run --with mcp --with msal python <path>/server.py
"""
import os
import sys

# 強化 import：不論啟動方式/cwd/symlink，都把本檔目錄放進 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
import tools

mcp = FastMCP("df-graph")


# ---- 信箱：讀 ----
@mcp.tool()
def mail_list_recent(days: int = 7, folder: str = "inbox") -> str:
    """List recent emails in a mail folder from the last N days, newest first.
    Returns JSON with each message's id; read a message with mail_get(message_id)."""
    return tools.mail_list_recent(days, folder)


@mcp.tool()
def mail_search(subject_prefix: str = "", body_query: str = "",
                days: int = 7, folder: str = "inbox") -> str:
    """Search emails by subject prefix (startswith) and/or body keyword. Returns JSON with ids."""
    return tools.mail_search(subject_prefix, body_query, days, folder)


@mcp.tool()
def mail_get(message_id: str, mode: str = "concise") -> str:
    """Read an email by id. mode="concise" (default) returns plain text (token-cheap);
    mode="full" returns the original HTML body. The body is wrapped in an untrusted-content
    fence (prompt-injection mitigation). Lists attachments if any."""
    return tools.mail_get(message_id, mode)


@mcp.tool()
def folder_list(parent_id: str = "", include_hidden: bool = False) -> str:
    """List mail folders with unread/total counts (JSON). parent_id empty = top level;
    pass a folder id or well-known name (e.g. inbox) to list its child folders."""
    return tools.folder_list(parent_id, include_hidden)


@mcp.tool()
def mail_download_attachment(message_id: str, attachment_id: str, dest_dir: str = "",
                            overwrite: bool = False) -> str:
    """Download one email attachment (ids from mail_get) to local disk; returns the saved path.
    dest_dir empty -> ~/Downloads/df-graph. Won't overwrite same-name files unless
    overwrite=True (otherwise auto-suffixes (1)/(2)...). fileAttachment only."""
    return tools.mail_download_attachment(message_id, attachment_id, dest_dir, overwrite)


# ---- 信箱：寫/寄 ----
@mcp.tool()
def mail_send(to: str, subject: str, body: str = "", cc: str = "", bcc: str = "",
              html: bool = True, attachments: str = "", body_file: str = "") -> str:
    """Send an email as the signed-in user. to/cc/bcc are comma-separated addresses.
    attachments: comma-separated local file paths (up to 150MB; large files auto-chunked).
    body_file: if body is empty, read the HTML body from this local (WSL) file path —
    use for large bodies so they don't pass through the conversation."""
    return tools.mail_send(to, subject, body, cc, bcc, html, attachments, body_file)


@mcp.tool()
def mail_draft(to: str, subject: str, body: str = "", cc: str = "", bcc: str = "",
               html: bool = True, attachments: str = "", body_file: str = "") -> str:
    """Create a draft in Drafts for you to review in Outlook before sending (does NOT send).
    Same params as mail_send (incl. body_file for large bodies via a local file path).
    Returns draft_id + web_link."""
    return tools.mail_draft(to, subject, body, cc, bcc, html, attachments, body_file)


@mcp.tool()
def mail_reply(message_id: str, body: str, reply_all: bool = False) -> str:
    """Reply (or reply-all) to an email by id with the given text."""
    return tools.mail_reply(message_id, body, reply_all)


@mcp.tool()
def mail_reply_draft(message_id: str, body: str = "", reply_all: bool = False,
                     body_file: str = "") -> str:
    """Create a REPLY DRAFT (threaded, keeps the original quoted) in Drafts for you to review
    in Outlook before sending — does NOT send. Unlike mail_reply (which sends immediately),
    this preserves supervisor review. body (e.g. card HTML) is prepended above the quote;
    body_file: if body is empty, read the HTML body from this local (WSL) file path."""
    return tools.mail_reply_draft(message_id, body, reply_all, body_file)


@mcp.tool()
def mail_forward(message_id: str, to: str, comment: str = "") -> str:
    """Forward an email by id to comma-separated recipients, with an optional comment."""
    return tools.mail_forward(message_id, to, comment)


# ---- 信箱：整理 ----
@mcp.tool()
def mail_mark_read(message_id: str, read: bool = True) -> str:
    """Mark an email read (read=True) or unread (read=False). The message id is unchanged."""
    return tools.mail_mark_read(message_id, read)


@mcp.tool()
def mail_move(message_id: str, destination: str) -> str:
    """Move an email to a folder (well-known name like inbox/archive/deleteditems, or a folder id).
    WARNING: moving assigns a NEW message id; the returned new_message_id replaces the old one."""
    return tools.mail_move(message_id, destination)


@mcp.tool()
def mail_delete(message_id: str, permanent: bool = False) -> str:
    """Delete an email. Default soft-delete moves it to Deleted Items (recoverable; returns the
    new id for undo). permanent=True uses HTTP DELETE (still recoverable in Deleted Items)."""
    return tools.mail_delete(message_id, permanent)


# ---- 行事曆 ----
@mcp.tool()
def calendar_list(days_ahead: int = 7, days_back: int = 0, tz: str = "") -> str:
    """List calendar events from -days_back to +days_ahead. Times in tz (default Asia/Taipei).
    Returns events with id; use calendar_get(event_id) for the full agenda."""
    return tools.calendar_list(days_ahead, days_back, tz)


@mcp.tool()
def calendar_get(event_id: str, tz: str = "") -> str:
    """Read one event by id (from calendar_list): full attendees, body/agenda, Teams join URL.
    Times in tz (default Asia/Taipei)."""
    return tools.calendar_get(event_id, tz)


@mcp.tool()
def calendar_create(subject: str, start_iso: str, end_iso: str,
                    body: str = "", location: str = "", attendees: str = "",
                    online_meeting: bool = False) -> str:
    """Create a calendar event. start_iso/end_iso UTC ISO-8601 like 2026-06-04T09:00:00.
    attendees comma-separated addresses. online_meeting=True adds a Teams link (returns join_url)."""
    return tools.calendar_create(subject, start_iso, end_iso, body, location, attendees, online_meeting)


@mcp.tool()
def calendar_find_times(attendees: str, duration_minutes: int = 30,
                        window_start_iso: str = "", window_end_iso: str = "",
                        max_suggestions: int = 5, tz: str = "") -> str:
    """Find time slots when all attendees are free (Microsoft findMeetingTimes).
    attendees comma-separated addresses; window UTC ISO-8601, defaults to now..+5 days.
    Suggestions returned in tz (default Asia/Taipei)."""
    return tools.calendar_find_times(attendees, duration_minutes,
                                     window_start_iso, window_end_iso, max_suggestions, tz)


@mcp.tool()
def calendar_get_schedule(attendees: str, start_iso: str = "", end_iso: str = "",
                          interval_minutes: int = 30, tz: str = "") -> str:
    """Query free/busy for people (Microsoft getSchedule). attendees comma-separated SMTP;
    window UTC ISO-8601, defaults to now..+1 day. Returns each person's busy/tentative/oof
    blocks in tz (default Asia/Taipei)."""
    return tools.calendar_get_schedule(attendees, start_iso, end_iso, interval_minutes, tz)


@mcp.tool()
def calendar_rsvp(event_id: str, response: str, comment: str = "",
                  send_response: bool = True) -> str:
    """Respond to a meeting invite. response must be accept / decline / tentative."""
    return tools.calendar_rsvp(event_id, response, comment, send_response)


@mcp.tool()
def calendar_forward(event_id: str, to: str, comment: str = "") -> str:
    """Forward a meeting invite to others. to: comma-separated addresses."""
    return tools.calendar_forward(event_id, to, comment)


@mcp.tool()
def calendar_update(event_id: str, subject: str = "", start_iso: str = "",
                    end_iso: str = "", body: str = "", location: str = "") -> str:
    """Update fields of an existing event (id from calendar_list). Empty args are ignored."""
    return tools.calendar_update(event_id, subject, start_iso, end_iso, body, location)


@mcp.tool()
def calendar_delete(event_id: str) -> str:
    """Delete a calendar event by id (from calendar_list)."""
    return tools.calendar_delete(event_id)


# ---- 人員 ----
@mcp.tool()
def resolve_person(query: str, limit: int = 10) -> str:
    """Resolve a name (or partial / email) to [{name, email}] for the signed-in user.
    Searches your relevant people first, then the directory as a fallback."""
    return tools.resolve_person(query, limit)


if __name__ == "__main__":
    mcp.run()
