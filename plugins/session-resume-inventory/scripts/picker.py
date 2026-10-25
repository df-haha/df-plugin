#!/usr/bin/env python3
"""Let the user tick which sessions to keep, via a local HTML page.

build: snapshot (+ optional notes JSON written by the main loop) -> checklist HTML.
apply: the text the user pasted back -> filtered snapshot.
  save mode    rewrites the snapshot in place (full copy moved to full/), so restore.py's
               "latest *.json" lookup only ever sees the user's selection.
  restore mode writes picked/<name> and leaves the snapshot untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
SNAPSHOT_DIR = HOME / ".claude" / "session-snapshots"
TEMPLATE = Path(__file__).with_name("picker_template.html")
PREFIX_LEN = 8
DESC_LEN = 120
STATES = {"wait", "going", "done", "unknown"}
MODES = {
    "save": {"title": "存 session 選擇結果", "heading": "關機前要存哪些 session", "on": "要存", "off": "不存",
             "verb": "會存", "lead": "勾選要記進快照、開機後要開回的 session。"},
    "restore": {"title": "復原 session 選擇結果", "heading": "開機後要開回哪些 session", "on": "要開回",
                "off": "不開", "verb": "會開回", "lead": "勾選要開回 Orca 的 session。"},
}
LINE_SID_RE = re.compile(r"（([0-9a-f]{8})）\s*$", re.M)
BARE_SID_RE = re.compile(r"\b([0-9a-f]{8})\b")


def project_of(cwd: str) -> str:
    return Path(cwd).name or cwd


def short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= DESC_LEN else text[:DESC_LEN] + "…"


def build_rows(sessions: list[dict], notes: dict[str, dict]) -> list[dict]:
    """One row per resumable session, grouped by project in first-seen order."""
    rows: list[dict] = []
    for s in sessions:
        if not (s.get("resumable") and s.get("sid")):
            continue
        sid = s["sid"][:PREFIX_LEN]
        note = notes.get(sid) or notes.get(s["sid"]) or {}
        st = note.get("st") or ("going" if s.get("status") in ("busy", "shell") else "unknown")
        rows.append({
            "sid": sid,
            "name": s.get("name") or f"{s['runtime']} {sid}",
            "proj": note.get("proj") or project_of(s.get("cwd", "")),
            "st": st if st in STATES else "unknown",
            "desc": short(note.get("desc") or s.get("hint") or "（沒有可用的摘要）"),
        })
    order = list(dict.fromkeys(r["proj"] for r in rows))
    return sorted(rows, key=lambda r: order.index(r["proj"]))


def js_json(value: object) -> str:
    # Any "<" can end or confuse the inline <script> ("</script>", "<!--<script>"); \u003c parses back the same.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def render_html(rows: list[dict], mode: str, saved_at: str, snap_name: str = "") -> str:
    m = MODES[mode]
    try:
        when = datetime.fromisoformat(saved_at).strftime("%m/%d %H:%M")
    except ValueError:
        when = saved_at
    lead = (f"快照存於 {when}，共 {len(rows)} 個。{m['lead']}按下方「複製選擇結果」，再貼回給 Claude。"
            "已收尾的預設不勾。")
    meta = {"title": m["title"], "on": m["on"], "off": m["off"], "snap": snap_name or when}
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, val in (("%TITLE%", m["heading"]), ("%HEADING%", m["heading"]), ("%LEAD%", lead),
                     ("%VERB_ON%", m["verb"]), ("%MODE%", js_json(meta)), ("%DATA%", js_json(rows))):
        html = html.replace(key, val)
    return html


def parse_picked(text: str) -> list[str]:
    """Session-ID prefixes from the 'picked' section; a bare list of prefixes also works."""
    picked, exported = text, False
    for m in MODES.values():
        head = re.search(rf"^{m['on']}\s*\d+\s*個", text, re.M)
        if head:
            tail = re.search(rf"^{m['off']}\s*\d+\s*個", text[head.end():], re.M)
            picked = text[head.end(): head.end() + tail.start()] if tail else text[head.end():]
            exported = True
            break
    # Exported rows end with （<prefix>）; only that slot counts, so hex-looking names are ignored.
    found = LINE_SID_RE.findall(picked) if exported else BARE_SID_RE.findall(picked)
    return list(dict.fromkeys(found))


def write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def apply_pick(snap_path: Path, prefixes: list[str], mode: str) -> Path:
    if not prefixes:
        sys.exit("沒有勾選任何 session（或貼回的不是頁面匯出的文字），快照維持原樣。")
    backup = snap_path.parent / "full" / snap_path.name
    # Re-picking after a save must start from the full list, not the already-filtered file.
    source = backup if mode == "save" and backup.exists() else snap_path
    snap = json.loads(source.read_text(encoding="utf-8"))
    sessions = snap.get("sessions", [])
    chosen: list[dict] = []
    for p in prefixes:
        hits = [s for s in sessions if (s.get("sid") or "").startswith(p)]
        if len(hits) != 1:
            sys.exit(f"{p}：在快照裡對到 {len(hits)} 筆，無法判斷，請重新匯出。")
        chosen.append(hits[0])
    keep = {id(s) for s in chosen}
    out = dict(snap, sessions=[s for s in sessions if id(s) in keep], picked_from=snap_path.name)
    if mode == "save":
        if not backup.exists():
            backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(snap_path, backup)
            backup.chmod(0o600)
        write_private(snap_path, out)
        return snap_path
    target = snap_path.parent / "picked" / snap_path.name
    write_private(target, out)
    return target


def latest_snapshot() -> Path:
    candidates = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not candidates:
        sys.exit(f"找不到快照：{SNAPSHOT_DIR} 是空的。先跑 inventory.py --save。")
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="用 HTML 勾選頁挑要存／要開回的 session")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="產生勾選頁")
    b.add_argument("--mode", choices=MODES, required=True)
    b.add_argument("--snapshot", type=Path, help="快照路徑（預設取最新一份）")
    b.add_argument("--notes", type=Path, help="main loop 寫的摘要 JSON：{sid前8碼: {st, desc, proj}}")
    b.add_argument("--out", type=Path, help="HTML 輸出路徑（預設 <快照資料夾>/picker/<快照名>.html）")
    a = sub.add_parser("apply", help="依使用者貼回的結果篩選快照")
    a.add_argument("--mode", choices=MODES, required=True)
    a.add_argument("--snapshot", type=Path, help="快照路徑（預設取最新一份）")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-text", type=Path, help="使用者貼回的匯出文字檔；- 代表 stdin")
    src.add_argument("--sids", help="逗號分隔的 session ID 前 8 碼")
    opts = parser.parse_args()

    snap_path = opts.snapshot or latest_snapshot()
    if opts.cmd == "build":
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        notes = json.loads(opts.notes.read_text(encoding="utf-8")) if opts.notes else {}
        rows = build_rows(snap.get("sessions", []), notes)
        out = opts.out or snap_path.parent / "picker" / f"{snap_path.stem}-{opts.mode}.html"
        out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        out.write_text(render_html(rows, opts.mode, snap.get("saved_at", ""), snap_path.stem), encoding="utf-8")
        print(f"勾選頁：{out}（{len(rows)} 個 session，快照 {snap_path}）")
        return
    text = sys.stdin.read() if str(opts.from_text) == "-" else (
        opts.from_text.read_text(encoding="utf-8") if opts.from_text else opts.sids)
    out = apply_pick(snap_path, parse_picked(text), opts.mode)
    kept = len(json.loads(out.read_text(encoding="utf-8"))["sessions"])
    if opts.mode == "save":
        print(f"已篩選快照：{out}（保留 {kept} 個；完整版備份在 {snap_path.parent / 'full' / snap_path.name}）")
    else:
        print(f"已產生復原用快照：{out}（{kept} 個）。接著跑 restore.py --snapshot {out}")


if __name__ == "__main__":
    main()
