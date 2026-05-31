from __future__ import annotations

from pathlib import Path


def mask_email(email: str) -> str:
    name, sep, dom = email.partition("@")
    return (name[0] + "***@" + dom) if (sep and name) else "***"


def write_run_report(run_log_dir, date_str: str, summary: dict) -> Path:
    """只寫 counts + idempotency keys；不寫 email body / 完整位址（隱私邊界）。"""
    run_log_dir = Path(run_log_dir)
    run_log_dir.mkdir(parents=True, exist_ok=True)
    p = run_log_dir / f"run_report_{date_str}.md"
    lines = [
        f"# Meeting Tracker run report — {date_str}", "",
        f"- sent: {summary.get('sent', 0)}",
        f"- skipped (idempotent): {summary.get('skipped', 0)}",
        f"- replies processed (new): {summary.get('processed_new', 0)}",
        f"- replies already-seen: {summary.get('already_seen', 0)}",
        f"- untrusted senders dropped: {summary.get('untrusted', 0)}",
        "",
    ]
    keys = summary.get("idempotency_keys") or []
    if keys:
        lines += ["## idempotency keys", *[f"- `{k}`" for k in keys], ""]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
