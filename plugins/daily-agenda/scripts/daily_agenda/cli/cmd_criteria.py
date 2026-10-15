"""criteria draft/show/save/list/restore commands."""

from __future__ import annotations

import secrets
from pathlib import Path

from daily_agenda import criteria, state
from daily_agenda.messages import humanize
from ._common import Deps, emit, require_consent


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: criteria draft|show|save|list|restore"}, exit_code=2)

    sub = argv[0]

    if sub == "list":
        versions = criteria.list_versions(deps.home)
        return emit({"ok": True, "code": None, "versions": versions,
                      "latest": versions[-1] if versions else None})

    elif sub == "show":
        cfg = state.load_config(deps.home)
        rc = require_consent(cfg)
        if rc is not None:
            return rc

        version: int | None = None
        for i, arg in enumerate(argv):
            if arg == "--version" and i + 1 < len(argv):
                try:
                    version = int(argv[i + 1])
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR", "detail": "--version must be integer"}, exit_code=2)

        if version is None:
            version = criteria.latest_version(deps.home)
        if version == 0:
            return emit({"ok": False, "code": "CONFIG_MISSING", "detail": "no criteria versions found"})

        text = criteria.read_version(deps.home, version)
        if text is None:
            return emit({"ok": False, "code": "CONFIG_MISSING", "detail": f"version {version} not found"})

        # ★ command — shows content
        return emit({"ok": True, "code": None, "version": version, "text": text})

    elif sub == "save":
        file_path: str | None = None
        for i, arg in enumerate(argv):
            if arg == "--file" and i + 1 < len(argv):
                file_path = argv[i + 1]

        if not file_path:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: criteria save --file F"}, exit_code=2)

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": f"file not found: {file_path}"}, exit_code=2)

        v = criteria.save(deps.home, text)
        return emit({"ok": True, "code": None, "version": v})

    elif sub == "restore":
        version = None
        for i, arg in enumerate(argv):
            if arg == "--version" and i + 1 < len(argv):
                try:
                    version = int(argv[i + 1])
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR", "detail": "--version must be integer"}, exit_code=2)

        if version is None:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: criteria restore --version N"}, exit_code=2)

        try:
            new_v = criteria.restore(deps.home, version)
        except FileNotFoundError:
            return emit({"ok": False, "code": "CONFIG_MISSING", "detail": f"version {version} not found"})

        return emit({"ok": True, "code": None, "version": new_v, "restored_from": version})

    elif sub == "draft":
        cfg = state.load_config(deps.home)
        rc = require_consent(cfg)
        if rc is not None:
            return rc

        days = 14
        for i, arg in enumerate(argv):
            if arg == "--days" and i + 1 < len(argv):
                try:
                    days = int(argv[i + 1])
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR", "detail": "--days must be integer"}, exit_code=2)

        from daily_agenda import analyze, bundle, collect, validate
        from daily_agenda.state import _initial_state

        now = deps.now_factory()
        run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
        runs_dir = deps.home / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        # Build sources for initial collection
        line_src = None
        if deps.line_source_factory and cfg.get("line", {}).get("enabled"):
            try:
                line_src = deps.line_source_factory(
                    main_database=cfg["line"].get("main_database", ""),
                    trusted_builds_path=str(deps.home / "trusted_builds.json"),
                )
            except Exception:
                pass

        graph_src = None
        if deps.graph_factory:
            try:
                graph_src = deps.graph_factory()
            except Exception:
                pass

        # Collect with initial_days=days, fresh state (no checkpoints)
        try:
            result = collect.collect(
                cfg, _initial_state(), now=now, line=line_src, graph=graph_src,
                run_id=run_id, initial_days=days,
            )
        except collect.CollectError as exc:
            return emit({"ok": False, "code": exc.code})

        # Write bundle (no criteria, no open todos)
        bundle_path = runs_dir / "bundle.jsonl"
        bundle.write_bundle(bundle_path, result=result, criteria=None, open_todos=[], ctx=[])

        # Analyze with criteria prompt
        try:
            prompt = analyze.load_prompt("criteria")
            bundle_bytes = bundle_path.read_bytes()
            analyzer_output = analyze.run_analyzer(
                bundle_bytes, prompt,
                model=cfg.get("analysis", {}).get("model"),
                timeout_s=cfg.get("analysis", {}).get("timeout_s", 600),
            )
            criteria_text = validate.validate_criteria_draft(analyzer_output.output)
        except analyze.AnalyzerError as exc:
            return emit({"ok": False, "code": exc.code})
        except validate.ModelOutputInvalid:
            return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

        draft_path = runs_dir / "criteria-draft.md"
        draft_path.write_text(criteria_text, encoding="utf-8")
        return emit({"ok": True, "code": None, "draft_path": str(draft_path), "days": days})

    else:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: criteria draft|show|save|list|restore"}, exit_code=2)
