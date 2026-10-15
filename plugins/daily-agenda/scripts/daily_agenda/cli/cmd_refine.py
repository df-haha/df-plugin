"""refine status / draft / apply --file F / skip"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from daily_agenda import criteria, state
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: refine status|draft|apply|skip"}, exit_code=2)

    sub = argv[0]
    h = deps.home
    cfg = state.load_config(h)
    st = state.load_state(h)
    now = deps.now_factory()

    refine_cfg = cfg.get("refine", {})
    every_runs = refine_cfg.get("every_runs", 10)
    every_days = refine_cfg.get("every_days", 14)
    runs_since = st.get("runs_since_refine", 0)
    last_refine = st.get("last_refine_at")

    days_since: int | None = None
    if last_refine:
        try:
            last_dt = datetime.fromisoformat(last_refine)
            days_since = (now - last_dt).days
        except (ValueError, TypeError):
            days_since = None

    due = runs_since >= every_runs or (days_since is not None and days_since >= every_days)

    if sub == "status":
        reason = ""
        if runs_since >= every_runs:
            reason = f"已執行 {runs_since} 次"
        elif days_since is not None and days_since >= every_days:
            reason = f"距上次修正已 {days_since} 天"
        return emit({"ok": True, "code": None, "due": due, "reason": reason,
                      "runs_since_refine": runs_since, "last_refine_at": last_refine})

    elif sub == "draft":
        # Minimal: write current criteria as draft for editing
        import secrets
        run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
        runs_dir = h / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        draft_path = runs_dir / "criteria-draft.md"

        current = criteria.read_latest(h)
        if current:
            draft_path.write_text(current, encoding="utf-8")
        else:
            draft_path.write_text("# 判斷準則\n\n（尚無準則）\n", encoding="utf-8")

        return emit({"ok": True, "code": None, "draft_path": str(draft_path), "due": due})

    elif sub == "apply":
        file_path: str | None = None
        for i, arg in enumerate(argv):
            if arg == "--file" and i + 1 < len(argv):
                file_path = argv[i + 1]

        if not file_path:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: refine apply --file F"}, exit_code=2)

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": f"file not found: {file_path}"}, exit_code=2)

        v = criteria.save(h, text)

        # Reset refine counters in state
        st["runs_since_refine"] = 0
        st["last_refine_at"] = now.isoformat()
        st["criteria_version"] = v

        # Commit updated state
        ldg = state.load_ledger(h)
        cur_gen = state.current_generation(h)
        outputs: dict[str, str] = {}
        publish_targets: list[dict] = []
        if cur_gen is not None:
            cur_dir = h / "gen" / str(cur_gen)
            for fmt in ("html", "md"):
                out_file = cur_dir / f"agenda.{fmt}"
                if out_file.exists():
                    outputs[fmt] = out_file.read_text(encoding="utf-8")
            from daily_agenda.fsutil import read_json
            pub = read_json(cur_dir / "publish.json")
            if pub:
                publish_targets = pub.get("targets", [])

        staging = state.stage_generation(
            h, state=st, ledger=ldg, outputs=outputs, publish_targets=publish_targets,
        )
        state.commit_generation(h, staging)
        return emit({"ok": True, "code": None, "version": v})

    elif sub == "skip":
        # Reset runs counter without changing criteria
        st["runs_since_refine"] = 0
        st["last_refine_at"] = now.isoformat()

        ldg = state.load_ledger(h)
        cur_gen = state.current_generation(h)
        outputs: dict[str, str] = {}
        publish_targets: list[dict] = []
        if cur_gen is not None:
            cur_dir = h / "gen" / str(cur_gen)
            for fmt in ("html", "md"):
                out_file = cur_dir / f"agenda.{fmt}"
                if out_file.exists():
                    outputs[fmt] = out_file.read_text(encoding="utf-8")
            from daily_agenda.fsutil import read_json
            pub = read_json(cur_dir / "publish.json")
            if pub:
                publish_targets = pub.get("targets", [])

        staging = state.stage_generation(
            h, state=st, ledger=ldg, outputs=outputs, publish_targets=publish_targets,
        )
        state.commit_generation(h, staging)
        return emit({"ok": True, "code": None, "skipped": True})

    else:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: refine status|draft|apply|skip"}, exit_code=2)
