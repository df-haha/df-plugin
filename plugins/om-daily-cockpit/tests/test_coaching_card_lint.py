"""關卡 B + C-寫檔（coaching_card_lint）核心邏輯測試。

fixture 用**真實 CARD_YAML_BLOCK 格式**（```yaml block + body），與寄送端 parse_bundle 同形。
重點覆蓋：質疑詞 deny、成本詞 deny、成本摘要段不誤判（advisor #5 守住 H4-scoping 啟發法）、
缺欄位 deny、member/email 錯配 deny、未驗證 warn-not-deny、已驗證放行、malformed deny、
parser regex 與寄送端逐字對齊。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))

from _coaching_hooklib import (  # noqa: E402
    CARD_YAML_BLOCK,
    MalformedCardError,
    extract_question_segments,
    find_tone_violation,
    lint_cards,
    parse_cards,
)
from oc_core.config import load_config  # noqa: E402

CFG_MD = """```oc-config
schema_version: 1
tenant_id: dafeng-om
timezone: Asia/Taipei
identity:
  department: 營運部
  company: 大豐
  persona: 駕駛艙
team:
  members:
    - member_id: roy-you
      name: 游宗霖
      email: royyou@df-recycle.com
      verified: false
    - member_id: lin-meixing
      name: 林梅杏
      email: 01550@df-recycle.com.tw
      verified: true
    - member_id: xiao-xinping
      name: 蕭欣萍
      email: 01415@df-recycle.com.tw
      verified: true
email:
  adapter: df_graph
  account: boss@df-recycle.com.tw
  daily_report_folder: 每日工作報告
  processed_category: ""
paths:
  archive_dir: data/daily_reports
  daily_proposal_dir: daily_proposal
directive:
  subject_prefix: "【每日追蹤】"
  marker: "<!-- om-directive -->"
services:
  database_url_env: OM_COCKPIT_DATABASE_URL
  gemini_key_env: OM_COCKPIT_GEMINI_API_KEY
  n8n_api_url_env: OM_COCKPIT_N8N_API_URL
  n8n_api_key_env: OM_COCKPIT_N8N_API_KEY
  telegram_token_env: OM_COCKPIT_TELEGRAM_TOKEN
  telegram_chat_id_env: OM_COCKPIT_TELEGRAM_CHAT_ID
modules:
  intel:
    enabled: false
    storage: quick_only
  tender:
    enabled: false
    storage: quick_only
  fb:
    enabled: false
    storage: quick_only
```
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "config.md"
    p.write_text(CFG_MD, encoding="utf-8")
    return load_config(p)


def make_card(
    *,
    member_id="lin-meixing",
    name="林梅杏",
    email="01550@df-recycle.com.tw",
    card_id="11111111-1111-4111-8111-111111111111",
    review_status="draft",
    q_title="靠行系統部署驗收範圍",
    q_body="請整理四入口行為的驗收清單，附磅單測試結果。",
    evidence_hint="git:abc123",
    summary="- 2026-06-04：q2c-margin 已部署。AI 用量：$148.54（Opus 4.8 主力）。",
) -> str:
    return f"""```yaml
card_id: {card_id}
card_version: 1
target_work_date: 2026-06-04
employee:
  member_id: {member_id}
  name: {name}
  email: {email}
review_status: {review_status}
questions:
  - id: Q1
    title: {q_title}
    evidence_hint: "{evidence_hint}"
```

## 卡 1 — {name}

### 主管看到的本週工作
{summary}

### 主管想了解的 1 件事

#### Q1. {q_title}
{q_body}

### 回覆建議
每題用 CC 查證後回 100-200 字，附 commit hash。
"""


def bundle(*cards: str) -> str:
    fm = "---\ntarget_work_date: 2026-06-04\nfile_type: runtime_cards\n---\n\n# 團隊澄清卡\n\n"
    return fm + "\n---\n\n".join(cards)


# --- B：語氣禁區 ------------------------------------------------------------

def test_accusatory_word_in_question_denies(config):
    card = make_card(q_body="這個卡點為什麼拖到現在還沒解？")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "為什麼" in res.deny_reason


def test_cost_question_denies(config):
    card = make_card(q_body="你昨天花多少錢在 Opus 上？")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "花多少" in res.deny_reason


def test_token_usage_phrase_denies(config):
    card = make_card(q_body="你昨天用了多少 token？")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason is not None


def test_bare_token_engineering_context_allowed(config):
    """bare `token`（OAuth/JWT 工程語境）不該被擋——只禁「問消耗/成本」。"""
    card = make_card(
        q_title="df-graph OAuth token 刷新機制",
        q_body="請說明 df-graph 的 access token 過期後如何自動刷新？",
    )
    res = lint_cards(bundle(card), config)
    assert res.deny_reason is None


def test_ai_usage_summary_not_false_positive(config):
    """advisor #5：H3「主管看到的本週工作」的 AI 用量摘要不該觸發成本禁區，提問乾淨 → 放行。"""
    card = make_card(
        summary="- 2026-06-04：三線並行。AI 用量：$148.54，主因 Opus 跑 POS 覆驗（花了不少錢）。",
        q_body="POS 6 件高優先問題，你建議的執行順序前三名是哪幾件？",
    )
    res = lint_cards(bundle(card), config)
    assert res.deny_reason is None


def test_extract_question_segments_excludes_h3_summary():
    body = (
        "### 主管看到的本週工作\n- AI 用量：$148.54，花了不少錢\n\n"
        "### 主管想了解的 1 件事\n\n#### Q1. 排序\n請給出前三名。\n\n"
        "### 回覆建議\n附 commit hash。\n"
    )
    seg = extract_question_segments({"questions": [{"title": "排序"}]}, body)
    assert "AI 用量" not in seg
    assert "回覆建議" not in seg
    assert "請給出前三名" in seg


def test_h5_orphan_under_h3_excluded():
    # M-1：H3 下的孤兒 H5（無前置 H4）不算提問區塊 → 不掃
    body = "### 摘要\n##### 子節\n為什麼這樣\n\n#### Q1. 題\n請說明。\n"
    seg = extract_question_segments({"questions": []}, body)
    assert "為什麼這樣" not in seg
    assert "請說明" in seg


def test_h5_under_h4_included():
    # H4 提問下的 H5 子小節仍算提問區塊 → 要掃
    body = "#### Q1. 題\n##### 細項\n為什麼要這樣做\n"
    seg = extract_question_segments({"questions": []}, body)
    assert "為什麼要這樣做" in seg


def test_cost_duo_phrase_denies(config):
    card = make_card(q_body="這個專案到底花了這麼多錢值得嗎？")
    assert lint_cards(bundle(card), config).deny_reason is not None


def test_savings_topic_not_false_positive(config):
    # M-2：談「省錢/花力氣」不該誤判（無「花…多…錢」也無「花多少」）
    card = make_card(
        q_title="回收站省錢方案",
        q_body="關於降本，你建議哪些方式可以省錢？要花力氣導入哪些工具？",
    )
    assert lint_cards(bundle(card), config).deny_reason is None


# --- B：必填欄位 ------------------------------------------------------------

def test_missing_card_id_denies(config):
    raw = make_card().replace(
        "card_id: 11111111-1111-4111-8111-111111111111\n", ""
    )
    res = lint_cards(bundle(raw), config)
    assert res.deny_reason and "card_id" in res.deny_reason


def test_missing_evidence_hint_denies(config):
    raw = make_card().replace('    evidence_hint: "git:abc123"\n', "")
    res = lint_cards(bundle(raw), config)
    assert res.deny_reason and "evidence_hint" in res.deny_reason


def test_missing_employee_email_denies(config):
    raw = make_card().replace("  email: 01550@df-recycle.com.tw\n", "")
    res = lint_cards(bundle(raw), config)
    assert res.deny_reason and "employee.email" in res.deny_reason


# --- C-寫檔：member / email 一致性 ------------------------------------------

def test_member_email_mismatch_denies(config):
    # member_id=roy-you 但用林梅杏的 email（email 在 roster 但指向別人）→ 串錯人
    card = make_card(member_id="roy-you", name="游宗霖", email="01550@df-recycle.com.tw")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "錯配" in res.deny_reason


def test_email_not_in_roster_denies(config):
    card = make_card(member_id="lin-meixing", name="林梅杏", email="stranger@evil.example")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "roster" in res.deny_reason


def test_member_id_not_in_config_denies(config):
    card = make_card(member_id="ghost", name="幽靈", email="01550@df-recycle.com.tw")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "member_id" in res.deny_reason


def test_name_mismatch_denies(config):
    card = make_card(member_id="lin-meixing", name="林沒杏", email="01550@df-recycle.com.tw")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason and "name" in res.deny_reason


def test_unverified_member_warns_not_denies(config):
    # roy-you verified:false，三者一致 → warn 放行
    card = make_card(member_id="roy-you", name="游宗霖", email="royyou@df-recycle.com")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason is None
    assert any(w["member_id"] == "roy-you" for w in res.warnings)


def test_verified_member_passes_clean(config):
    card = make_card(member_id="lin-meixing", name="林梅杏", email="01550@df-recycle.com.tw")
    res = lint_cards(bundle(card), config)
    assert res.deny_reason is None
    assert res.warnings == []


def test_no_config_skips_c_check_but_keeps_b():
    # config=None：C-email 跳過，但語氣仍擋
    bad = make_card(q_body="這到底為什麼還沒做完？")
    assert lint_cards(bundle(bad), None).deny_reason is not None
    # 乾淨卡 + 無 config → 放行（不因缺 config 而誤擋）
    good = make_card()
    assert lint_cards(bundle(good), None).deny_reason is None


# --- malformed ---------------------------------------------------------------

def test_malformed_yaml_raises(config):
    broken = "```yaml\ncard_id: [unclosed\n  bad: : :\n```\n\n## 卡\n內容\n"
    with pytest.raises(MalformedCardError):
        lint_cards(bundle(broken), config)


def test_missing_pyyaml_raises_importerror_not_malformed(monkeypatch):
    """advisor #4：runtime 缺 PyYAML → ImportError 往上拋（外層 fail-open 放行），
    **不可**當 MalformedCardError（否則守衛缺件反而 deny 所有卡片寫入）。"""
    import _coaching_hooklib as lib
    monkeypatch.setitem(sys.modules, "yaml", None)  # 令 `import yaml` 拋 ImportError
    with pytest.raises(ImportError):
        lib.parse_cards("```yaml\ncard_id: x\n```\n\nbody\n")


# --- parser 對齊寄送端 ------------------------------------------------------

def test_card_yaml_block_regex_matches_sender():
    """CARD_YAML_BLOCK 必須與 om-daily-work-log/send_coaching_cards.py 逐字相同。"""
    sender = ROOT.parent / "om-daily-work-log" / "scripts" / "send_coaching_cards.py"
    sys.path.insert(0, str(sender.parent))
    import send_coaching_cards  # noqa: E402

    assert CARD_YAML_BLOCK.pattern == send_coaching_cards.CARD_YAML_BLOCK.pattern
    assert CARD_YAML_BLOCK.flags == send_coaching_cards.CARD_YAML_BLOCK.flags


def test_parse_cards_multi(config):
    c1 = make_card(member_id="lin-meixing", name="林梅杏", email="01550@df-recycle.com.tw")
    c2 = make_card(
        member_id="xiao-xinping", name="蕭欣萍", email="01415@df-recycle.com.tw",
        card_id="22222222-2222-4222-8222-222222222222",
    )
    cards = parse_cards(bundle(c1, c2))
    assert len(cards) == 2
    assert cards[0]["yaml"]["employee"]["member_id"] == "lin-meixing"
    assert cards[1]["yaml"]["employee"]["member_id"] == "xiao-xinping"
