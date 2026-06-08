"""om-daily-cockpit coaching hook 共用核心。

三個 hook（stop_coaching_guard / coaching_card_lint / coaching_send_gate）的純函式邏輯
集中於此，方便單元測試（hook 入口只負責 stdin/stdout I/O + fail-open 包裝）。

設計要點：
- **parser 對齊寄送端**：`CARD_YAML_BLOCK` 與 om-daily-work-log/send_coaching_cards.py 同一份
  regex（test_parser_alignment 釘住 `.pattern` 相等）。但**不**沿用寄送端「malformed → skip」
  行為——lint 對 malformed 卡片一律 deny（守衛要更嚴，不能讓壞卡靜默通過）。
- **提問段落鎖定**：tone lint 只掃 yaml `questions[].title` + body 的 H4（`####`）區塊；
  H1-H3 區（含「主管看到的本週工作」的 AI 用量摘要、卡末 soft note）排除，避免誤判。
- **token 語境**：禁「問 AI 消耗/成本」而非禁 `token` 這個工程字眼（對齊 team-coaching-cards
  SKILL.md 禁區 3 的 SSOT：允許卡末 soft note 提 AI 用量%，只禁把消耗當提問）。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# oc_core.config 在 ../scripts 下；加進 sys.path 供 config 載入用。
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# --- card 切分（regex 對齊 send_coaching_cards.py:42）------------------------
# ```yaml block + 其後 body，到下一個 ```yaml 或檔尾。與寄送端逐字相同（見對齊測試）。
CARD_YAML_BLOCK = re.compile(
    r"^```yaml\n(?P<yaml>.*?)\n```\n+(?P<body>.*?)(?=^```yaml\n|\Z)",
    re.DOTALL | re.MULTILINE,
)

# 卡片檔名樣式（只對 team_coaching_cards_*.md 生效）。
_CARD_FILENAME = re.compile(r"^team_coaching_cards_.*\.md$")


class MalformedCardError(ValueError):
    """卡片 yaml block 解析失敗——lint 端據此 deny（不像寄送端 skip）。"""


def is_coaching_card_file(file_path: str | None) -> bool:
    if not file_path:
        return False
    return bool(_CARD_FILENAME.match(Path(file_path).name))


def parse_cards(text: str) -> list[dict]:
    """切 bundle md → 卡片清單 [{"yaml": dict, "body_md": str}]。

    與寄送端 parse_bundle 同樣先剝除 bundle frontmatter（檔頭 ``---...---``），再以
    CARD_YAML_BLOCK 抓每張卡。**malformed yaml → raise MalformedCardError**（守衛要 deny，
    不沿用寄送端 skip-with-warn）。
    """
    # import 失敗（runtime interpreter 缺 PyYAML）→ ImportError 往上拋，由 hook 外層 fail-open；
    # **不可**當成 malformed（否則守衛缺件反而 deny 使用者所有卡片寫入）。
    import yaml

    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    body = text[m.end():] if m else text

    cards: list[dict] = []
    for match in CARD_YAML_BLOCK.finditer(body):
        yaml_text = match.group("yaml")
        try:
            card_yaml = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:  # 內容壞 → malformed（lint 端據此 deny）
            raise MalformedCardError(f"卡片 yaml block 解析失敗：{e}") from e
        if card_yaml is None:
            raise MalformedCardError("卡片 yaml block 為空")
        if not isinstance(card_yaml, dict):
            raise MalformedCardError("卡片 yaml block 不是 mapping")
        cards.append({"yaml": card_yaml, "body_md": match.group("body").strip()})
    return cards


# --- 提問段落抽取 -----------------------------------------------------------

def extract_question_segments(card_yaml: dict, body_md: str) -> str:
    """抽「提問段落」供 tone lint：yaml questions[].title + body 的 H4（####）區塊。

    body 中 H1-H3 的標題與內容（如「### 主管看到的本週工作」下的 AI 用量摘要、
    「### 回覆建議」、卡末 soft note）一律排除——它們是給主管看的脈絡，不是對屬下的提問。
    """
    segs: list[str] = []
    for q in (card_yaml.get("questions") or []):
        if isinstance(q, dict):
            title = q.get("title")
            if isinstance(title, str):
                segs.append(title)

    in_question_block = False
    for line in body_md.splitlines():
        heading = re.match(r"^(#{1,6})\s", line)
        if heading:
            level = len(heading.group(1))
            # 提問區塊「只」由 H4（####）起算；H1-H3 結束區塊（含 AI 用量摘要、回覆建議、
            # 卡末 soft note）；H5/H6 維持現狀（H4 提問下的子小節算提問，H3 下的孤兒 H5 不算）。
            if level <= 3:
                in_question_block = False
            elif level == 4:
                in_question_block = True
            if in_question_block:
                segs.append(line)
            continue
        if in_question_block:
            segs.append(line)
    return "\n".join(segs)


# --- 語氣禁區（對齊 team-coaching-cards SKILL.md 三禁區）---------------------
# 禁區 1：質疑類 bare words（全部明確是責問語氣）。
TONE_ACCUSATORY_WORDS = ["為什麼", "為何", "跟誰對齊", "跑偏", "失職"]
# 禁區 3：AI 成本/用量質問——用「用量語境」pattern，不禁 bare `token`。
# （SKILL.md:81-82：禁「問消耗/成本」，但 token 是合法工程字眼且允許卡末 soft note 提用量%。）
TONE_COST_PATTERNS = [
    re.compile(r"多少.{0,4}token", re.IGNORECASE),
    re.compile(r"token.{0,6}(用量|數|花|成本|費)", re.IGNORECASE),
    re.compile(r"用了.{0,8}token", re.IGNORECASE),
    re.compile(r"AI\s*成本"),
    re.compile(r"花多少"),            # 花多少錢 / 花多少 token
    re.compile(r"花.{0,4}多.{0,4}錢"),  # 花這麼多錢 / 花了這麼多錢（要有「多」量詞，避免誤判「省錢」）
    re.compile(r"用量太高"),
    re.compile(r"成本.{0,4}太高"),
]


def find_tone_violation(question_text: str) -> str | None:
    """提問段落命中任一禁用詞/pattern → 回該命中字串；無則 None。"""
    for word in TONE_ACCUSATORY_WORDS:
        if word in question_text:
            return word
    for pat in TONE_COST_PATTERNS:
        m = pat.search(question_text)
        if m:
            return m.group(0)
    return None


# --- 必填欄位 ---------------------------------------------------------------

def check_required_fields(card_yaml: dict) -> list[str]:
    """回傳缺漏的必填欄位清單（空 = 齊全）。"""
    missing: list[str] = []
    for key in ("card_id", "card_version", "target_work_date", "review_status"):
        if card_yaml.get(key) in (None, ""):
            missing.append(key)
    emp = card_yaml.get("employee")
    if not isinstance(emp, dict):
        missing.append("employee")
    else:
        for k in ("member_id", "name", "email"):
            if not str(emp.get(k, "")).strip():
                missing.append(f"employee.{k}")
    questions = card_yaml.get("questions")
    if not isinstance(questions, list) or not questions:
        missing.append("questions[]")
    else:
        for i, q in enumerate(questions, 1):
            if not isinstance(q, dict) or not str(q.get("evidence_hint", "")).strip():
                missing.append(f"questions[{i}].evidence_hint")
    return missing


# --- member / email 一致性（防串錯人）---------------------------------------

@dataclass
class ConsistencyVerdict:
    kind: str            # "ok" | "deny" | "warn"
    member_id: str = ""
    msg: str = ""


def check_member_consistency(card_yaml: dict, config) -> ConsistencyVerdict:
    """檢查卡片 employee.{member_id,name,email} 是否指向同一 config member。

    - member_id 不在 config / email 不在任何 roster / 兩者指向不同人 / name 不符 → deny。
    - 三者一致但該 member 主 email 尚未 verified → warn（放行，提醒實寄前先標 verified）。
    """
    emp = card_yaml.get("employee") or {}
    member_id = str(emp.get("member_id", "")).strip()
    email = str(emp.get("email", "")).strip()
    name = str(emp.get("name", "")).strip()

    m_by_id = next((m for m in config.members if m.member_id == member_id), None)
    m_by_email = config.member_by_email(email)

    if m_by_id is None:
        return ConsistencyVerdict("deny", member_id, f"member_id {member_id!r} 不在 config team.members")
    if m_by_email is None:
        return ConsistencyVerdict("deny", member_id, f"email {email!r} 不在任何成員 roster（防串錯人）")
    if m_by_id.member_id != m_by_email.member_id:
        return ConsistencyVerdict(
            "deny", member_id,
            f"member_id 指向 {m_by_id.name}、email 指向 {m_by_email.name}——錯配，疑似串錯人",
        )
    if name and name != m_by_id.name:
        return ConsistencyVerdict(
            "deny", member_id,
            f"name {name!r} 與 config 中 {member_id} 的 {m_by_id.name!r} 不符",
        )
    if not m_by_id.is_verified(email):
        return ConsistencyVerdict(
            "warn", member_id,
            f"{m_by_id.name} 的 email {email} 尚未 verified——實寄前請先在 config 標 verified: true",
        )
    return ConsistencyVerdict("ok", member_id)


# --- lint 主邏輯 ------------------------------------------------------------

@dataclass
class LintResult:
    deny_reason: str | None = None
    warnings: list[dict] = field(default_factory=list)  # [{"member_id", "msg"}]


def lint_cards(content: str, config=None) -> LintResult:
    """對整份卡片內容做 B（語氣 + 欄位）+ C-寫檔（email/member 一致）檢查。

    config=None（找不到 tenant config）→ 跳過 C-email 一致性，仍做語氣 + 欄位。
    malformed 由 parse_cards raise，caller 轉 deny。
    """
    cards = parse_cards(content)
    result = LintResult()
    for idx, card in enumerate(cards, 1):
        y = card["yaml"]
        missing = check_required_fields(y)
        if missing:
            result.deny_reason = f"卡 {idx} 缺必填欄位：{', '.join(missing)}"
            return result
        hit = find_tone_violation(extract_question_segments(y, card["body_md"]))
        if hit:
            result.deny_reason = (
                f"卡 {idx} 提問含禁用詞「{hit}」（語氣禁區：提問非質疑、不把 AI 消耗當提問）"
            )
            return result
        if config is not None:
            verdict = check_member_consistency(y, config)
            if verdict.kind == "deny":
                result.deny_reason = f"卡 {idx}（{y.get('employee', {}).get('name', '?')}）{verdict.msg}"
                return result
            if verdict.kind == "warn":
                result.warnings.append({"member_id": verdict.member_id, "msg": verdict.msg})
    return result


# --- config 定位（env → 從起點往上 walk 找 .om-cockpit/config.md）-----------

def find_config_path(start: Path) -> Path | None:
    env = os.environ.get("OM_DAILY_COCKPIT_CONFIG")
    if env and Path(env).is_file():
        return Path(env)
    try:
        cur = start.resolve()
    except OSError:
        return None
    for d in [cur, *cur.parents]:
        cand = d / ".om-cockpit" / "config.md"
        if cand.is_file():
            return cand
    return None


def load_config_safe(path: Path):
    """載入 config；任何錯誤回 None（fail-open，守衛不因 config 壞而誤擋）。"""
    try:
        from oc_core.config import load_config
        return load_config(path)
    except Exception:  # noqa: BLE001
        return None
