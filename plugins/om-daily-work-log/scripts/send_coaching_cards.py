#!/usr/bin/env python3
"""
send_coaching_cards.py — 主管端：reply 屬下原日報寄出澄清問題卡

讀取 daily_proposal/team_coaching_cards_{date}.md（multi-document YAML + markdown），
切分成 N 張卡片，對每張卡：
  1. 找對應屬下的 previous-workday 日報郵件（在「每日工作報告」資料夾）
  2. 用 Outlook COM 開 Reply 草稿（thread 自動串）
  3. body 帶該卡片內容（HTML 渲染）
  4. 附件帶該卡片的 .md 切片
  5. 把 EntryID + ConversationID 寫回 bundle md 的卡片 frontmatter

預設模式：reply + 開草稿（不自動發送）。
旗標：
  --mode reply | compose       (預設 reply；fallback compose 開新郵件)
  --auto-send                  (直接發送，不開草稿；預設開草稿)
  --target-date YYYY-MM-DD     (指定 previous-workday；預設從 bundle md target_work_date 取)
  --dry-run                    (只 parse + print，不開 Outlook)

Usage:
  python3 send_coaching_cards.py daily_proposal/team_coaching_cards_2026-05-06.md
  python3 send_coaching_cards.py team_coaching_cards_2026-05-06.md --dry-run
  python3 send_coaching_cards.py team_coaching_cards_2026-05-06.md --mode compose --auto-send
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[ERROR] 缺 PyYAML：pip install PyYAML", file=sys.stderr)
    sys.exit(1)


CARD_YAML_BLOCK = re.compile(
    r"^```yaml\n(?P<yaml>.*?)\n```\n+(?P<body>.*?)(?=^```yaml\n|\Z)",
    re.DOTALL | re.MULTILINE,
)
TPE = timezone(timedelta(hours=8))


def parse_bundle(md_path: Path) -> tuple[dict, list[dict], str]:
    """切 bundle md：抽 bundle frontmatter + 每張卡 (yaml + body)。

    回傳 (bundle_meta, cards[], full_text)
    cards[i] = {"yaml": dict, "body_md": str, "yaml_block_start": int, "yaml_block_end": int}
    """
    text = md_path.read_text(encoding="utf-8")

    # 抽 bundle frontmatter（檔頭 --- ... ---）
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    bundle_meta: dict = {}
    body_after_bundle = text
    if m:
        bundle_meta = yaml.safe_load(m.group(1)) or {}
        body_after_bundle = text[m.end() :]

    cards = []
    # 在剩餘文字找所有 ```yaml ... ``` block
    for match in CARD_YAML_BLOCK.finditer(body_after_bundle):
        yaml_text = match.group("yaml")
        try:
            card_yaml = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as e:
            print(f"[WARN] 跳過 yaml 解析失敗的卡片：{e}", file=sys.stderr)
            continue
        if not isinstance(card_yaml, dict) or "card_id" not in card_yaml:
            continue
        cards.append(
            {
                "yaml": card_yaml,
                "body_md": match.group("body").strip(),
                "yaml_block_start": match.start() + (text.find(body_after_bundle)),
                "yaml_block_end": match.end() + (text.find(body_after_bundle)),
            }
        )
    return bundle_meta, cards, text


def previous_workday(date_str: str) -> str:
    """簡化版：往前 1 天（週末/連假需手動指定）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def render_card_html(card: dict, body_md: str) -> str:
    """把單張卡的 markdown body 轉 HTML。"""
    yaml_data = card["yaml"]
    employee = yaml_data.get("employee", {})
    name = employee.get("name", "屬下")

    # 簡單 markdown → HTML（H2/H3/H4 + bullet + paragraph）
    html_lines = [
        '<div style="font-family: \'Microsoft JhengHei\', sans-serif; font-size: 14px; '
        'color: #333; line-height: 1.7; max-width: 720px;">',
        f"<p>{name} 您好，</p>",
        '<p>看了您 5/6 的日報，整理幾個想了解的點。請用 Claude Code（CC）查 git/spec/tasks '
        "後組織回覆，明日日報附上即可。</p>",
        '<hr style="border: 0; border-top: 1px solid #ddd; margin: 16px 0;">',
    ]

    in_ul = False
    for line in body_md.split("\n"):
        s = line.strip()
        if not s:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            continue
        if s.startswith("## "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(
                f'<h2 style="color: #2E7D32; border-left: 4px solid #2E7D32; '
                f'padding-left: 10px;">{s[3:]}</h2>'
            )
        elif s.startswith("### "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(
                f'<h3 style="color: #1565C0; margin-top: 16px;">{s[4:]}</h3>'
            )
        elif s.startswith("#### "):
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            html_lines.append(
                f'<h4 style="color: #6A1B9A; margin-top: 12px;">{s[5:]}</h4>'
            )
        elif s.startswith("- ") or s.startswith("* "):
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            item = s[2:]
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
            item = re.sub(r"`([^`]+)`", r"<code>\1</code>", item)
            html_lines.append(f"  <li>{item}</li>")
        else:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            paragraph = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
            paragraph = re.sub(r"`([^`]+)`", r"<code>\1</code>", paragraph)
            html_lines.append(f"<p>{paragraph}</p>")
    if in_ul:
        html_lines.append("</ul>")

    html_lines.extend(
        [
            '<hr style="border: 0; border-top: 1px solid #ddd; margin: 16px 0;">',
            '<p style="color: #666; font-size: 12px;">'
            "此卡片由 om-daily-work-log plugin 產生。"
            f"card_id: <code>{yaml_data.get('card_id', 'N/A')}</code>"
            "</p>",
            "</div>",
        ]
    )
    return "\n".join(html_lines)


def write_card_md_slice(card: dict, body_md: str, out_dir: Path) -> Path:
    """把單張卡 dump 成獨立 .md 檔（給附件用）。"""
    yaml_data = card["yaml"]
    employee = yaml_data.get("employee", {})
    name = employee.get("name", "card")
    target_date = yaml_data.get("target_work_date", "unknown")
    out_path = out_dir / f"coaching_card_{name}_{target_date}.md"
    content = (
        "---\n"
        + yaml.safe_dump(yaml_data, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body_md
        + "\n"
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def to_windows_path(posix_path: Path) -> str:
    abs_path = str(posix_path.resolve())
    try:
        result = subprocess.run(
            ["wslpath", "-w", abs_path],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return abs_path


def open_reply_draft(
    employee_name: str,
    previous_date: str,
    html_body: str,
    attachment_path: Path | None,
    auto_send: bool = False,
) -> dict:
    """
    用 PowerShell + Outlook COM 找屬下的 previous-workday 日報，呼叫 .Reply() 開草稿。

    回傳 {"status": "sent"|"draft"|"failed", "entry_id": str, "conversation_id": str, "error": str}
    """
    # subject pattern: 「每日工作報告 YYYY/MM/DD」
    subject_pattern = f"每日工作報告 {previous_date.replace('-', '/')}"

    attach_line = ""
    if attachment_path is not None and attachment_path.exists():
        win_path = to_windows_path(attachment_path)
        ps_escaped = win_path.replace("'", "''")
        attach_line = f"$reply.Attachments.Add('{ps_escaped}') | Out-Null"

    action_line = "$reply.Send()" if auto_send else "$reply.Display()"
    final_status = "sent" if auto_send else "draft"

    # PowerShell：找匹配 subject + sender name 的最新郵件 → Reply
    ps_script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace('MAPI')

    # 找「每日工作報告」資料夾（在 Inbox 下）
    $inbox = $namespace.GetDefaultFolder(6)  # olFolderInbox
    $targetFolder = $null
    foreach ($f in $inbox.Folders) {{
        if ($f.Name -eq '每日工作報告') {{
            $targetFolder = $f
            break
        }}
    }}
    if ($targetFolder -eq $null) {{ $targetFolder = $inbox }}

    # 用 Items.Restrict 找匹配 subject + sender 的郵件（最新一封）
    $filter = "[Subject] = '{subject_pattern}'"
    $items = $targetFolder.Items.Restrict($filter)
    $items.Sort('[ReceivedTime]', $true)

    $matchedMail = $null
    foreach ($item in $items) {{
        if ($item.SenderName -like '*{employee_name}*' -or $item.Subject -like '*{employee_name}*') {{
            $matchedMail = $item
            break
        }}
    }}

    # Fallback：找不到匹配 sender，取第一封 subject 對的
    if ($matchedMail -eq $null -and $items.Count -gt 0) {{
        $matchedMail = $items.GetFirst()
    }}

    if ($matchedMail -eq $null) {{
        Write-Output (ConvertTo-Json @{{ status='failed'; error='找不到原日報郵件 (subject={subject_pattern}, sender={employee_name})' }} -Compress)
        exit 0
    }}

    # 寫 HTML 到 temp
    if (-not (Test-Path "C:\\temp")) {{ New-Item -ItemType Directory -Path "C:\\temp" | Out-Null }}
    $htmlContent = @"
{html_body}
"@
    [System.IO.File]::WriteAllText("C:\\temp\\coaching_card.html", $htmlContent, [System.Text.Encoding]::UTF8)
    $body = [System.IO.File]::ReadAllText("C:\\temp\\coaching_card.html", [System.Text.Encoding]::UTF8)

    # Reply
    $reply = $matchedMail.Reply()
    $reply.HTMLBody = $body + $reply.HTMLBody  # prepend
    {attach_line}
    {action_line}

    # 取 EntryID + ConversationID
    $entryId = ''
    $convId = ''
    try {{ $entryId = $reply.EntryID }} catch {{}}
    try {{ $convId = $reply.ConversationID }} catch {{}}

    Remove-Item "C:\\temp\\coaching_card.html" -ErrorAction SilentlyContinue

    Write-Output (ConvertTo-Json @{{ status='{final_status}'; entry_id=$entryId; conversation_id=$convId }} -Compress)
}} catch {{
    Write-Output (ConvertTo-Json @{{ status='failed'; error=$_.Exception.Message }} -Compress)
}}
"""

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
            "status": "failed",
            "error": f"PS 無輸出 / stderr: {result.stderr[:500]}",
        }
    try:
        return json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return {"status": "failed", "error": f"PS 輸出非 JSON: {output[:500]}"}


def update_card_in_bundle(
    md_path: Path,
    full_text: str,
    card_index: int,
    cards: list[dict],
    update_fields: dict,
) -> str:
    """把單張卡的 yaml 部分更新（review_status, sent_at 等）後 dump 回 md。"""
    card = cards[card_index]
    new_yaml = {**card["yaml"], **update_fields}
    new_yaml_text = yaml.safe_dump(new_yaml, allow_unicode=True, sort_keys=False).rstrip()

    # 替換對應 yaml block
    old_block_text = full_text[card["yaml_block_start"] : card["yaml_block_end"]]
    body_part = old_block_text.split("```\n", 2)[2] if "```\n" in old_block_text else ""
    new_block = f"```yaml\n{new_yaml_text}\n```\n\n{body_part.lstrip()}"

    new_full = full_text[: card["yaml_block_start"]] + new_block + full_text[card["yaml_block_end"] :]
    return new_full


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("md_file")
    parser.add_argument("--mode", choices=["reply", "compose"], default="reply")
    parser.add_argument("--auto-send", action="store_true")
    parser.add_argument("--target-date")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    md_path = Path(args.md_file)
    if not md_path.exists():
        print(f"[ERROR] 檔案不存在：{md_path}", file=sys.stderr)
        sys.exit(1)

    bundle_meta, cards, full_text = parse_bundle(md_path)
    if not cards:
        print("[ERROR] bundle 中沒有解析到任何卡片（檢查 ```yaml block 格式）", file=sys.stderr)
        sys.exit(1)

    target_date = args.target_date or bundle_meta.get("target_work_date")
    if not target_date:
        print("[ERROR] 缺 target_work_date（請用 --target-date 指定，或在 bundle frontmatter 補）", file=sys.stderr)
        sys.exit(1)
    # PyYAML 把 ISO 日期 auto-cast 成 datetime.date — 統一轉 str
    if hasattr(target_date, "strftime"):
        target_date = target_date.strftime("%Y-%m-%d")
    target_date = str(target_date)

    # 防呆：reference card bundle 不可寄送
    if bundle_meta.get("file_type") == "reference_card_bundle":
        print(
            "[WARN] 此檔為 reference_card_bundle（鎖定範本），不應直接寄出。"
            "請先 copy 一份成 runtime_cards 再執行。"
        )
        if not args.dry_run:
            print("[INFO] 用 --dry-run 可繼續預覽 parse 結果。", file=sys.stderr)
            sys.exit(2)

    # 每張卡寫獨立 md slice 給附件用
    slice_dir = md_path.parent / ".coaching_card_slices"
    slice_dir.mkdir(exist_ok=True)

    print(f"[INFO] 解析到 {len(cards)} 張卡片，寄送模式={args.mode}, auto_send={args.auto_send}")

    new_full_text = full_text
    for idx, card in enumerate(cards):
        yaml_data = card["yaml"]
        employee = yaml_data.get("employee", {})
        name = employee.get("name", f"card-{idx}")

        # Skip 已寄出的（除非主管要求重寄；目前簡化為 skip）
        if yaml_data.get("review_status") in ("sent", "replied", "parsed", "closed"):
            print(f"[SKIP] 卡 {idx + 1} ({name}) 狀態={yaml_data.get('review_status')}，跳過")
            continue
        if yaml_data.get("review_status") == "superseded":
            print(f"[SKIP] 卡 {idx + 1} ({name}) 已被 supersede，跳過")
            continue

        # 寫 md slice
        slice_path = write_card_md_slice(card, card["body_md"], slice_dir)
        # 渲染 HTML
        html_body = render_card_html(card, card["body_md"])

        if args.dry_run:
            print(f"[DRY-RUN] 卡 {idx + 1} ({name}) → reply 屬下 5/6 日報")
            print(f"[DRY-RUN]   subject 將自動變 Re: 每日工作報告 {target_date.replace('-', '/')}")
            print(f"[DRY-RUN]   附件: {slice_path}")
            print(f"[DRY-RUN]   HTML 預覽（前 200 字）: {html_body[:200]}")
            continue

        # 開 Reply 草稿
        result = open_reply_draft(
            employee_name=name,
            previous_date=target_date,
            html_body=html_body,
            attachment_path=slice_path,
            auto_send=args.auto_send,
        )

        if result.get("status") in ("sent", "draft"):
            print(
                f"[OK] 卡 {idx + 1} ({name}) → status={result['status']}, "
                f"entry_id={result.get('entry_id', '')[:30]}, conv_id={result.get('conversation_id', '')[:30]}"
            )
            update_fields = {
                "review_status": "sent" if result["status"] == "sent" else "draft",
                "sent_at": datetime.now(TPE).isoformat() if result["status"] == "sent" else None,
                "review_message_id": result.get("entry_id", ""),
                "review_thread_id": result.get("conversation_id", ""),
                "last_review_action_at": datetime.now(TPE).isoformat(),
            }
            new_full_text = update_card_in_bundle(
                md_path, new_full_text, idx, cards, update_fields
            )
        else:
            print(
                f"[FAIL] 卡 {idx + 1} ({name}) → {result.get('error', 'unknown')}",
                file=sys.stderr,
            )

    # 寫回 bundle md（如有更新）
    if not args.dry_run and new_full_text != full_text:
        md_path.write_text(new_full_text, encoding="utf-8")
        print(f"[OK] 已更新 bundle md: {md_path}")

    print("[DONE]")


if __name__ == "__main__":
    main()
