#!/usr/bin/env python3
"""裁決帳本檢查：lint / paths / resurrect。只讀不寫。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path

INDEX_REL = Path("docs") / "裁決帳本.md"
DETAIL_DIR_REL = Path("docs") / "裁決"
VALID_STATUS = ("採用", "打掉", "待裁")
ID_RE = re.compile(r"^R-\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HISTORY_LINE_RE = re.compile(r"^- \d{4}-\d{2}-\d{2}\s")
SECTION_RE = re.compile(r"^##\s*([0A-D])\s*[.．、]")
HARD_ZONE_ENTRY_RE = re.compile(r"^###\s*(?:⚠\s*)?(R-\d{4})\b")
FIELD_RE = re.compile(r"^- \*{0,2}([^：:*]+)\*{0,2}[：:]\s*(.*)$")
PLACEHOLDER_PREFIX = "（"
HEADER_FIRST_CELLS = {"ID", "工作線", "檔"}
ACTIVE_SOFT_LIMIT = 50
HARD_ZONE_LIMIT = 5
TIER_MIN, TIER_MAX = 0, 2
NEGATION_RE = re.compile(r"(沒有|不談|拿掉|打掉|不當|不做|不再|不採|已否決|勿復活|不成立|無關|取消|廢除|不列|不寫)")
REF_ID_RE = re.compile(r"R-\d{4}")
REF_WORD_RE = re.compile(r"(打掉|否決|翻案|已)")
NEG_WINDOW = 12
SCAN_EXTS = {".md", ".txt", ".html", ".pptx", ".docx"}
SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
A_T_RE = re.compile(r"<a:t[^>]*>(.*?)</a:t>", re.S)
W_P_RE = re.compile(r"<w:p(?:\s[^>]*)?/>|<w:p[ >].*?</w:p>", re.S)
W_T_RE = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)


@dataclass
class WhitelistRow:
    line: str
    path: str
    note: str


@dataclass
class DeprecatedRow:
    path: str
    reason: str


@dataclass
class IndexRow:
    id: str
    status: str
    claim: str
    keywords: list = field(default_factory=list)
    tier: int = 0
    resurrect_count: int = 0
    last_resurrection: str = ""
    archived_on: str = ""
    warned: bool = False


@dataclass
class Index:
    whitelist: list = field(default_factory=list)
    deprecated: list = field(default_factory=list)
    hard_zone: list = field(default_factory=list)
    active: list = field(default_factory=list)
    archived: list = field(default_factory=list)
    problems: list = field(default_factory=list)


@dataclass
class Detail:
    id: str
    status: str
    claim: str
    scope: str
    reason: str
    synonyms: list
    review_condition: str
    source: str
    history: list = field(default_factory=list)
    duplicate_fields: list = field(default_factory=list)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _split_terms(s: str) -> list:
    return [t.strip() for t in re.split(r"[、,，;；]", s) if t.strip()]


def _blank(value: str) -> bool:
    v = (value or "").strip()
    return (not v) or v.startswith(PLACEHOLDER_PREFIX)


def _split_cells(line: str) -> list:
    """用未跳脫的 | 切欄；去頭尾空欄；把 \\| 還原成 |。"""
    parts = re.split(r"(?<!\\)\|", line.strip())
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def _table_rows(section_text: str, expect: int, label: str, problems: list) -> list:
    """回傳欄數正確的資料列；跳過表頭列與分隔列，欄數不對進 problems。"""
    rows = []
    for raw in section_text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = _split_cells(line)
        if not cells:
            continue
        if cells[0] in HEADER_FIRST_CELLS:
            continue
        if all(c and set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != expect:
            problems.append(f"{label}: 欄數應為 {expect}，實際 {len(cells)} → {line}")
            continue
        rows.append(cells)
    return rows


def _sections(text: str) -> dict:
    """回傳 {'0': ..., 'A': ..., 'B': ..., 'C': ..., 'D': ...}，依 '## X.' 切段。"""
    out = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if current:
                out[current] = "\n".join(buf)
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf)
    return out


def _split_a(section_text: str) -> tuple:
    head, _, tail = section_text.partition("### 已淘汰檔")
    return head, tail


def parse_index(repo_root: Path) -> Index:
    text = _read(Path(repo_root) / INDEX_REL)
    sec = _sections(text)
    idx = Index()
    for line in sec.get("0", "").splitlines():
        m = HARD_ZONE_ENTRY_RE.match(line.strip())
        if m:
            idx.hard_zone.append(m.group(1))
    a_white, a_dep = _split_a(sec.get("A", ""))
    for cells in _table_rows(a_white, 3, "A 段白名單", idx.problems):
        if "…" in cells[1]:
            continue
        idx.whitelist.append(WhitelistRow(cells[0], cells[1].strip("`"), cells[2]))
    for cells in _table_rows(a_dep, 2, "A 段已淘汰檔", idx.problems):
        idx.deprecated.append(DeprecatedRow(cells[0].strip("`"), cells[1]))
    for cells in _table_rows(sec.get("B", ""), 7, "B 段", idx.problems):
        raw_id = cells[0]
        warned = raw_id.lstrip().startswith("⚠")
        rid = raw_id.lstrip("⚠").strip()
        tier = int(cells[4]) if cells[4].isdigit() else -1
        if tier < 0:
            idx.problems.append(f"{rid}: 層級欄「{cells[4]}」不是數字")
        count = int(cells[5]) if cells[5].isdigit() else -1
        if count < 0:
            idx.problems.append(f"{rid}: 復活欄「{cells[5]}」不是數字")
        idx.active.append(IndexRow(rid, cells[1], cells[2], _split_terms(cells[3]),
                                   tier, count, cells[6], "", warned))
    for cells in _table_rows(sec.get("C", ""), 4, "C 段", idx.problems):
        rid = cells[0].lstrip("⚠").strip()
        idx.archived.append(IndexRow(rid, cells[1], cells[2], [], 0, 0, "", cells[3], False))
    return idx


def parse_detail(path: Path) -> Detail:
    text = _read(Path(path))
    fields = {}
    duplicates = []
    history = []
    current = None
    in_history = False
    for raw in text.splitlines():
        if raw.startswith("## "):
            in_history = "沿革" in raw
            current = None
            continue
        if in_history:
            if raw.strip():
                history.append(raw.rstrip())
            continue
        stripped = raw.strip()
        m = FIELD_RE.match(stripped)
        if m and not raw.startswith("  "):
            key = m.group(1).strip()
            if key in fields:
                duplicates.append(key)
            fields[key] = m.group(2).strip()
            current = key
        elif current and stripped and raw.startswith("  "):
            fields[current] = (fields[current] + " " + stripped).strip()
    title = re.search(r"^# (R-\d{4})\s*(.*)$", text, re.M)
    return Detail(
        id=title.group(1) if title else Path(path).stem,
        status=fields.get("狀態", ""),
        claim=fields.get("命題", ""),
        scope=fields.get("範圍", ""),
        reason=fields.get("理由", ""),
        synonyms=_split_terms(fields.get("同義表達", "")),
        review_condition=fields.get("重審條件", ""),
        source=fields.get("出處", ""),
        history=history,
        duplicate_fields=duplicates,
    )


def lint(repo_root: Path) -> list:
    repo_root = Path(repo_root)
    problems = []
    index_path = repo_root / INDEX_REL
    if not index_path.exists():
        return [f"索引不存在：{index_path}"]
    idx = parse_index(repo_root)
    problems.extend(idx.problems)
    detail_dir = repo_root / DETAIL_DIR_REL
    seen = set()

    for scope, rows in (("B", idx.active), ("C", idx.archived)):
        last = -1
        for row in rows:
            if not ID_RE.match(row.id):
                problems.append(f"{row.id}: ID 格式錯誤（應為 R-NNNN）")
            else:
                num = int(row.id[2:])
                if num <= last:
                    problems.append(f"{row.id}: {scope} 段 ID 未依出現順序遞增（前一條 R-{last:04d}）")
                last = max(last, num)
            if row.id in seen:
                problems.append(f"{row.id}: ID 重複")
            seen.add(row.id)
            if row.status not in VALID_STATUS:
                problems.append(f"{row.id}: 狀態「{row.status}」不合法，只能是 {'/'.join(VALID_STATUS)}")
            if scope == "C" and not DATE_RE.match(row.archived_on):
                problems.append(f"{row.id}: 歸檔日期「{row.archived_on}」不是 YYYY-MM-DD")
            dpath = detail_dir / f"{row.id}.md"
            if not dpath.exists():
                problems.append(f"{row.id}: 缺詳情檔 {DETAIL_DIR_REL / (row.id + '.md')}")
                continue
            d = parse_detail(dpath)
            for dup in d.duplicate_fields:
                problems.append(f"{row.id}: 詳情欄位「{dup}」重複")
            if d.status != row.status:
                problems.append(f"{row.id}: 索引狀態「{row.status}」與詳情「{d.status}」不一致")
            for name, value in (("命題", d.claim), ("範圍", d.scope), ("理由", d.reason), ("出處", d.source)):
                if _blank(value):
                    problems.append(f"{row.id}: 缺必填欄「{name}」")
            if row.status == "打掉":
                if not d.synonyms or _blank(" ".join(d.synonyms)):
                    problems.append(f"{row.id}: 打掉項缺同義表達")
                if _blank(d.review_condition):
                    problems.append(f"{row.id}: 打掉項缺重審條件")
            if not d.history:
                problems.append(f"{row.id}: 詳情缺「## 沿革」內容")
            for h in d.history:
                if not HISTORY_LINE_RE.match(h):
                    problems.append(f"{row.id}: 沿革該行不是「- YYYY-MM-DD 」開頭 → {h}")

    hard_zone = set(idx.hard_zone)
    active_ids = {r.id for r in idx.active}
    for row in idx.active:
        # 層級欄非數字時，parse_index 已報過「不是數字」；此處對該行的層級相關
        # 檢查全部跳過，避免把內部哨兵值 -1 洩漏進第二條訊息。
        if row.tier < 0:
            if row.last_resurrection and not DATE_RE.match(row.last_resurrection):
                problems.append(f"{row.id}: 最近復活「{row.last_resurrection}」不是 YYYY-MM-DD")
            if row.resurrect_count > 0 and not row.last_resurrection:
                problems.append(f"{row.id}: 復活 {row.resurrect_count} 次但「最近復活」欄空白")
            continue
        if row.tier < TIER_MIN or row.tier > TIER_MAX:
            problems.append(f"{row.id}: 層級「{row.tier}」超出 {TIER_MIN}–{TIER_MAX}")
            continue
        if row.tier >= 1 and not row.warned:
            problems.append(f"{row.id}: 層級 {row.tier} 但索引該行 ID 前缺 ⚠")
        if row.tier == 0 and row.warned:
            problems.append(f"{row.id}: 層級 0 但索引該行 ID 前有 ⚠")
        if row.tier == 2 and row.id not in hard_zone:
            problems.append(f"{row.id}: 層級 2 但不在「## 0. 硬禁區」段")
        if row.tier < 2 and row.id in hard_zone:
            problems.append(f"{row.id}: 在硬禁區但層級為 {row.tier}")
        if row.last_resurrection and not DATE_RE.match(row.last_resurrection):
            problems.append(f"{row.id}: 最近復活「{row.last_resurrection}」不是 YYYY-MM-DD")
        if row.resurrect_count > 0 and not row.last_resurrection:
            problems.append(f"{row.id}: 復活 {row.resurrect_count} 次但「最近復活」欄空白")
        if row.resurrect_count >= 2 and row.tier < 2:
            problems.append(f"提醒：{row.id} 復活累計 {row.resurrect_count} 次，提議升 2（移進硬禁區，全文寫命題／範圍／重審條件），需使用者確認")
        elif row.resurrect_count >= 1 and row.tier == 0:
            problems.append(f"提醒：{row.id} 復活累計 {row.resurrect_count} 次，提議升 1（索引該行加 ⚠、同義表達併進關鍵字），需使用者確認")

    for rid in idx.hard_zone:
        if rid not in active_ids:
            problems.append(f"{rid}: 出現在硬禁區但 B 段沒有這條")
    if len(idx.hard_zone) > HARD_ZONE_LIMIT:
        problems.append(f"提醒：硬禁區有 {len(idx.hard_zone)} 條，超過上限 {HARD_ZONE_LIMIT}，請先降舊的（不擋）")

    if detail_dir.exists():
        for p in sorted(detail_dir.glob("R-*.md")):
            if p.stem not in seen:
                problems.append(f"{p.stem}: 詳情檔存在但索引沒有這條")
    if len(idx.active) > ACTIVE_SOFT_LIMIT:
        problems.append(f"提醒：B 段有效裁決 {len(idx.active)} 行，超過 {ACTIVE_SOFT_LIMIT}，請歸檔（不擋）")
    return problems


def _resolved_inside(repo_root: Path, rel: str) -> tuple:
    """解析 repo_root/rel，回傳 (resolved 目標路徑, 是否仍在 repo 內)。"""
    target = (repo_root / rel).resolve()
    inside = target == repo_root or repo_root in target.parents
    return target, inside


def check_paths(repo_root: Path) -> list:
    """白名單路徑必須是 repo 相對路徑、不得含 ..、解析後仍在 repo 內、且檔案存在。"""
    repo_root = Path(repo_root).resolve()
    idx = parse_index(repo_root)
    problems = []
    for w in idx.whitelist:
        raw = w.path.strip()
        if not raw:
            problems.append("白名單有空路徑")
            continue
        p = Path(raw)
        if p.is_absolute() or re.match(r"^[A-Za-z]:", raw) or raw.startswith("/") or raw.startswith("\\"):
            problems.append(f"{raw}: 白名單不得使用絕對路徑")
            continue
        if ".." in p.parts:
            problems.append(f"{raw}: 白名單路徑不得含 ..")
            continue
        target, inside = _resolved_inside(repo_root, raw)
        if not inside:
            problems.append(f"{raw}: 解析後在 repo 之外（{target}）")
            continue
        if not target.exists():
            problems.append(f"{raw}: 檔案不存在")
    return problems


@dataclass
class Hit:
    ruling_id: str
    term: str
    file: str
    line_no: int
    text: str
    hint: str


def _read_lines(path: Path) -> list:
    """回傳「行」清單。pptx 一張投影片一行，docx 一個段落一行，其餘照檔案行。"""
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        out = []
        with zipfile.ZipFile(path) as z:
            slides = []
            for name in z.namelist():
                m = SLIDE_RE.match(name)
                if m:
                    slides.append((int(m.group(1)), name))
            for _, name in sorted(slides):
                xml = z.read(name).decode("utf-8", "ignore")
                out.append(unescape("".join(A_T_RE.findall(xml))))
        return out
    if suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        return [unescape("".join(W_T_RE.findall(p))) for p in W_P_RE.findall(xml)]
    return path.read_text(encoding="utf-8").splitlines()


def _hint(line: str, term: str, pos: int) -> str:
    """提示欄：只影響排序，不排除任何命中。"""
    if REF_ID_RE.search(line) and REF_WORD_RE.search(line):
        return "引用"
    window = line[max(0, pos - NEG_WINDOW): pos + len(term) + NEG_WINDOW]
    if NEGATION_RE.search(window):
        return "否定"
    return "疑似"


def _scan_targets(repo_root: Path, extra: list) -> list:
    idx = parse_index(repo_root)
    targets = [repo_root / w.path for w in idx.whitelist]
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists():
        targets.append(claude_md)
    for p in extra:
        p = Path(p)
        p = p if p.is_absolute() else repo_root / p
        if p.is_dir():
            targets.extend(q for q in p.rglob("*") if q.suffix.lower() in SCAN_EXTS)
        else:
            targets.append(p)
    index_abs = (repo_root / INDEX_REL).resolve()
    detail_abs = (repo_root / DETAIL_DIR_REL).resolve()
    out, seen = [], set()
    for t in targets:
        if not t.exists() or t.suffix.lower() not in SCAN_EXTS:
            continue
        rt = t.resolve()
        if rt == index_abs or detail_abs in rt.parents:
            continue
        if rt in seen:
            continue
        seen.add(rt)
        out.append(t)
    return out


def _display_path(f: Path, repo_root: Path) -> str:
    """相對 repo_root 顯示；一律用正斜線（as_posix），與帳本白名單的寫法一致，
    Windows 上也不印反斜線。若 f 不在 repo_root 之內（例如 --scan 指到 repo 外的
    絕對路徑）relative_to 會丟 ValueError，退回用絕對路徑（同樣轉正斜線）。"""
    try:
        return f.relative_to(repo_root).as_posix()
    except ValueError:
        return f.as_posix()


def resurrect_scan(repo_root: Path, extra_targets: list) -> list:
    repo_root = Path(repo_root)
    idx = parse_index(repo_root)
    terms = []
    for row in idx.active:
        if row.status != "打掉":
            continue
        dpath = repo_root / DETAIL_DIR_REL / f"{row.id}.md"
        synonyms = parse_detail(dpath).synonyms if dpath.exists() else []
        for t in dict.fromkeys(list(row.keywords) + list(synonyms)):
            if t:
                terms.append((row.id, t))
    hits = []
    for f in _scan_targets(repo_root, extra_targets):
        file_display = _display_path(f, repo_root)
        for line_no, line in enumerate(_read_lines(f), 1):
            for rid, term in terms:
                start = 0
                while True:
                    pos = line.find(term, start)
                    if pos < 0:
                        break
                    hits.append(Hit(rid, term, file_display,
                                    line_no, line.strip(), _hint(line, term, pos)))
                    start = pos + len(term)
    return hits


def main(argv: list = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="裁決帳本檢查")
    ap.add_argument("repo_root")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--paths", action="store_true")
    ap.add_argument("--resurrect", action="store_true")
    ap.add_argument("--scan", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    run_all = not (args.lint or args.paths or args.resurrect)
    result = {}
    hits = []
    if args.lint or run_all:
        result["lint"] = lint(root)
    if args.paths or run_all:
        result["paths"] = check_paths(root)
    if args.resurrect or run_all:
        hits = resurrect_scan(root, [Path(s) for s in args.scan])
        result["hits"] = [asdict(h) for h in hits]
    lint_hard = [p for p in result.get("lint", []) if not p.startswith("提醒")]
    hard = lint_hard + result.get("paths", [])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "lint" in result:
            print("lint:", "通過" if not lint_hard else "有問題")
            for p in result["lint"]:
                print("  -", p)
        if "paths" in result:
            print("paths:", "通過" if not result["paths"] else "有問題")
            for p in result["paths"]:
                print("  -", p)
        if "hits" in result:
            print(f"resurrect: {len(hits)} 筆命中（提示只排序，全部都要判定）")
            order = {"疑似": 0, "引用": 1, "否定": 2}
            for h in sorted(hits, key=lambda x: (order.get(x.hint, 3), x.file, x.line_no)):
                print(f"  - {h.ruling_id} | {h.hint} | {h.file}:{h.line_no} | {h.term} | {h.text}")
    if hard:
        return 1
    return 2 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
