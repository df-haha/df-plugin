from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("[ERROR] 缺 PyYAML：pip install PyYAML")


class ConfigError(ValueError):
    """config.md 解析或 schema validation 失敗。"""


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FENCE = re.compile(r"^```mt-config[ \t]*\n(.*?)\n```[ \t]*$", re.DOTALL | re.MULTILINE)
_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_MEETING_DAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}


@dataclass
class Paths:
    tracking_file: str
    draft_dir: str
    context_dir: str
    state_file: str
    run_log_dir: str


@dataclass
class SendCfg:
    adapter: str


@dataclass
class Owner:
    owner_id: str
    name: str
    email: str
    alias_allowlist: list[str] = field(default_factory=list)
    tier: int = 1   # 1=人工屬下（v1）| 2=AI 屬下（v1.5 啟用）；v1 驗證但不分支


@dataclass
class Metric:
    metric_id: str
    owner_id: str
    title: str
    deadline: date
    cadence: str
    meeting_id: str


@dataclass
class Config:
    schema_version: int
    tenant_id: str
    timezone: str
    week_start: str
    meeting_day: str
    business_days: list[str]
    paths: Paths
    send: SendCfg
    owners: list[Owner]
    metrics: list[Metric]
    state_backend: str = "git_branch"   # git_branch（預設）| postgres


def extract_config_block(md_text: str) -> str:
    blocks = _FENCE.findall(md_text)
    if len(blocks) == 0:
        raise ConfigError("找不到 mt-config 區塊（需恰好一個 ```mt-config fenced block）")
    if len(blocks) > 1:
        raise ConfigError(f"找到 {len(blocks)} 個 mt-config 區塊，必須恰好一個")
    return blocks[0]


def _check_path(label: str, p: str) -> None:
    if p.startswith("/") or ".." in Path(p).parts:
        raise ConfigError(f"paths.{label} 不可為絕對路徑或含 '..'：{p!r}")


def _valid_cadence(c: str) -> bool:
    if c in {"daily", "business_days", "overdue_only"}:
        return True
    if c.startswith("snooze:"):
        try:
            date.fromisoformat(c.split(":", 1)[1])
            return True
        except ValueError:
            return False
    return False


def load_config(md_path: Path) -> Config:
    raw = extract_config_block(Path(md_path).read_text(encoding="utf-8"))
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError("mt-config 區塊不是合法的 mapping")
    return _build_config(data)


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

    if d.get("week_start") != "monday":
        raise ConfigError("week_start 目前只支援 monday")
    if d.get("meeting_day") not in _MEETING_DAYS:
        raise ConfigError(f"meeting_day 非法：{d.get('meeting_day')!r}")

    bdays = d.get("business_days") or []
    if not bdays or any(x not in _WEEKDAYS for x in bdays):
        raise ConfigError(f"business_days 非法：{bdays!r}")

    p = d.get("paths") or {}
    for k in ("tracking_file", "draft_dir", "context_dir", "state_file", "run_log_dir"):
        if not p.get(k):
            raise ConfigError(f"paths.{k} 必填")
        _check_path(k, str(p[k]))
    paths = Paths(p["tracking_file"], p["draft_dir"], p["context_dir"], p["state_file"], p["run_log_dir"])

    send = d.get("send") or {}
    if send.get("adapter") not in {"n8n_webhook", "gmail_smtp"}:
        raise ConfigError(f"send.adapter 非法：{send.get('adapter')!r}")
    send_cfg = SendCfg(send["adapter"])

    state_backend = str(d.get("state_backend", "git_branch"))
    if state_backend not in {"git_branch", "postgres"}:
        raise ConfigError(f"state_backend 非法：{state_backend!r}")

    owners: list[Owner] = []
    seen_owner: set[str] = set()
    for o in d.get("owners") or []:
        oid = str(o.get("owner_id", ""))
        if not _SLUG.match(oid):
            raise ConfigError(f"owner_id 非法 slug：{oid!r}")
        if oid in seen_owner:
            raise ConfigError(f"owner_id 重複：{oid!r}")
        seen_owner.add(oid)
        if not str(o.get("name", "")).strip():
            raise ConfigError(f"owner {oid} 缺 name")
        if not _EMAIL.match(str(o.get("email", ""))):
            raise ConfigError(f"owner {oid} email 非法：{o.get('email')!r}")
        aliases = o.get("alias_allowlist") or []
        for a in aliases:
            if not _EMAIL.match(str(a)):
                raise ConfigError(f"owner {oid} alias 非法 email：{a!r}")
        tier = o.get("tier", 1)
        if tier not in (1, 2):
            raise ConfigError(f"owner {oid} tier 非法（只接受 1 或 2）：{tier!r}")
        owners.append(Owner(oid, o["name"], o["email"], list(aliases), tier))
    if not owners:
        raise ConfigError("至少要一個 owner")

    metrics: list[Metric] = []
    seen_metric: set[str] = set()
    for m in d.get("metrics") or []:
        mid = str(m.get("metric_id", ""))
        if not _SLUG.match(mid):
            raise ConfigError(f"metric_id 非法 slug：{mid!r}")
        if mid in seen_metric:
            raise ConfigError(f"metric_id 重複：{mid!r}")
        seen_metric.add(mid)
        if m.get("owner_id") not in seen_owner:
            raise ConfigError(f"metric {mid} 的 owner_id 不存在：{m.get('owner_id')!r}")
        if not str(m.get("title", "")).strip():
            raise ConfigError(f"metric {mid} 缺 title")
        try:
            dl = date.fromisoformat(str(m.get("deadline", "")))
        except ValueError:
            raise ConfigError(f"metric {mid} deadline 非法日期：{m.get('deadline')!r}")
        if not _valid_cadence(str(m.get("cadence", ""))):
            raise ConfigError(f"metric {mid} cadence 非法：{m.get('cadence')!r}")
        if not _SLUG.match(str(m.get("meeting_id", ""))):
            raise ConfigError(f"metric {mid} meeting_id 非法 slug：{m.get('meeting_id')!r}")
        metrics.append(Metric(mid, m["owner_id"], m["title"], dl, m["cadence"], m["meeting_id"]))
    if not metrics:
        raise ConfigError("至少要一個 metric")

    return Config(1, tenant_id, tz, "monday", d["meeting_day"], list(bdays),
                  paths, send_cfg, owners, metrics, state_backend)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="meeting-tracker config 工具")
    parser.add_argument("--validate", metavar="CONFIG_MD", help="驗證 config.md")
    args = parser.parse_args()

    if args.validate:
        try:
            cfg = load_config(Path(args.validate))
        except ConfigError as e:
            print(f"[config 不合法] {e}", file=sys.stderr)
            sys.exit(1)
        print(f"OK：tenant={cfg.tenant_id} owners={len(cfg.owners)} metrics={len(cfg.metrics)}")
        sys.exit(0)
    parser.print_help()
    sys.exit(2)
