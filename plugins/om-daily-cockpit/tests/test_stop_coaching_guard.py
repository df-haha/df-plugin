"""關卡 A（stop_coaching_guard）測試。

純判準（stop_should_block）+ 防迴圈 state（_block_key/_already_blocked/_record_blocked）
+ 端對端 subprocess（有訊號缺卡 block / 同 session 去重 / 無 config 放行）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))

import stop_coaching_guard as sg  # noqa: E402
from last_workday import get_last_workday  # noqa: E402
from test_coaching_card_lint import CFG_MD  # noqa: E402

VENV_PY = ROOT.parent.parent / ".venv" / "bin" / "python"
HOOK = ROOT / "hooks" / "stop_coaching_guard.py"


# --- 純判準 -----------------------------------------------------------------

def test_signal_and_no_card_blocks():
    block, tag = sg.stop_should_block("...\n## ❓ 待釐清疑問\n- 宗霖：…", card_exists=False)
    assert block is True and tag == "missing_card"


def test_signal_but_card_exists_allows():
    block, tag = sg.stop_should_block("❓ 待釐清疑問", card_exists=True)
    assert block is False and tag == "card_present"


def test_no_signal_allows_noop():
    block, tag = sg.stop_should_block("今天沒有待釐清，一切正常。", card_exists=False)
    assert block is False and tag == "no_signal"


def test_coaching_waived_allows():
    text = "## ❓ 待釐清疑問\n- …\n\ncoaching_waived: true\n"
    block, tag = sg.stop_should_block(text, card_exists=False)
    assert block is False and tag == "waived"


def test_coaching_required_marker_signal():
    block, _ = sg.stop_should_block("<!-- coaching_required -->", card_exists=False)
    assert block is True


def test_no_proposal_allows():
    block, tag = sg.stop_should_block(None, card_exists=False)
    assert block is False and tag == "no_proposal"


# --- 防迴圈 state ----------------------------------------------------------

def test_dedup_same_session_date(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_STATE", tmp_path / "state.json")
    key = sg._block_key("sess-1", "2026-06-04", "/repo/a")
    assert sg._already_blocked(key) is False
    sg._record_blocked(key)
    assert sg._already_blocked(key) is True


def test_cross_day_reblocks(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_STATE", tmp_path / "state.json")
    k1 = sg._block_key("sess-1", "2026-06-04", "/repo/a")
    sg._record_blocked(k1)
    # 不同 target_date → 不同 key → 隔日仍會 block
    k2 = sg._block_key("sess-1", "2026-06-05", "/repo/a")
    assert sg._already_blocked(k2) is False
    # 不同 cwd 也獨立
    k3 = sg._block_key("sess-1", "2026-06-04", "/repo/b")
    assert sg._already_blocked(k3) is False


# --- 端對端 subprocess ------------------------------------------------------

def _run(cwd: Path, session_id: str, state_file: Path | None = None,
         stop_hook_active: bool = False) -> str:
    env = dict(os.environ)
    env.pop("OM_DAILY_COCKPIT_CONFIG", None)  # 避免外部 env 干擾「無 config 放行」測試
    if state_file is not None:
        env["OM_COCKPIT_STOP_GUARD_STATE"] = str(state_file)
    payload = {"session_id": session_id, "cwd": str(cwd)}
    if stop_hook_active:
        payload["stop_hook_active"] = True
    proc = subprocess.run(
        [str(VENV_PY), str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _make_tenant(tmp_path: Path, *, with_signal: bool, with_card: bool) -> tuple[Path, str]:
    root = tmp_path / "tenant"
    (root / ".om-cockpit").mkdir(parents=True)
    (root / ".om-cockpit" / "config.md").write_text(CFG_MD, encoding="utf-8")
    dp = root / "daily_proposal"
    dp.mkdir()
    tz = ZoneInfo("Asia/Taipei")
    today = datetime.now(tz).date()
    target = get_last_workday(today)
    signal = "## ❓ 待釐清疑問\n- 宗霖：…\n" if with_signal else "今天一切正常。\n"
    (dp / f"daily_proposal_{today.isoformat()}.md").write_text(
        f"# 報告 {today}\n\n{signal}", encoding="utf-8"
    )
    if with_card:
        (dp / f"team_coaching_cards_{target.isoformat()}.md").write_text(
            "（草稿）", encoding="utf-8"
        )
    return root, f"sess-{today.isoformat()}-{'c' if with_card else 'n'}"


def test_e2e_signal_no_card_blocks(tmp_path):
    root, sid = _make_tenant(tmp_path, with_signal=True, with_card=False)
    out = _run(root, sid + "-blk", state_file=tmp_path / "sg.json")
    assert out, "預期 block 輸出"
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "team_coaching_cards" in payload["reason"]


def test_e2e_dedup_second_run_allows(tmp_path):
    root, sid = _make_tenant(tmp_path, with_signal=True, with_card=False)
    state = tmp_path / "sg.json"
    first = _run(root, sid, state_file=state)
    assert json.loads(first)["decision"] == "block"
    second = _run(root, sid, state_file=state)  # 同 session+date → 放行（無輸出）
    assert second == ""


def test_e2e_card_present_allows(tmp_path):
    root, sid = _make_tenant(tmp_path, with_signal=True, with_card=True)
    assert _run(root, sid + "-ok", state_file=tmp_path / "sg.json") == ""


def test_e2e_no_signal_allows(tmp_path):
    root, sid = _make_tenant(tmp_path, with_signal=False, with_card=False)
    assert _run(root, sid + "-nosig", state_file=tmp_path / "sg.json") == ""


def test_e2e_no_config_allows(tmp_path):
    # 沒有 .om-cockpit/config.md 的目錄 → 靜默放行
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _run(bare, "sess-bare", state_file=tmp_path / "sg.json") == ""


def test_e2e_stop_hook_active_breaks_loop(tmp_path):
    # advisor 補洞：stop_hook_active=true（前一 Stop 已 block 而重觸）→ 即使該 block 也放行，
    # 不依賴 state 檔。用「會 block 的情境 + 全新 state 檔」確認是 stop_hook_active 而非去重生效。
    root, sid = _make_tenant(tmp_path, with_signal=True, with_card=False)
    out = _run(root, sid + "-active", state_file=tmp_path / "sg.json", stop_hook_active=True)
    assert out == "", "stop_hook_active 應無條件放行，避免 state 寫入失敗時無限 re-block"
