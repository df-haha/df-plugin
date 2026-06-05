#!/usr/bin/env python3
"""
週報郵件 payload 產生器（df-graph 版，取代舊 Outlook COM）。
讀取週報 markdown → 轉成郵件 HTML → 寫到本機（WSL）暫存檔 →
emit JSON payload 給 skill；由 agent 呼叫 mcp__df-graph__mail_draft(body_file=...) 建草稿。
大型 HTML 內文走 body_file、不經對話 token 流。stdout 只輸出 payload JSON；進度走 stderr。

Usage:
    python3 send_weekly_report_email.py weekly_reports/weekly_report_2026-W16.md
    python3 send_weekly_report_email.py <md_path> --to manager@company.com

收件者優先序：--to 參數 > config.manager_email > config.outlook_email
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "daily-work-log" / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def extract_week_from_filename(filepath: str) -> str:
    m = re.search(r"(\d{4})-W(\d{2})", filepath)
    if m:
        return f"{m.group(1)} 第 {int(m.group(2))} 週"
    return ""


# ────────────────────────────────────────────────────────────────
# Markdown → HTML（簡版，只支援週報會用到的語法）
# ────────────────────────────────────────────────────────────────

def md_to_html(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    in_list = False
    in_code = False
    in_lock_zone = False
    in_paragraph: list[str] = []

    def flush_paragraph():
        nonlocal in_paragraph
        if in_paragraph:
            text = " ".join(in_paragraph).strip()
            if text:
                out.append(f"<p>{_inline(text)}</p>")
            in_paragraph = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()

        # 處理 HTML 註解 → 判斷 Lock 區
        comment_match = re.match(r"^\s*<!--\s*(.+?)\s*-->\s*$", line)
        if comment_match:
            comment = comment_match.group(1)
            if "LOCK" in comment.upper() and "START" in comment.upper() or "請勿修改" in comment or "AI 觀察" in comment:
                in_lock_zone = True
                flush_paragraph()
                close_list()
                out.append(
                    '<div style="background: #FFF9C4; border-left: 4px solid #F9A825; '
                    'padding: 12px 16px; margin: 16px 0; border-radius: 4px;">'
                    '<div style="font-size: 12px; color: #F57F17; font-weight: bold; margin-bottom: 8px;">'
                    '🔒 AI 觀察原文（請勿修改）</div>'
                )
                continue
            if "LOCK" in comment.upper() and "END" in comment.upper() or "補充" in comment or "以下由" in comment:
                flush_paragraph()
                close_list()
                if in_lock_zone:
                    out.append("</div>")
                    in_lock_zone = False
                continue
            # 其他註解忽略
            continue

        # Code fence
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append(
                    '<pre style="background: #f5f5f5; border: 1px solid #ddd; '
                    'padding: 10px; overflow-x: auto; font-family: Consolas, monospace; font-size: 13px;">'
                )
                in_code = True
            continue
        if in_code:
            out.append(html.escape(line))
            continue

        stripped = line.strip()

        # 分隔線
        if stripped == "---":
            flush_paragraph()
            close_list()
            out.append('<hr style="border: none; border-top: 1px solid #ddd; margin: 16px 0;">')
            continue

        # 空行
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        # Heading
        h_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if h_match:
            flush_paragraph()
            close_list()
            level = len(h_match.group(1))
            text = _inline(h_match.group(2))
            color_map = {1: "#1B5E20", 2: "#2E7D32", 3: "#1565C0", 4: "#37474F"}
            color = color_map.get(level, "#333")
            size_map = {1: "22px", 2: "18px", 3: "15px", 4: "14px"}
            size = size_map.get(level, "14px")
            border = f"border-left: 4px solid {color}; padding-left: 10px;" if level <= 2 else ""
            out.append(
                f'<h{level} style="color: {color}; font-size: {size}; margin-top: 20px; {border}">{text}</h{level}>'
            )
            continue

        # Blockquote
        if stripped.startswith("> "):
            flush_paragraph()
            close_list()
            out.append(
                f'<blockquote style="border-left: 3px solid #78909C; padding: 6px 12px; '
                f'margin: 8px 0; color: #455A64; background: #ECEFF1;">{_inline(stripped[2:])}</blockquote>'
            )
            continue

        # List item
        li_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if li_match:
            flush_paragraph()
            if not in_list:
                out.append('<ul style="margin: 6px 0; padding-left: 24px;">')
                in_list = True
            out.append(f"  <li>{_inline(li_match.group(1))}</li>")
            continue

        # Paragraph
        close_list()
        in_paragraph.append(stripped)

    flush_paragraph()
    close_list()
    if in_lock_zone:
        out.append("</div>")
    if in_code:
        out.append("</pre>")

    return "\n".join(out)


def _inline(text: str) -> str:
    # 先 escape 再還原我們支援的 inline 語法
    t = html.escape(text)
    # **bold**
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    # `code`
    t = re.sub(
        r"`([^`]+?)`",
        r'<code style="background: #f5f5f5; padding: 1px 4px; border-radius: 3px; font-family: Consolas, monospace;">\1</code>',
        t,
    )
    return t


# ────────────────────────────────────────────────────────────────
# WSL temp file writer
# ────────────────────────────────────────────────────────────────

def write_body_file(html_body: str, week_tag: str) -> Path:
    """把 HTML 內文寫到本機（WSL）暫存檔，回傳路徑。
    供 df-graph mail_draft 的 body_file 讀取——大型內文不經對話 token 流。
    一律用 WSL 暫存目錄（tempfile），絕不寫 C:\\temp / /mnt/c。"""
    tmp_dir = Path(tempfile.gettempdir()) / "daily-work-log"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_tag = re.sub(r"[^0-9A-Za-z_-]", "_", week_tag) or "latest"
    body_file = tmp_dir / f"weekly_report_email_{safe_tag}.html"
    body_file.write_text(html_body, encoding="utf-8")
    return body_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("md_file", help="週報 md 檔路徑")
    parser.add_argument("--to", help="覆寫收件者 email")
    args = parser.parse_args()

    md_path = Path(args.md_file)
    if not md_path.exists():
        print(f"[ERROR] 檔案不存在：{args.md_file}", file=sys.stderr)
        return 1

    # 收件者優先序
    to_email = args.to or ""
    if not to_email:
        cfg = load_config()
        to_email = cfg.get("manager_email") or cfg.get("outlook_email") or ""

    if not to_email:
        print(
            "未設定主管收件者。請用 --to 指定，或在 "
            f"{CONFIG_PATH} 設定 manager_email",
            file=sys.stderr,
        )
        return 0

    md_content = md_path.read_text(encoding="utf-8")
    week_str = extract_week_from_filename(str(md_path))
    subject = f"週工作報告 {week_str}" if week_str else "週工作報告"

    # 轉換為 HTML，寫到本機暫存檔（大型內文走 body_file，不經對話）
    html_body = md_to_html(md_content)
    # derive a safe tag from the filename (e.g. "2026-W16")
    week_match = re.search(r"(\d{4}-W\d{2})", str(md_path))
    week_tag = week_match.group(1) if week_match else "latest"
    body_file = write_body_file(html_body, week_tag)

    # 輸出 payload 給 skill：由 agent 呼叫 df-graph mail_draft 建草稿（不自動寄出）
    #   mcp__df-graph__mail_draft(to=<to>, subject=<subject>,
    #                             body_file=<body_file>, attachments=<attachment>)
    payload = {
        "action": "mail_draft",
        "to": to_email,
        "subject": subject,
        "body_file": str(body_file),
        "attachments": str(md_path.resolve()),
    }
    print(f"[INFO] 郵件標題：{subject}", file=sys.stderr)
    print(f"[INFO] 收件者：{to_email}", file=sys.stderr)
    print(f"[INFO] 內文暫存：{body_file}", file=sys.stderr)
    print(f"[INFO] 附件：{md_path.name}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
