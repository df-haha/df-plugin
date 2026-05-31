# tests/test_collect_replies_cli.py
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples" / "dafeng-ops"

def test_collect_writes_draft_with_report(tmp_path):
    msgs = [{
        "msg_id": "g1", "thread_id": "t1",
        "sender": "haha@example.com",
        "subject": "Re: (2026-W22) [#MTD1.dafeng-ops.haha.2026-W22.ab12cd]",
        "body_text": "[#metric:g1-tender] 已完成投標文件，等待開標",
    }]
    inbox = tmp_path / "inbox.json"
    inbox.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/collect_replies.py"),
         "--config", str(EX / "config.md"), "--repo-root", str(tmp_path),
         "--inbox-json", str(inbox), "--today", "2026-05-29"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    draft = tmp_path / "drafts" / "meeting_draft_week_2026-W22.md"
    assert draft.exists()
    body = draft.read_text(encoding="utf-8")
    assert "已完成投標文件" in body and "(source: owner email)" in body

def test_late_reply_does_not_clobber_past_week_draft(tmp_path):
    # Pre-create a W21 draft with sentinel content that must NOT be overwritten
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    past_draft = drafts_dir / "meeting_draft_week_2026-W21.md"
    past_draft.write_text("PAST WEEK W21 EXISTING DRAFT — earlier reports here", encoding="utf-8")

    # Late reply: token week is W21 but --today is 2026-05-29 (W22)
    msgs = [{
        "msg_id": "late1", "thread_id": "t-late",
        "sender": "haha@example.com",
        "subject": "Re: [#MTD1.dafeng-ops.haha.2026-W21.ab12cd]",
        "body_text": "[#metric:g1-tender] 補上週進度",
    }]
    inbox = tmp_path / "inbox.json"
    inbox.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/collect_replies.py"),
         "--config", str(EX / "config.md"), "--repo-root", str(tmp_path),
         "--inbox-json", str(inbox), "--today", "2026-05-29"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # (a) Past-week W21 draft must NOT be clobbered
    assert "PAST WEEK W21 EXISTING DRAFT" in past_draft.read_text(encoding="utf-8")
    # (b) Current-week W22 draft must exist (always written)
    w22_draft = tmp_path / "drafts" / "meeting_draft_week_2026-W22.md"
    assert w22_draft.exists()
    # (c) summary reports late_replies_recorded == 1
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    assert summary["late_replies_recorded"] == 1


def test_collect_dedup_keeps_draft_on_rerun(tmp_path):
    # P0-3 回歸測試：第二次重抓整週，舊回報「已處理」但 draft 不可消失
    msgs = [{"msg_id":"dup","thread_id":"t","sender":"haha@example.com",
             "subject":"[#MTD1.dafeng-ops.haha.2026-W22.ab12cd]",
             "body_text":"[#metric:g1-tender] 投標文件已送出"}]
    inbox = tmp_path / "inbox.json"; inbox.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    base = [sys.executable, str(ROOT/"scripts/collect_replies.py"),
            "--config", str(EX/"config.md"), "--repo-root", str(tmp_path),
            "--inbox-json", str(inbox), "--today", "2026-05-29"]
    r1 = subprocess.run(base, capture_output=True, text=True)
    s1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert s1["processed_new"] == 1 and s1["already_seen"] == 0
    r2 = subprocess.run(base, capture_output=True, text=True)
    s2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert s2["already_seen"] == 1 and s2["processed_new"] == 0
    # 關鍵：第二次後 draft 仍含該回報（沒被 dedup 掉）
    draft = (tmp_path / "drafts" / "meeting_draft_week_2026-W22.md").read_text(encoding="utf-8")
    assert "投標文件已送出" in draft


def test_no_token_empty_state_reply_reaches_current_week_draft(tmp_path):
    # Codex WF2 #1 regression: a TRUSTED reply with NO correlation token and NO prior
    # state must still land in the CURRENT week's draft (not be dropped with week="").
    msgs = [{"msg_id":"ntk","thread_id":"t","sender":"haha@example.com",
             "subject":"進度回報（改了主旨、沒帶 token）",
             "body_text":"[#metric:g1-tender] 投標文件昨天送出了"}]
    inbox = tmp_path / "inbox.json"
    inbox.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT/"scripts/collect_replies.py"),
         "--config", str(EX/"config.md"), "--repo-root", str(tmp_path),
         "--inbox-json", str(inbox), "--today", "2026-05-29"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout.strip().splitlines()[-1])
    assert s["processed_new"] == 1 and s["untrusted"] == 0
    draft = (tmp_path / "drafts" / "meeting_draft_week_2026-W22.md").read_text(encoding="utf-8")
    assert "投標文件昨天送出了" in draft   # reached current-week draft, not dropped on week=""
