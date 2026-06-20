#!/usr/bin/env python3
"""devlog — repo 決策日誌的決定性 helper。

設計理念：把「絕不可飄」的 bookkeeping 交給腳本，把需要判斷的 prose body 留給 LLM。
腳本負責：ADR id 配號、從模板建檔、LOG.md append（與檔案同步、不重複狀態）、sensitivity 掃描。
僅用 Python 標準函式庫，任何 repo 皆可跑。

⚠️ sensitivity scan 定位：它是「盡力而為的最後一道薄網」，不是個資合規控制。
它一定漏自由文字 PII（中文姓名、地址、病情描述、組合型再識別）。含特種個資的 repo 必須
另加 repo 層治理（CI required check、成熟 scanner、CODEOWNERS 隱私審、誤寫 purge runbook），
詳見 skill 的 references/adopting-in-a-repo.md。

子指令：
  init    在 repo 內建立 docs/devlog/（LOG.md + config.json + decisions/ + .gitignore）
  next-id 印出下一個 ADR id（不建檔；僅提示，非保留，真正配號在 new 的鎖內完成）
  new     建立一筆 ADR（鎖內：配號 + exclusive 建檔 + append LOG），印出檔案路徑
  scan    對檔案跑 sensitivity 掃描（依 config 的 profile），有命中則 exit 1
"""
import argparse
import contextlib
import datetime
import json
import os
import re
import sys

try:
    import fcntl
    HAVE_FCNTL = True
except ImportError:  # 非 POSIX（如 Windows）退化為無鎖；單人使用仍可運作
    HAVE_FCNTL = False

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PLUGIN_ROOT, "assets")

DEFAULT_CONFIG = {
    "profile": "secrets-only",   # none | secrets-only | pii | custom
    "id_prefix": "ADR",
    "custom_patterns": [],        # 任何非 none profile 都會額外套用（regex 字串）
}

# ----------------------------------------------------------------------------
# 路徑 / 設定
# ----------------------------------------------------------------------------

def devlog_dir(repo_root):
    return os.path.join(repo_root, "docs", "devlog")


def decisions_dir(repo_root):
    return os.path.join(devlog_dir(repo_root), "decisions")


def log_path(repo_root):
    return os.path.join(devlog_dir(repo_root), "LOG.md")


def config_path(repo_root):
    return os.path.join(devlog_dir(repo_root), "config.json")


def load_config(repo_root):
    cfg = dict(DEFAULT_CONFIG)
    p = config_path(repo_root)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def today():
    return datetime.date.today().isoformat()


# ----------------------------------------------------------------------------
# 並行鎖：把「算 id → exclusive 建檔 → append LOG」包成單一 critical section
# ----------------------------------------------------------------------------

@contextlib.contextmanager
def devlog_lock(repo_root):
    os.makedirs(devlog_dir(repo_root), exist_ok=True)
    f = open(os.path.join(devlog_dir(repo_root), ".lock"), "w")
    try:
        if HAVE_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if HAVE_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


# ----------------------------------------------------------------------------
# id / slug / yaml
# ----------------------------------------------------------------------------

ID_RE = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<num>\d{1,})")


def max_id(decisions, prefix):
    mx = 0
    if os.path.isdir(decisions):
        for name in os.listdir(decisions):
            m = ID_RE.match(name)
            if m and m.group("prefix") == prefix:
                mx = max(mx, int(m.group("num")))
    return mx


def compute_next_id(repo_root):
    cfg = load_config(repo_root)
    prefix = cfg["id_prefix"]
    return f"{prefix}-{max_id(decisions_dir(repo_root), prefix) + 1:04d}"


def slugify(title, fallback="untitled"):
    # 限制檔名字元在 [a-z0-9-]，避免奇怪 slug 造成路徑問題
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title or "").strip("-").lower()
    return s or fallback


def yaml_list(csv):
    """把逗號分隔字串轉成安全的 YAML flow list，避免特殊字元破壞 frontmatter。"""
    items = [x.strip() for x in (csv or "").split(",") if x.strip()]
    if not items:
        return "[]"
    quoted = ['"' + i.replace("\\", "\\\\").replace('"', '\\"') + '"' for i in items]
    return "[" + ", ".join(quoted) + "]"


def yaml_scalar(s):
    """單行 scalar 的安全化：去換行、跳脫雙引號（呼叫端會再包雙引號）。"""
    return (s or "").replace("\n", " ").replace("\r", " ").replace('"', '\\"')


# ----------------------------------------------------------------------------
# sensitivity 掃描
#   邊界用 NB/NA（非 ASCII-alnum 的前後界），而非 \b——\b 在中文相鄰時會失效，
#   例如 "身分證A123456789" 的 A 前面是中文（也算 \w），\b 不成立而漏抓。
# ----------------------------------------------------------------------------

NB = r"(?<![A-Za-z0-9])"   # 前界：前一字元不是 ASCII 英數（中文/標點/行首皆可）
NA = r"(?![A-Za-z0-9])"    # 後界：後一字元不是 ASCII 英數

# 通用機密（secrets-only / pii / custom 皆套用）
SECRET_PATTERNS = [
    ("private-key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ("aws-access-key", NB + r"AKIA[0-9A-Z]{16}" + NA),
    ("github-token", NB + r"gh[pousr]_[A-Za-z0-9]{30,}" + NA),
    ("github-pat-fg", NB + r"github_pat_[A-Za-z0-9_]{30,}" + NA),
    ("slack-token", NB + r"xox[baprs]-[A-Za-z0-9-]{10,}" + NA),
    ("google-api-key", NB + r"AIza[0-9A-Za-z_\-]{35}" + NA),
    ("openai-key", NB + r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}" + NA),
    ("stripe-key", NB + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}" + NA),
    ("jwt", NB + r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}" + NA),
    ("conn-string-cred",
     r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s:@/]+@"),
    ("generic-secret-assign",
     r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{16,}"),
]

# 台灣 PII（pii / custom profile 額外套用）
PII_PATTERNS = [
    ("tw-national-id", NB + r"[A-Za-z][12]\d{8}" + NA),
    ("tw-mobile", NB + r"09\d{8}" + NA),
    ("tw-mobile-sep", NB + r"09\d{2}[-\s]\d{3}[-\s]\d{3}" + NA),
    ("tw-mobile-intl", r"\+?886[-\s]?9\d{2}[-\s]?\d{3}[-\s]?\d{3}"),
    ("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("long-digit-seq", NB + r"\d{9,}" + NA),  # 軟性：可能病歷號/身分證；易誤判流水號
]


def patterns_for(cfg):
    """profile 語意（可組合）：
       none         → 不掃
       secrets-only → secrets + custom_patterns
       pii          → secrets + pii + custom_patterns
       custom       → secrets + pii + custom_patterns（＝ pii 的別名；custom_patterns 不再排擠 pii）
       任何非 none profile 都會套用 custom_patterns。"""
    profile = cfg.get("profile", "secrets-only")
    if profile == "none":
        return []
    pats = list(SECRET_PATTERNS)
    if profile in ("pii", "custom"):
        pats += PII_PATTERNS
    for i, raw in enumerate(cfg.get("custom_patterns", []) or []):
        pats.append((f"custom-{i}", raw))
    compiled = []
    for name, rx in pats:
        try:
            compiled.append((name, re.compile(rx)))
        except re.error as e:
            print(f"[devlog scan] 警告：custom pattern '{rx}' 無法編譯：{e}", file=sys.stderr)
    return compiled


def mask(s):
    s = s.strip()
    if len(s) <= 8:
        return (s[0] + "***") if s else "***"
    return s[:4] + "***" + s[-2:]


def scan_files(files, cfg):
    pats = patterns_for(cfg)
    findings = []
    for fp in files:
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    for name, rx in pats:
                        m = rx.search(line)
                        if m:
                            findings.append((fp, lineno, name, mask(m.group(0))))
        except OSError:
            continue
    return findings


def default_scan_targets(repo_root):
    targets = []
    for root, _dirs, names in os.walk(devlog_dir(repo_root)):
        for n in names:
            if n.endswith(".md"):
                targets.append(os.path.join(root, n))
    return targets


# ----------------------------------------------------------------------------
# 模板 / 建檔
# ----------------------------------------------------------------------------

def read_template(mode):
    fn = "ADR-template-pointer.md" if mode == "pointer" else "ADR-template-full.md"
    with open(os.path.join(ASSETS, fn), encoding="utf-8") as f:
        return f.read()


def fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def append_log(repo_root, adr_id, title, date):
    line = f"## [{date}] decision | {adr_id} | {title}\n"
    lp = log_path(repo_root)
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as f:
            content = f.read()
        if content and not content.endswith("\n"):
            content += "\n"
        if not content.endswith("\n\n"):
            content += "\n"
        content += line
    else:
        content = line
    with open(lp, "w", encoding="utf-8") as f:
        f.write(content)
    return line


def cmd_new(args):
    repo_root = os.path.abspath(args.repo_root)
    os.makedirs(decisions_dir(repo_root), exist_ok=True)
    date = args.date or today()
    slug = slugify(args.slug) if args.slug else slugify(args.title)
    tpl = read_template(args.mode)

    # critical section：序列化「算 id → exclusive 建檔 → append LOG」
    with devlog_lock(repo_root):
        adr_id = compute_next_id(repo_root)
        fpath = os.path.join(decisions_dir(repo_root), f"{adr_id}-{slug}.md")
        body = fill(
            tpl,
            ID=adr_id,
            TITLE=yaml_scalar(args.title),
            STATUS=args.status,
            DATE=date,
            SUPERSEDES=yaml_scalar(args.supersedes),
            RESOLVES=yaml_scalar(args.resolves),
            REFS_YAML=yaml_list(args.refs),
            TAGS_YAML=yaml_list(args.tags),
        )
        try:
            with open(fpath, "x", encoding="utf-8") as f:  # exclusive create
                f.write(body)
        except FileExistsError:
            sys.exit(f"error: {fpath} 已存在，請改 --slug")
        log_line = append_log(repo_root, adr_id, args.title, date)

    print(json.dumps({
        "id": adr_id,
        "file": fpath,
        "mode": args.mode,
        "log_line": log_line.strip(),
    }, ensure_ascii=False))


def cmd_init(args):
    repo_root = os.path.abspath(args.repo_root)
    os.makedirs(decisions_dir(repo_root), exist_ok=True)
    cp = config_path(repo_root)
    if not os.path.exists(cp):
        cfg = dict(DEFAULT_CONFIG)
        cfg["profile"] = args.profile
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    lp = log_path(repo_root)
    if not os.path.exists(lp):
        header_file = os.path.join(ASSETS, "LOG-header.md")
        header = ""
        if os.path.exists(header_file):
            with open(header_file, encoding="utf-8") as f:
                header = f.read()
        with open(lp, "w", encoding="utf-8") as f:
            f.write(header)
    gi = os.path.join(devlog_dir(repo_root), ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(".lock\n")
    actual = load_config(repo_root)  # 回報實際 config 的 profile（非 args，避免不一致）
    print(json.dumps({
        "devlog_dir": devlog_dir(repo_root),
        "profile": actual.get("profile"),
        "log": lp,
        "config": cp,
    }, ensure_ascii=False))


def cmd_next_id(args):
    print(compute_next_id(os.path.abspath(args.repo_root)))


def cmd_scan(args):
    repo_root = os.path.abspath(args.repo_root)
    cfg = load_config(repo_root)
    files = args.files if args.files else default_scan_targets(repo_root)
    findings = scan_files(files, cfg)
    if findings:
        print(f"[devlog scan] profile={cfg.get('profile')} 命中 {len(findings)} 筆敏感樣式：", file=sys.stderr)
        for fp, ln, name, snip in findings:
            print(f"  {fp}:{ln}  [{name}]  {snip}", file=sys.stderr)
        print("\n→ devlog 任何檔/commit/git 歷史不得含機密或個案可識別資訊。"
              "請改用佔位符（<client-id>、<tenant>、<redacted>）後重試。", file=sys.stderr)
        sys.exit(1)
    print(f"[devlog scan] profile={cfg.get('profile')} 乾淨（掃描 {len(files)} 檔）"
          f"｜提醒：scan 抓不到自由文字 PII（中文姓名/地址/病情），勿視為合規保證。")


def build_parser():
    p = argparse.ArgumentParser(prog="devlog", description="repo 決策日誌 helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="在 repo 建立 docs/devlog/")
    pi.add_argument("repo_root")
    pi.add_argument("--profile", default="secrets-only",
                    choices=["none", "secrets-only", "pii", "custom"])
    pi.set_defaults(func=cmd_init)

    pn = sub.add_parser("next-id", help="印出下一個 ADR id（僅提示）")
    pn.add_argument("repo_root")
    pn.set_defaults(func=cmd_next_id)

    pnew = sub.add_parser("new", help="建立一筆 ADR + append LOG（鎖內原子）")
    pnew.add_argument("repo_root")
    pnew.add_argument("--title", required=True)
    pnew.add_argument("--mode", default="full", choices=["full", "pointer"])
    pnew.add_argument("--status", default="proposed",
                      choices=["proposed", "accepted", "superseded", "rejected"])
    pnew.add_argument("--slug", default="")
    pnew.add_argument("--date", default="")
    pnew.add_argument("--supersedes", default="")
    pnew.add_argument("--resolves", default="")
    pnew.add_argument("--refs", default="", help="逗號分隔，安全轉成 frontmatter refs[]")
    pnew.add_argument("--tags", default="", help="逗號分隔，安全轉成 frontmatter tags[]")
    pnew.set_defaults(func=cmd_new)

    ps = sub.add_parser("scan", help="跑 sensitivity 掃描")
    ps.add_argument("repo_root")
    ps.add_argument("--files", nargs="*", help="指定檔案；省略則掃整個 docs/devlog")
    ps.set_defaults(func=cmd_scan)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
