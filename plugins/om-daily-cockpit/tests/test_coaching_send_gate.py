"""關卡 C-寄送（coaching_send_gate）測試。

涵蓋：未驗證+--auto-send deny、未驗證+草稿放行、已驗證+實寄放行、compose 模式 deny、
非 coaching 指令放行、指令解析（旗標/值旗標/positional md）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))

from coaching_send_gate import gate_decision, parse_send_commands  # noqa: E402
from oc_core.config import load_config  # noqa: E402

# 共用同一份 config（roy-you verified:false / lin-meixing,xiao-xinping verified:true）
from test_coaching_card_lint import CFG_MD  # noqa: E402

VERIFIED = "01550@df-recycle.com.tw"      # 林梅杏 verified
UNVERIFIED = "royyou@df-recycle.com"      # 游宗霖 unverified

# 多一個帶 alias 的 config（lin-meixing 加 alias + verified:true）測「alias 永遠 unverified」
CFG_WITH_ALIAS = CFG_MD.replace(
    "      email: 01550@df-recycle.com.tw\n      verified: true\n",
    "      email: 01550@df-recycle.com.tw\n      verified: true\n"
    "      alias_allowlist: [meixing.lin@personal.example]\n",
)
ALIAS = "meixing.lin@personal.example"


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.md"
    p.write_text(CFG_MD, encoding="utf-8")
    return load_config(p)


@pytest.fixture
def config_alias(tmp_path):
    p = tmp_path / "config_alias.md"
    p.write_text(CFG_WITH_ALIAS, encoding="utf-8")
    return load_config(p)


# --- 指令解析 ---------------------------------------------------------------

def test_parse_non_send_command_returns_empty():
    assert parse_send_commands("ls -la daily_proposal/") == []
    assert parse_send_commands("python3 other_script.py x.md --auto-send") == []


def test_parse_extracts_md_and_flags():
    out = parse_send_commands(
        "python3 hooks/../scripts/send_coaching_cards.py "
        "daily_proposal/team_coaching_cards_2026-06-04.md --auto-send"
    )
    assert len(out) == 1
    p = out[0]
    assert p["md_file"].endswith("team_coaching_cards_2026-06-04.md")
    assert p["auto_send"] is True
    assert p["mode"] == "reply"
    assert p["dry_run"] is False


def test_parse_mode_compose_space_and_value_flags():
    out = parse_send_commands(
        "python3 send_coaching_cards.py cards.md --target-date 2026-06-04 "
        "--mode compose --subject-prefix 【追蹤】"
    )
    assert len(out) == 1
    assert out[0]["md_file"] == "cards.md"       # 值旗標的值不被誤當 md
    assert out[0]["mode"] == "compose"


def test_parse_dry_run():
    out = parse_send_commands("python3 send_coaching_cards.py cards.md --dry-run")
    assert out[0]["dry_run"] is True


def test_parse_mode_uppercase_lowercased():
    # I-1：--mode COMPOSE 不可繞過（小寫化後仍 compose）
    out = parse_send_commands("python3 send_coaching_cards.py cards.md --mode COMPOSE")
    assert out[0]["mode"] == "compose"


def test_parse_chained_dry_run_then_autosend():
    # C-1：鏈式「預覽 && 真寄」——必須抓到第二段的 --auto-send，不被第一段 --dry-run 蓋掉
    out = parse_send_commands(
        "python3 send_coaching_cards.py a.md --dry-run && "
        "python3 send_coaching_cards.py b.md --auto-send"
    )
    assert len(out) == 2
    assert out[0] == {"md_file": "a.md", "auto_send": False, "mode": "reply", "dry_run": True}
    assert out[1] == {"md_file": "b.md", "auto_send": True, "mode": "reply", "dry_run": False}


def test_parse_bash_c_wrapped():
    # C-2：bash -c "..." 包裹也要解包
    out = parse_send_commands(
        'bash -c "python3 send_coaching_cards.py cards.md --auto-send"'
    )
    assert len(out) == 1
    assert out[0]["md_file"] == "cards.md"
    assert out[0]["auto_send"] is True


def test_parse_semicolon_and_pipe_chains():
    out = parse_send_commands(
        "python3 send_coaching_cards.py a.md ; python3 send_coaching_cards.py b.md --mode compose"
    )
    assert len(out) == 2
    assert out[1]["mode"] == "compose"


# --- gate 決策 --------------------------------------------------------------

def test_unverified_autosend_denies(config):
    assert gate_decision([UNVERIFIED], auto_send=True, mode="reply", config=config) is not None


def test_unverified_draft_reply_allows(config):
    # reply + 草稿（不帶 --auto-send / compose）→ 放行
    assert gate_decision([UNVERIFIED], auto_send=False, mode="reply", config=config) is None


def test_verified_autosend_allows(config):
    assert gate_decision([VERIFIED], auto_send=True, mode="reply", config=config) is None


def test_unverified_compose_draft_denies(config):
    # compose 模式即使草稿也把未驗證 email 填 To: → deny
    assert gate_decision([UNVERIFIED], auto_send=False, mode="compose", config=config) is not None


def test_verified_compose_allows(config):
    assert gate_decision([VERIFIED], auto_send=False, mode="compose", config=config) is None


def test_email_not_in_roster_treated_unverified(config):
    assert gate_decision(["ghost@evil.example"], auto_send=True, mode="reply", config=config) is not None


def test_mixed_emails_denies_if_any_unverified(config):
    reason = gate_decision([VERIFIED, UNVERIFIED], auto_send=True, mode="reply", config=config)
    assert reason is not None
    assert UNVERIFIED in reason
    assert VERIFIED not in reason  # 已驗證者不列入未驗證清單


def test_main_email_uppercase_case_insensitive(config):
    # 主 email 大小寫不影響 verified 判定 → 已驗證者大寫仍放行
    assert gate_decision([VERIFIED.upper()], auto_send=True, mode="reply", config=config) is None


def test_alias_email_treated_unverified(config_alias):
    # M-3：alias 永遠 unverified（即使其 member verified:true）→ 實寄擋
    assert gate_decision([ALIAS], auto_send=True, mode="reply", config=config_alias) is not None
    # 同 member 的主 email 仍 verified → 放行（對照）
    assert gate_decision([VERIFIED], auto_send=True, mode="reply", config=config_alias) is None
