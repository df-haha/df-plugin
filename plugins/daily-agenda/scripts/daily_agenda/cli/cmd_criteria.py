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
        from daily_agenda.line_source import LineSourceError
        from daily_agenda.state import _initial_state

        now = deps.now_factory()
        run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
        runs_dir = deps.home / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        progress = deps.progress

        # Build LINE source — map factory errors to their code (#2)
        line_src = None
        line_enabled = cfg.get("line", {}).get("enabled", False)
        if deps.line_source_factory and line_enabled:
            try:
                line_src = deps.line_source_factory(
                    main_database=cfg["line"].get("main_database", ""),
                    trusted_builds_path=str(deps.home / "trusted_builds.json"),
                )
            except LineSourceError as exc:
                return emit({"ok": False, "code": exc.code})
            except Exception:
                return emit({"ok": False, "code": "LINE_RUNTIME_MISSING"})

        # --- Fail-fast LINE check (D) ---
        # Before collecting mail or calling the analyzer, verify LINE is
        # reachable.  criteria draft is a one-shot scan with no degradation
        # path, so an unreachable LINE is a hard failure.
        if line_src is not None:
            progress.event("line_check")
            try:
                line_src.recent_chats(days=14, offset=0, limit=1)
            except LineSourceError as exc:
                return emit({"ok": False, "code": exc.code})
            except Exception:
                return emit({"ok": False, "code": "LOCAL_READER_FAILED"})

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
                run_id=run_id, initial_days=days, progress=progress,
            )
        except collect.CollectError as exc:
            return emit({"ok": False, "code": exc.code})

        # --- Check LINE collection result (#2) ---
        # criteria draft has no degradation path: if LINE is enabled and
        # failed during collection, fail the whole draft.
        if line_enabled and line_src is not None:
            line_status = result.source_status.get("line", {})
            if line_status.get("status") == "failed":
                code = line_status.get("code") or "LOCAL_READER_FAILED"
                return emit({"ok": False, "code": code})

        # --- Batched analysis ---
        batch_bytes_cfg = cfg.get("analysis", {}).get("batch_bytes", 300_000)
        batches = bundle.split_batches(result.records, batch_bytes_cfg)
        model_cfg = cfg.get("analysis", {}).get("model")
        timeout_s = cfg.get("analysis", {}).get("timeout_s", 600)

        if len(batches) <= 1:
            # Single batch (or empty) — use original criteria prompt directly
            bundle_path = runs_dir / "bundle.jsonl"
            bundle.write_bundle(bundle_path, result=result, criteria=None, open_todos=[], ctx=[])

            try:
                prompt = analyze.load_prompt("criteria")
                progress.event("analyze_batch", i=1, n=1)
                with progress.heartbeat():
                    analyzer_output = analyze.run_analyzer(
                        bundle_path.read_bytes(), prompt,
                        model=model_cfg, timeout_s=timeout_s,
                    )
                progress.event("analyze_batch_done", i=1, n=1)
                criteria_text = validate.validate_criteria_draft(analyzer_output.output)
            except analyze.AnalyzerError as exc:
                return emit({"ok": False, "code": exc.code})
            except validate.ModelOutputInvalid:
                return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

            return _emit_draft(runs_dir, criteria_text, days, batches_skipped=0)
        else:
            # Multiple batches — map/reduce
            return _draft_map_reduce(
                batches, result, runs_dir, progress, model_cfg, timeout_s,
                days, analyze, bundle, validate,
            )

    else:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: criteria draft|show|save|list|restore"}, exit_code=2)


# ---------------------------------------------------------------------------
# Map-reduce helpers
# ---------------------------------------------------------------------------

# Maximum total observations fed to the reduce step (global cap, #6)
_MAX_TOTAL_OBSERVATIONS = 400


class _BatchResult:
    """Lightweight adapter so ``bundle.write_bundle`` sees a CollectResult-like object."""

    def __init__(self, records: list, base: object) -> None:
        self.run_id = base.run_id  # type: ignore[attr-defined]
        self.generated_at = base.generated_at  # type: ignore[attr-defined]
        self.tz = base.tz  # type: ignore[attr-defined]
        self.records = records
        self.source_status = base.source_status  # type: ignore[attr-defined]
        self.new_chats = base.new_chats  # type: ignore[attr-defined]
        self.criteria_version = base.criteria_version  # type: ignore[attr-defined]


def _make_batch_result(records: list, base: object) -> _BatchResult:
    return _BatchResult(records, base)


def _emit_draft(runs_dir: Path, criteria_text: str, days: int, *, batches_skipped: int = 0) -> int:
    draft_path = runs_dir / "criteria-draft.md"
    draft_path.write_text(criteria_text, encoding="utf-8")
    result: dict = {"ok": True, "code": None, "draft_path": str(draft_path), "days": days}
    if batches_skipped > 0:
        result["batches_skipped"] = batches_skipped
    return emit(result)


def _dedupe_observations(observations: list[dict]) -> list[dict]:
    """Deduplicate by (category, pattern) and cap at _MAX_TOTAL_OBSERVATIONS."""
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for obs in observations:
        key = (obs.get("category", ""), obs.get("pattern", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(obs)
        if len(result) >= _MAX_TOTAL_OBSERVATIONS:
            break
    return result


def _draft_map_reduce(
    batches: list[list],
    result: object,
    runs_dir: Path,
    progress: object,
    model_cfg: str | None,
    timeout_s: int,
    days: int,
    analyze: object,
    bundle: object,
    validate: object,
) -> int:
    """Map each batch → observations, reduce → final criteria.

    Policy (#7): skip-and-count malformed map batches instead of failing
    everything.  If more than half of batches fail → MODEL_OUTPUT_INVALID.
    """
    from daily_agenda.fsutil import jsonl_line

    observations: list[dict] = []
    n_batches = len(batches)
    batches_skipped = 0

    for idx, batch_records in enumerate(batches, 1):
        bp = runs_dir / f"bundle-{idx}.jsonl"
        batch_result = _make_batch_result(batch_records, result)
        bundle.write_bundle(  # type: ignore[attr-defined]
            bp, result=batch_result, criteria=None, open_todos=[], ctx=[],
            batch_index=idx, batch_total=n_batches,
        )

        try:
            prompt = analyze.load_prompt("criteria_observe")  # type: ignore[attr-defined]
            progress.event("analyze_batch", i=idx, n=n_batches)  # type: ignore[attr-defined]
            with progress.heartbeat():  # type: ignore[attr-defined]
                analyzer_output = analyze.run_analyzer(  # type: ignore[attr-defined]
                    bp.read_bytes(), prompt,
                    model=model_cfg, timeout_s=timeout_s,
                )
            progress.event("analyze_batch_done", i=idx, n=n_batches)  # type: ignore[attr-defined]
            batch_obs = validate.validate_observations(analyzer_output.output)  # type: ignore[attr-defined]
            observations.extend(batch_obs)
        except validate.ModelOutputInvalid:  # type: ignore[attr-defined]
            # Skip malformed batch (#7)
            batches_skipped += 1
        except analyze.AnalyzerError as exc:  # type: ignore[attr-defined]
            return emit({"ok": False, "code": exc.code})

    # If more than half of batches failed → fail entirely (#7)
    if batches_skipped > n_batches // 2:
        return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

    # Deduplicate and cap observations (#6)
    observations = _dedupe_observations(observations)

    # Reduce: seed criteria + all observations → final criteria
    reduce_lines: list[str] = []
    seed_path = analyze.TEMPLATES_DIR / "criteria-seed.md"  # type: ignore[attr-defined]
    if seed_path.exists():
        seed_text = seed_path.read_text(encoding="utf-8")
        reduce_lines.append(jsonl_line({
            "kind": "criteria",
            "version": 0,
            "text": seed_text,
        }))

    for obs in observations:
        reduce_lines.append(jsonl_line({
            "kind": "observation",
            "untrusted": obs,
        }))

    reduce_bundle = "\n".join(reduce_lines) + "\n"

    try:
        reduce_prompt = analyze.load_prompt("criteria_reduce")  # type: ignore[attr-defined]
        progress.event("analyze_reduce", i=1, n=1)  # type: ignore[attr-defined]
        with progress.heartbeat():  # type: ignore[attr-defined]
            reduce_output = analyze.run_analyzer(  # type: ignore[attr-defined]
                reduce_bundle.encode("utf-8"), reduce_prompt,
                model=model_cfg, timeout_s=timeout_s,
            )
        progress.event("analyze_reduce_done", i=1, n=1)  # type: ignore[attr-defined]
        criteria_text = validate.validate_criteria_draft(reduce_output.output)  # type: ignore[attr-defined]
    except analyze.AnalyzerError as exc:  # type: ignore[attr-defined]
        return emit({"ok": False, "code": exc.code})
    except validate.ModelOutputInvalid:  # type: ignore[attr-defined]
        return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

    return _emit_draft(runs_dir, criteria_text, days, batches_skipped=batches_skipped)
