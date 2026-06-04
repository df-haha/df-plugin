#!/usr/bin/env python3
"""
工作日誌郵件 payload 產生器（df-graph 版，取代舊 Outlook COM）。
讀取 markdown 工作日誌 → 轉成郵件 HTML → 寫到本機（WSL）暫存檔 →
emit JSON payload 給 skill；由 agent 呼叫 mcp__df-graph__mail_draft(body_file=...) 建草稿。
大型 HTML 內文走 body_file、不經對話 token 流。stdout 只輸出 payload JSON；進度走 stderr。

Usage:
    python scripts/send_work_log_email.py daily_proposal/daily_work_log_2026-03-09.md
    python scripts/send_work_log_email.py daily_proposal/daily_work_log_2026-03-09.md --to boss@example.com
"""
import sys
import re
import json
import tempfile
from pathlib import Path


CONFIG_PATH = Path.home() / ".claude" / "daily-work-log" / "config.json"


def load_config() -> dict:
    """讀取 daily-work-log config，JSON parse 失敗視為未設定。"""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def extract_date_from_filename(filepath: str) -> str:
    """從檔名提取日期，格式 YYYY/MM/DD"""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", filepath)
    if match:
        return f"{match.group(1)}/{match.group(2)}/{match.group(3)}"
    return ""


def md_to_html(md_content: str) -> str:
    """將工作日誌 markdown 轉為郵件 HTML 格式"""
    lines = md_content.strip().split("\n")

    # 跳過 metadata（標題行與 blockquote 區段）
    body_lines = []
    skip_header = True
    for line in lines:
        if skip_header:
            if line.startswith("# ") or line.startswith("> ") or line.strip() == "" or line.strip() == "---":
                continue
            skip_header = False
        body_lines.append(line)

    # 解析 markdown 結構
    sections = []          # (level, title, items)
    output_table_rows = [] # 產出清單表格
    topic_items = []       # 工作主題分類

    current_section = None
    current_subsection = None
    in_table = False
    in_topics = False
    table_header_skipped = False

    for line in body_lines:
        stripped = line.strip()

        # 分隔線
        if stripped == "---":
            in_table = False
            in_topics = False
            table_header_skipped = False
            continue

        # 工作主題分類
        if stripped == "## 工作主題分類":
            in_topics = True
            continue
        if in_topics:
            match = re.match(r"^\d+\.\s+(.+)$", stripped)
            if match:
                topic_items.append(match.group(1))
            continue

        # 產出清單表格
        if stripped == "## 產出清單":
            in_table = True
            table_header_skipped = False
            continue
        if in_table:
            if stripped.startswith("|"):
                # 跳過表頭與分隔行
                if not table_header_skipped:
                    if "---" in stripped:
                        table_header_skipped = True
                    continue
                # 解析資料行
                cols = [c.strip() for c in stripped.split("|")[1:-1]]
                if len(cols) >= 3:
                    num = cols[0]
                    item = cols[1]
                    status = cols[2].replace("✅", "完成").replace("🔄", "進行中")
                    output_table_rows.append((num, item, status))
            continue

        # 專案標題 (## 一、...)
        match_h2 = re.match(r"^##\s+(.+)$", stripped)
        if match_h2 and not stripped.startswith("## 產出") and not stripped.startswith("## 工作"):
            title = match_h2.group(1)
            current_section = {"title": title, "subsections": [], "items": []}
            sections.append(current_section)
            current_subsection = None
            continue

        # 工作主題 (### ...)
        match_h3 = re.match(r"^###\s+(.+)$", stripped)
        if match_h3 and current_section is not None:
            current_subsection = {"title": match_h3.group(1), "items": []}
            current_section["subsections"].append(current_subsection)
            continue

        # 列表項目
        match_li = re.match(r"^[-*]\s+(.+)$", stripped)
        if match_li:
            item_text = match_li.group(1)
            # 處理粗體
            item_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item_text)
            if current_subsection is not None:
                current_subsection["items"].append(item_text)
            elif current_section is not None:
                current_section["items"].append(item_text)
            continue

    # 組裝 HTML
    html_parts = []

    # 開場
    html_parts.append('<div style="font-family: \'Microsoft JhengHei\', sans-serif; font-size: 14px; color: #333; line-height: 1.6; max-width: 720px;">')
    html_parts.append("<p>主管您好，</p>")
    html_parts.append("<p>以下為今日工作報告，敬請參閱。</p>")

    # 一、工作主題分類
    if topic_items:
        html_parts.append(_section_header("一、今日工作主題"))
        html_parts.append("<ol>")
        for item in topic_items:
            # 粗體處理
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
            html_parts.append(f"  <li>{item}</li>")
        html_parts.append("</ol>")

    # 二、各專案工作明細
    if sections:
        html_parts.append(_section_header("二、各專案工作明細"))
        for sec in sections:
            html_parts.append(f'<h4 style="color: #1565C0; margin-top: 16px;">{sec["title"]}</h4>')
            if sec["subsections"]:
                for sub in sec["subsections"]:
                    html_parts.append(f'<p style="margin: 8px 0 4px 0; font-weight: bold;">{sub["title"]}</p>')
                    if sub["items"]:
                        html_parts.append("<ul>")
                        for item in sub["items"]:
                            html_parts.append(f"  <li>{item}</li>")
                        html_parts.append("</ul>")
            if sec["items"]:
                html_parts.append("<ul>")
                for item in sec["items"]:
                    html_parts.append(f"  <li>{item}</li>")
                html_parts.append("</ul>")

    # 三、產出清單
    if output_table_rows:
        html_parts.append(_section_header("三、產出清單"))
        html_parts.append('<table style="border-collapse: collapse; width: 100%;">')
        html_parts.append('<tr style="background: #E8F5E9;">')
        html_parts.append('  <th style="border: 1px solid #C8E6C9; padding: 8px; text-align: center; width: 40px;">#</th>')
        html_parts.append('  <th style="border: 1px solid #C8E6C9; padding: 8px;">產出項目</th>')
        html_parts.append('  <th style="border: 1px solid #C8E6C9; padding: 8px; text-align: center; width: 60px;">狀態</th>')
        html_parts.append("</tr>")
        for num, item, status in output_table_rows:
            html_parts.append("<tr>")
            html_parts.append(f'  <td style="border: 1px solid #ddd; padding: 8px; text-align: center;">{num}</td>')
            html_parts.append(f'  <td style="border: 1px solid #ddd; padding: 8px;">{item}</td>')
            html_parts.append(f'  <td style="border: 1px solid #ddd; padding: 8px; text-align: center;">{status}</td>')
            html_parts.append("</tr>")
        html_parts.append("</table>")

    # 結語 + 簽名
    html_parts.append('<p style="margin-top: 20px;">如有需要進一步了解的部分，歡迎隨時告知。</p>')
    html_parts.append('<p style="margin-top: 16px;">黃奕儒<br/><span style="color: #666; font-size: 12px;">營運部</span></p>')
    html_parts.append("</div>")

    return "\n".join(html_parts)


def _section_header(title: str) -> str:
    return (
        f'<h3 style="color: #2E7D32; border-left: 4px solid #2E7D32; '
        f'padding-left: 10px; margin-top: 20px;">{title}</h3>'
    )


def write_body_file(html_body: str, date_tag: str) -> Path:
    """把 HTML 內文寫到本機（WSL）暫存檔，回傳路徑。
    供 df-graph mail_draft 的 body_file 讀取——大型內文不經對話 token 流。
    一律用 WSL 暫存目錄（tempfile），絕不寫 C:\\temp / /mnt/c。"""
    tmp_dir = Path(tempfile.gettempdir()) / "daily-work-log"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_tag = re.sub(r"[^0-9A-Za-z_-]", "_", date_tag) or "latest"
    body_file = tmp_dir / f"work_log_email_{safe_tag}.html"
    body_file.write_text(html_body, encoding="utf-8")
    return body_file


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/send_work_log_email.py <markdown_file> [--to email]")
        sys.exit(1)

    md_file = sys.argv[1]

    # 優先序：--to 參數 > config 檔 email > 未設定則 exit
    to_email = ""
    if "--to" in sys.argv:
        idx = sys.argv.index("--to")
        if idx + 1 < len(sys.argv):
            to_email = sys.argv[idx + 1]

    if not to_email:
        config = load_config()
        to_email = config.get("outlook_email", "")

    if not to_email:
        print("未設定收件者。請用 --to 指定或在 ~/.claude/daily-work-log/config.json 設定 outlook_email。", file=sys.stderr)
        sys.exit(0)

    # 讀取 markdown
    md_path = Path(md_file)
    if not md_path.exists():
        print(f"[ERROR] 檔案不存在：{md_file}", file=sys.stderr)
        sys.exit(1)

    md_content = md_path.read_text(encoding="utf-8")

    # 提取日期
    date_str = extract_date_from_filename(str(md_path))
    subject = f"每日工作報告 {date_str}" if date_str else "每日工作報告"

    # 轉換為 HTML，寫到本機暫存檔（大型內文走 body_file，不經對話）
    html_body = md_to_html(md_content)
    date_tag = date_str.replace("/", "-") if date_str else "latest"
    body_file = write_body_file(html_body, date_tag)

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


if __name__ == "__main__":
    main()
