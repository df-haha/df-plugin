"""refine status / draft / apply --file F / skip"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from daily_agenda import analyze, criteria, state, validate
from daily_agenda.fence import fence_untrusted
from daily_agenda.fsutil import jsonl_line
from daily_agenda.ledger import feedback_signals
from ._common import Deps, emit, require_consent, validate_file_flag


def _write_refine_bundle(
    path: Path,
    *,
    criteria_text: str,
    criteria_version: int,
    signals: list[dict],
    profile_text: str | None = None,
) -> None:
    """Write a feedback.jsonl bundle for the refine analyzer."""
    lines: list[str] = []

    # criteria line (trusted)
    lines.append(jsonl_line({
        "kind": "criteria",
        "version": criteria_version,
        "text": criteria_text,
    }))

    # profile (untrusted — may contain inferred third-party names)
    if profile_text:
        lines.append(jsonl_line({
            "kind": "profile",
            "untrusted": {"text": profile_text},
        }))

    # feedback lines — untrusted text nested under "untrusted"
    for sig in signals:
        lines.append(jsonl_line({
            "kind": "feedback",
            "todo_id": sig["todo_id"],
            "signal": sig["kind"],
            "at": sig["at"],
            "priority": sig["priority"],
            "source_kind": sig["source_kind"],
            "carry_days": sig["carry_days"],
            "untrusted": sig["untrusted"],
        }))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        # Consent gate
        rc = require_consent(cfg)
        if rc is not None:
            return rc

        # Noninteractive mode: scheduled runs must not trigger refine
        if deps.noninteractive:
            return emit({"ok": False, "code": "REFINE_NONINTERACTIVE"})

        # Require existing criteria
        base_version = criteria.latest_version(h)
        if base_version == 0:
            return emit({"ok": False, "code": "CONFIG_MISSING",
                          "detail": "no criteria versions found"})

        criteria_text = criteria.read_latest(h) or ""

        # Collect feedback signals from ledger — use last_refine_applied_at
        # (set only by apply, not skip) so a skip doesn't permanently exclude
        # pre-skip feedback from future drafts.
        last_applied = st.get("last_refine_applied_at")
        ldg = state.load_ledger(h)
        signals = feedback_signals(ldg, since=last_applied, now=now)

        if not signals:
            return emit({"ok": False, "code": "NO_FEEDBACK"})

        # Prepare run directory
        run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
        runs_dir = h / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        # Load profile for refine bundle
        from daily_agenda import profile as _profile
        profile_text = _profile.read_latest(h)

        # Write feedback bundle
        bundle_path = runs_dir / "feedback.jsonl"
        _write_refine_bundle(
            bundle_path,
            criteria_text=criteria_text,
            criteria_version=base_version,
            signals=signals,
            profile_text=profile_text,
        )

        # Run analyzer
        progress = deps.progress
        try:
            prompt = analyze.load_prompt("refine")
            bundle_bytes = bundle_path.read_bytes()
            progress.event("analyze_batch", i=1, n=1)
            with progress.heartbeat():
                analyzer_output = analyze.run_analyzer(
                    bundle_bytes, prompt,
                    model=cfg.get("analysis", {}).get("model"),
                    timeout_s=cfg.get("analysis", {}).get("timeout_s", 600),
                )
            progress.event("analyze_batch_done", i=1, n=1)
            draft_text, changes = validate.validate_refine_draft(
                analyzer_output.output,
            )
        except analyze.AnalyzerError as exc:
            return emit({"ok": False, "code": exc.code})
        except validate.ModelOutputInvalid:
            return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

        # Write outputs (no state commit — only apply/skip do that)
        draft_path = runs_dir / "criteria-draft.md"
        draft_path.write_text(draft_text, encoding="utf-8")

        changes_path = runs_dir / "changes.json"
        changes_path.write_text(
            json.dumps(changes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # F4d: fence change summaries/reasons (model-derived from third-party)
        fenced_changes = []
        for ch in changes:
            fenced_changes.append({
                "summary": fence_untrusted(ch.get("summary", "")) if ch.get("summary") else "",
                "reason": fence_untrusted(ch.get("reason", "")) if ch.get("reason") else "",
            })

        return emit({
            "ok": True, "code": None,
            "draft_path": str(draft_path),
            "draft_text": fence_untrusted(draft_text),
            "base_version": base_version,
            "feedback_count": len(signals),
            "changes": fenced_changes,
        })

    elif sub == "apply":
        file_path, err = validate_file_flag(argv, h)
        if err is not None:
            return err
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
        st["last_refine_applied_at"] = now.isoformat()
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
