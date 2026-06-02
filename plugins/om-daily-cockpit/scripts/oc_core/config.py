"""om-daily-cockpit config loader / validator。

設計鏡像 om-meeting-tracker 的 mt_core/config.py：
- 只讀 markdown 內 ```oc-config fenced block（敘事說明可寫在區塊外）。
- YAML parse + dataclass schema validation，錯誤訊息明確。
- `services.*_env` 只接受「環境變數名稱」，並全域掃描密鑰特徵 → 強制 config 內零密鑰。
- 提供 `--config` / `OM_DAILY_COCKPIT_CONFIG` 自動解析入口給各 script 共用。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("[ERROR] 缺 PyYAML：pip install PyYAML")


ENV_VAR_NAME = "OM_DAILY_COCKPIT_CONFIG"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_FENCE = re.compile(r"^```oc-config[ \t]*\n(.*?)\n```[ \t]*$", re.DOTALL | re.MULTILINE)
_VALID_STORAGE = {"quick_only", "sqlite", "postgres"}
_MODULE_KEYS = ("intel", "tender", "fb")

# 密鑰特徵：config 內任一字串值命中即拒絕（強制 secret 走 env、不進 config）。
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),        # Google / Gemini API key
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT（含 n8n API key）
    re.compile(r"postgres(?:ql)?://[^/\s]+:[^@\s]+@"),         # 連線字串含密碼
    re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),  # Slack token（保險）
]


class ConfigError(ValueError):
    """config.md 解析或 schema validation 失敗。"""


@dataclass
class Identity:
    department: str
    company: str
    persona: str


@dataclass
class Member:
    member_id: str
    name: str
    email: str
    alias_allowlist: list[str] = field(default_factory=list)

    def all_emails(self) -> list[str]:
        """主 email + 所有 alias（小寫化），供嚴格比對用。"""
        return [self.email.lower(), *(a.lower() for a in self.alias_allowlist)]


@dataclass
class EmailCfg:
    adapter: str
    account: str
    daily_report_folder: str
    processed_category: str


@dataclass
class Paths:
    archive_dir: str
    daily_proposal_dir: str


@dataclass
class Directive:
    subject_prefix: str
    marker: str


@dataclass
class Services:
    database_url_env: str
    gemini_key_env: str
    n8n_api_url_env: str
    n8n_api_key_env: str
    telegram_token_env: str
    telegram_chat_id_env: str


@dataclass
class Module:
    key: str
    enabled: bool
    storage: str
    sources: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    org_ids: list[str] = field(default_factory=list)


@dataclass
class Config:
    schema_version: int
    tenant_id: str
    timezone: str
    identity: Identity
    members: list[Member]
    email: EmailCfg
    paths: Paths
    directive: Directive
    services: Services
    modules: dict[str, Module]

    def member_by_email(self, addr: str) -> Member | None:
        """嚴格 email 比對找成員（含 alias），找不到回 None。防多屬下同主旨串錯人。"""
        addr_l = (addr or "").strip().lower()
        if not addr_l:
            return None
        for m in self.members:
            if addr_l in m.all_emails():
                return m
        return None


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def extract_config_block(md_text: str) -> str:
    blocks = _FENCE.findall(md_text)
    if len(blocks) == 0:
        raise ConfigError("找不到 oc-config 區塊（需恰好一個 ```oc-config fenced block）")
    if len(blocks) > 1:
        raise ConfigError(f"找到 {len(blocks)} 個 oc-config 區塊，必須恰好一個")
    return blocks[0]


def _check_path(label: str, p: str) -> None:
    if p.startswith("/") or ".." in Path(p).parts:
        raise ConfigError(f"paths.{label} 不可為絕對路徑或含 '..'：{p!r}")


def _scan_secrets(node: object, trail: str = "") -> None:
    """遞迴掃描 config 內所有字串值，命中密鑰特徵即拒絕。"""
    if isinstance(node, str):
        for pat in _SECRET_PATTERNS:
            if pat.search(node):
                raise ConfigError(
                    f"config 內疑似含密鑰（{trail or '<root>'}）——secret 一律放 env，"
                    f"config 只填環境變數名稱"
                )
    elif isinstance(node, dict):
        for k, v in node.items():
            _scan_secrets(v, f"{trail}.{k}" if trail else str(k))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _scan_secrets(v, f"{trail}[{i}]")


def load_config(md_path: Path) -> Config:
    raw = extract_config_block(Path(md_path).read_text(encoding="utf-8"))
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError("oc-config 區塊不是合法的 mapping")
    _scan_secrets(data)
    return _build_config(data)


def _require_str(d: dict, label: str) -> str:
    v = d.get(label)
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{label} 必填且須為非空字串")
    return v.strip()


def _build_config(d: dict) -> Config:
    if d.get("schema_version") != 1:
        raise ConfigError(f"schema_version 必須為 1，得到 {d.get('schema_version')!r}")

    tenant_id = str(d.get("tenant_id", ""))
    if not _SLUG.match(tenant_id):
        raise ConfigError(f"tenant_id 非法 slug：{tenant_id!r}")

    tz = str(d.get("timezone", ""))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError, ModuleNotFoundError):
        raise ConfigError(f"timezone 無法解析：{tz!r}")

    # identity
    idd = d.get("identity") or {}
    if not isinstance(idd, dict):
        raise ConfigError("identity 必須為 mapping")
    identity = Identity(
        _require_str(idd, "department"),
        _require_str(idd, "company"),
        _require_str(idd, "persona"),
    )

    # team.members
    team = d.get("team") or {}
    members: list[Member] = []
    seen_member: set[str] = set()
    seen_email: set[str] = set()
    for m in (team.get("members") or []):
        mid = str(m.get("member_id", ""))
        if not _SLUG.match(mid):
            raise ConfigError(f"member_id 非法 slug：{mid!r}")
        if mid in seen_member:
            raise ConfigError(f"member_id 重複：{mid!r}")
        seen_member.add(mid)
        if not str(m.get("name", "")).strip():
            raise ConfigError(f"member {mid} 缺 name")
        email = str(m.get("email", ""))
        if not _EMAIL.match(email):
            raise ConfigError(f"member {mid} email 非法：{email!r}")
        if email.lower() in seen_email:
            raise ConfigError(f"member email 重複：{email!r}")
        seen_email.add(email.lower())
        aliases = m.get("alias_allowlist") or []
        for a in aliases:
            if not _EMAIL.match(str(a)):
                raise ConfigError(f"member {mid} alias 非法 email：{a!r}")
        members.append(Member(mid, m["name"], email, [str(a) for a in aliases]))
    if not members:
        raise ConfigError("至少要一個 team.member")

    # email
    em = d.get("email") or {}
    adapter = str(em.get("adapter", ""))
    if adapter != "outlook_local":
        raise ConfigError(f"email.adapter MVP 只支援 outlook_local，得到 {adapter!r}")
    account = str(em.get("account", ""))
    if not _EMAIL.match(account):
        raise ConfigError(f"email.account 非法 email：{account!r}")
    email_cfg = EmailCfg(
        adapter,
        account,
        _require_str(em, "daily_report_folder"),
        _require_str(em, "processed_category"),
    )

    # paths
    p = d.get("paths") or {}
    for k in ("archive_dir", "daily_proposal_dir"):
        if not p.get(k):
            raise ConfigError(f"paths.{k} 必填")
        _check_path(k, str(p[k]))
    paths = Paths(p["archive_dir"], p["daily_proposal_dir"])

    # directive
    dr = d.get("directive") or {}
    directive = Directive(
        _require_str(dr, "subject_prefix"),
        _require_str(dr, "marker"),
    )

    # services（只接受 env 變數名稱）
    sv = d.get("services") or {}
    svc_fields = (
        "database_url_env", "gemini_key_env", "n8n_api_url_env",
        "n8n_api_key_env", "telegram_token_env", "telegram_chat_id_env",
    )
    svc_vals: dict[str, str] = {}
    for k in svc_fields:
        val = str(sv.get(k, ""))
        if not _ENV_NAME.match(val):
            raise ConfigError(
                f"services.{k} 必須是環境變數名稱（大寫底線，如 OM_COCKPIT_X），"
                f"不可是值：{val!r}"
            )
        svc_vals[k] = val
    services = Services(**svc_vals)

    # modules
    mods_in = d.get("modules") or {}
    modules: dict[str, Module] = {}
    for key in _MODULE_KEYS:
        mc = mods_in.get(key) or {}
        enabled = mc.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"modules.{key}.enabled 必須是 true/false")
        storage = str(mc.get("storage", "quick_only"))
        if storage not in _VALID_STORAGE:
            raise ConfigError(
                f"modules.{key}.storage 非法（{storage!r}），"
                f"須為 {sorted(_VALID_STORAGE)}"
            )
        if enabled and storage == "postgres" and not services.database_url_env:
            raise ConfigError(
                f"modules.{key} 啟用且 storage=postgres，但 services.database_url_env 未設"
            )
        modules[key] = Module(
            key=key,
            enabled=enabled,
            storage=storage,
            sources=[str(x) for x in (mc.get("sources") or [])],
            keywords=[str(x) for x in (mc.get("keywords") or [])],
            org_ids=[str(x) for x in (mc.get("org_ids") or [])],
        )

    return Config(
        schema_version=1,
        tenant_id=tenant_id,
        timezone=tz,
        identity=identity,
        members=members,
        email=email_cfg,
        paths=paths,
        directive=directive,
        services=services,
        modules=modules,
    )


# ---------------------------------------------------------------------------
# script 共用入口
# ---------------------------------------------------------------------------

def resolve_config_path(cli_path: str | None) -> Path:
    """依序：--config 參數 > OM_DAILY_COCKPIT_CONFIG env。都沒有則報錯。"""
    candidate = cli_path or os.environ.get(ENV_VAR_NAME)
    if not candidate:
        raise ConfigError(
            f"未提供 config：請給 --config <path> 或設環境變數 {ENV_VAR_NAME}"
        )
    path = Path(candidate)
    if not path.is_file():
        raise ConfigError(f"config 檔不存在：{path}")
    return path


def load_from_cli(cli_path: str | None) -> Config:
    """各 script 共用：解析 config 路徑 → 載入 + validate。"""
    return load_config(resolve_config_path(cli_path))


def require_env(name: str) -> str:
    """讀取 secret 環境變數，缺少時給明確錯誤（不印出值）。"""
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"缺環境變數 {name}（secret 應放 env，不進 config）")
    return val


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="om-daily-cockpit config 工具")
    parser.add_argument("--validate", metavar="CONFIG_MD", help="驗證 config.md")
    args = parser.parse_args()

    if args.validate:
        try:
            cfg = load_config(Path(args.validate))
        except ConfigError as e:
            print(f"[config 不合法] {e}", file=sys.stderr)
            sys.exit(1)
        enabled = [k for k, m in cfg.modules.items() if m.enabled]
        print(
            f"OK：tenant={cfg.tenant_id} members={len(cfg.members)} "
            f"enabled_modules={enabled or '無（核心模組）'}"
        )
        sys.exit(0)
    parser.print_help()
    sys.exit(2)
