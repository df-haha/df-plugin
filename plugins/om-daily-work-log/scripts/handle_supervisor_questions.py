#!/usr/bin/env python3
"""
handle_supervisor_questions.py — 屬下端：偵測主管 reply 的「澄清問題卡」

流程：
  1. 接 target_date（屬下今天）→ 算 previous_workday
  2. 用 Outlook COM 搜屬下「寄件備份」中 subject = '每日工作報告 {previous_workday}' 的最新郵件
  3. 用該郵件 ConversationID 找 Inbox 中後續的 Reply（主管寄的）
  4. 抽 reply 的 .md 附件 → fallback 到 reply body 解析問題
  5. 輸出 JSON 給 om-daily-work-log skill 用

Usage:
  python3 handle_supervisor_questions.py 2026-05-08
  python3 handle_supervisor_questions.py 2026-05-08 --output-json

  python3 handle_supervisor_questions.py 2026-05-08 --previous-date 2026-05-06
  python3 handle_supervisor_questions.py 2026-05-08 --dry-run

JSON 輸出格式：
  {
    "has_supervisor_email": true,
    "previous_date": "2026-05-06",
    "card_id": "be23c883-...",
    "card_version": 1,
    "review_thread_id": "<ConversationID>",
    "review_message_id": "<EntryID>",
    "supervisor_email": "<主管 email>",
    "questions": [
      {"id": "Q1", "title": "...", "body": "...", "evidence_hint": "..."},
      ...
    ]
  }
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # 沒裝也能跑（fallback 不用 yaml）


def previous_workday(date_str: str) -> str:
    """從 target_date 倒推 1 天（連假處理待擴充）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def find_supervisor_reply(previous_date: str, dry_run: bool = False) -> dict:
    """
    用 PowerShell + Outlook COM 找主管 reply。

    回傳 dict（成功）或空 dict（找不到）。
    """
    subject_pattern = f"每日工作報告 {previous_date.replace('-', '/')}"

    # PowerShell 腳本：
    # 1. Sent Items 找原日報
    # 2. 用 ConversationID 找 Inbox 後續 reply
    # 3. 抽 .md 附件存到 /tmp/，回傳路徑（or body）
    ps_script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace('MAPI')

    $sent = $namespace.GetDefaultFolder(5)  # olFolderSentMail
    $filter = "[Subject] = '{subject_pattern}'"
    $sentItems = $sent.Items.Restrict($filter)
    $sentItems.Sort('[SentOn]', $true)

    if ($sentItems.Count -eq 0) {{
        Write-Output (ConvertTo-Json @{{ has_supervisor_email=$false; reason="no_original_log_found"; subject="{subject_pattern}" }} -Compress)
        exit 0
    }}

    $original = $sentItems.GetFirst()
    $convId = $original.ConversationID

    # Inbox 用 Conversation 找 reply（主管寄回的）
    $inbox = $namespace.GetDefaultFolder(6)  # olFolderInbox

    # 一些 Outlook 版本沒 .ConversationTopic.Restrict — 改用 walk Items
    $myEmail = $namespace.CurrentUser.Address
    $reply = $null

    # 先找 InboxFolder（含 subfolder「每日工作回覆」之類），廣度優先
    $foldersToScan = @($inbox)
    foreach ($f in $inbox.Folders) {{
        if ($f.DefaultItemType -eq 0) {{ $foldersToScan += $f }}
    }}

    foreach ($folder in $foldersToScan) {{
        # 限制搜尋近 30 天 reply
        $items = $folder.Items
        $items.Sort('[ReceivedTime]', $true)
        foreach ($item in $items) {{
            if ($item.MessageClass -ne 'IPM.Note') {{ continue }}
            if ($item.ConversationID -eq $convId -and $item.SenderEmailAddress -ne $myEmail) {{
                $reply = $item
                break
            }}
        }}
        if ($reply -ne $null) {{ break }}
    }}

    if ($reply -eq $null) {{
        Write-Output (ConvertTo-Json @{{
            has_supervisor_email=$false;
            reason='no_reply_found';
            previous_date='{previous_date}';
            conversation_id=$convId
        }} -Compress)
        exit 0
    }}

    # 抽附件 .md（找第一個 .md）
    $attachmentPath = $null
    foreach ($attach in $reply.Attachments) {{
        if ($attach.FileName -like '*.md') {{
            $tempPath = Join-Path $env:TEMP $attach.FileName
            $attach.SaveAsFile($tempPath)
            $attachmentPath = $tempPath
            break
        }}
    }}

    Write-Output (ConvertTo-Json @{{
        has_supervisor_email=$true;
        previous_date='{previous_date}';
        review_thread_id=$convId;
        review_message_id=$reply.EntryID;
        supervisor_email=$reply.SenderEmailAddress;
        supervisor_name=$reply.SenderName;
        received_at=$reply.ReceivedTime.ToString('o');
        subject=$reply.Subject;
        attachment_path=$attachmentPath;
        body_html=$reply.HTMLBody
    }} -Compress -Depth 4)

}} catch {{
    Write-Output (ConvertTo-Json @{{ has_supervisor_email=$false; reason='ps_exception'; error=$_.Exception.Message }} -Compress)
}}
"""

    if dry_run:
        return {"has_supervisor_email": False, "reason": "dry_run", "ps_script_preview": ps_script[:200]}

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout.strip()
    if not output:
        return {
            "has_supervisor_email": False,
            "reason": "ps_no_output",
            "stderr": result.stderr[:500],
        }
    try:
        return json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return {"has_supervisor_email": False, "reason": "ps_invalid_json", "raw": output[:500]}


def parse_card_md(md_path: Path) -> dict:
    """從卡片 .md 附件抽 frontmatter（card_id, questions[]）。"""
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    if yaml is None:
        # fallback: regex 抽 card_id + questions title
        card_id_m = re.search(r"card_id:\s*([\w\-]+)", text)
        return {
            "card_id": card_id_m.group(1) if card_id_m else None,
            "questions": [],
            "yaml_unavailable": True,
        }
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return {
        "card_id": data.get("card_id"),
        "card_version": data.get("card_version", 1),
        "employee": data.get("employee", {}),
        "questions": data.get("questions", []),
    }


def parse_questions_from_html(html: str) -> list[dict]:
    """fallback：從 HTML body 抽 H4「Q1.」「Q2.」段落作為 questions。"""
    if not html:
        return []
    # 移除 HTML tag → 純文字
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", "", text)

    questions = []
    pattern = re.compile(r"Q(\d+)[.、](.*?)(?=Q\d+[.、]|\Z)", re.DOTALL)
    for m in pattern.finditer(text):
        qid = f"Q{m.group(1)}"
        body = m.group(2).strip()[:1500]
        title = body.split("\n", 1)[0].strip()[:200]
        questions.append({"id": qid, "title": title, "body": body})
    return questions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target_date", help="屬下今天日期 YYYY-MM-DD")
    parser.add_argument("--previous-date", help="原日報日期（覆寫自動推算）")
    parser.add_argument("--output-json", action="store_true", help="只輸出 JSON 到 stdout")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    prev_date = args.previous_date or previous_workday(args.target_date)

    if not args.output_json:
        print(f"[INFO] target_date={args.target_date}, previous_date={prev_date}", file=sys.stderr)

    found = find_supervisor_reply(prev_date, dry_run=args.dry_run)

    if not found.get("has_supervisor_email"):
        result = {
            "has_supervisor_email": False,
            "previous_date": prev_date,
            "reason": found.get("reason"),
            "details": {k: v for k, v in found.items() if k not in ("has_supervisor_email", "reason")},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 有 reply：優先解析 .md 附件
    attachment_path_str = found.get("attachment_path")
    parsed_card = {}
    if attachment_path_str:
        # PowerShell 給的是 Windows 路徑；轉 WSL
        try:
            wsl_path = subprocess.check_output(
                ["wslpath", "-u", attachment_path_str], text=True
            ).strip()
            parsed_card = parse_card_md(Path(wsl_path))
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 已是 WSL 路徑或 wslpath 不可用
            parsed_card = parse_card_md(Path(attachment_path_str))

    questions = parsed_card.get("questions") or parse_questions_from_html(
        found.get("body_html", "")
    )

    result = {
        "has_supervisor_email": True,
        "previous_date": prev_date,
        "card_id": parsed_card.get("card_id"),
        "card_version": parsed_card.get("card_version", 1),
        "review_thread_id": found.get("review_thread_id"),
        "review_message_id": found.get("review_message_id"),
        "supervisor_email": found.get("supervisor_email"),
        "supervisor_name": found.get("supervisor_name"),
        "received_at": found.get("received_at"),
        "subject": found.get("subject"),
        "attachment_path": attachment_path_str,
        "questions": questions,
        "questions_source": "attachment_md" if attachment_path_str else "body_fallback",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
