from __future__ import annotations
from mt_core.runlog import write_run_report, mask_email

def test_mask_email():
    assert mask_email("alice@example.com") == "a***@example.com"
    assert mask_email("garbage") == "***"

def test_report_has_counts_no_raw_email(tmp_path):
    summary = {
        "sent": 2, "skipped": 0, "processed_new": 1, "already_seen": 0, "untrusted": 1,
        "owners": [{"owner_id": "haha", "email": "haha@example.com"}],  # 不該洩漏進報告
        "idempotency_keys": ["MT:acme:digest:haha:2026-W22:2026-05-29"],
    }
    p = write_run_report(tmp_path, "2026-05-29", summary)
    body = p.read_text(encoding="utf-8")
    assert "haha@example.com" not in body         # 隱私：不寫完整 email
    assert "sent: 2" in body and "untrusted senders dropped: 1" in body
    assert "MT:acme:digest:haha" in body
