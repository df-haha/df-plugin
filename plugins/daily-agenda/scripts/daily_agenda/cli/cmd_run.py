"""run [--noninteractive] — the /agenda pipeline.

Pipeline: publish-backlog check → lock → collect → analyze → validate →
ledger → render → commit generation → publish.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from daily_agenda import (
    analyze, bundle, collect, criteria, history, ledger, render, runtime, state, validate,
)
from daily_agenda.line_source import LineSourceError
from ._common import Deps, emit, require_consent

# First run has no checkpoints; read this many days back (same as the
# setup scan default) instead of collect()'s 14, which doubled first-day volume.
_INITIAL_DAYS = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_src_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build ``{src_id: {"label": str, "href": None}}`` for source annotations."""
    index: dict[str, dict[str, Any]] = {}
    for rec in records:
        sid = rec.get("src_id", "")
        if not sid:
            continue  # skip records without src_id (e.g. chat_history)
        kind = rec.get("kind", "")
        ut = rec.get("untrusted", {})

        if kind == "line_message":
            label = f"LINE · {ut.get('chat_name', '')}"
        elif kind == "mail":
            label = f"Mail · {ut.get('from', '')}"
        elif kind == "event":
            label = f"行事曆 · {ut.get('subject', '')}"
        elif kind == "ctx":
            label = f"Ctx · {rec.get('path', '')}"
        else:
            label = sid

        index[sid] = {"label": label, "href": None}
    return index


def _load_ctx(h: Path) -> list[dict[str, Any]]:
    """Load ctx-index.jsonl."""
    path = h / "ctx-index.jsonl"
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def _analyze_batch_with_retry(
    bp: Path,
    prompt: str,
    cfg: dict[str, Any],
    progress: Any,
) -> analyze.AnalyzerResult:
    """Run analysis on one batch.  Retry once on MODEL_OUTPUT_INVALID (C1).

    Also checks that ``items`` is a list in the result — if not, treats it
    as MODEL_OUTPUT_INVALID so the retry covers it.
    """
    model_name = state.analysis_model(cfg)
    timeout_s = cfg.get("analysis", {}).get("timeout_s", 600)
    stdin_bytes = bp.read_bytes()

    def _run_once() -> analyze.AnalyzerResult:
        with progress.heartbeat():
            ar = analyze.run_analyzer(
                stdin_bytes, prompt, model=model_name, timeout_s=timeout_s,
            )
        # items must be a list — if not, this is a structural failure
        if not isinstance(ar.output.get("items"), list):
            raise analyze.AnalyzerError("MODEL_OUTPUT_INVALID")
        return ar

    try:
        return _run_once()
    except analyze.AnalyzerError as exc:
        if exc.code != "MODEL_OUTPUT_INVALID":
            raise
        # Single retry on parse failure
        return _run_once()


def _normalise_bullets(raw: Any) -> list[str]:
    """Normalise bullets: non-list → [], non-str entries dropped."""
    if not isinstance(raw, list):
        return []
    return [b for b in raw if isinstance(b, str)]


def _normalise_needs_reply(raw: Any) -> list[dict[str, Any]]:
    """Normalise needs_reply: non-list → [], non-dict entries and entries
    whose src_id is not a non-empty string dropped (they would otherwise
    poison the set-based union and empty every digest)."""
    if not isinstance(raw, list):
        return []
    return [nr for nr in raw if isinstance(nr, dict) and isinstance(nr.get("src_id"), str) and nr.get("src_id")]


def _sweep_excluded_history(h: Path, cfg: dict[str, Any]) -> None:
    """C4 retry: `exclude` may have failed to delete history rows (reported as
    history_cleanup_failed); deleting again here is idempotent and cheap."""
    for c in cfg.get("line", {}).get("excluded_chats", []):
        try:
            history.delete_by_chat_ref(h, c.get("ref", ""))
        except Exception:
            pass  # still non-fatal for the run; reported path is `exclude`


def _merge_chat_digests(
    batches: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge chat_digests across batches per C2.

    Same chat_ref in multiple batches: bullets concatenated then truncated to 5,
    needs_reply union by src_id, quiet = all True.

    Normalises each entry leniently before merging; wraps so any unexpected
    exception degrades to empty digests instead of crashing.
    """
    try:
        return _merge_chat_digests_inner(batches)
    except Exception:
        return []


def _merge_chat_digests_inner(
    batches: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        if not isinstance(batch, list):
            continue
        for d in batch:
            if not isinstance(d, dict):
                continue
            cref = d.get("chat_ref", "")
            if not isinstance(cref, str) or not cref:
                continue
            bullets = _normalise_bullets(d.get("bullets"))
            nr = _normalise_needs_reply(d.get("needs_reply"))
            if cref not in merged:
                merged[cref] = {
                    "chat_ref": cref,
                    "bullets": list(bullets),
                    "needs_reply": list(nr),
                    "quiet": bool(d.get("quiet", False)),
                }
            else:
                existing = merged[cref]
                existing["bullets"].extend(bullets)
                # needs_reply: union by src_id
                seen_sids = {e.get("src_id") for e in existing["needs_reply"]}
                for entry in nr:
                    if entry.get("src_id") not in seen_sids:
                        existing["needs_reply"].append(entry)
                        seen_sids.add(entry.get("src_id"))
                # quiet = all True
                existing["quiet"] = existing["quiet"] and bool(d.get("quiet", False))

    result: list[dict[str, Any]] = []
    for d in merged.values():
        d["bullets"] = d["bullets"][:5]
        result.append(d)
    return result


def _merge_mail_digests(
    batches: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge mail_digests across batches per C2.  Dedup by src_id, last wins."""
    try:
        merged: dict[str, dict[str, Any]] = {}
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for d in batch:
                if not isinstance(d, dict):
                    continue
                sid = d.get("src_id", "")
                if isinstance(sid, str) and sid:
                    merged[sid] = dict(d)
        return list(merged.values())
    except Exception:
        return []


class _UntrustedLine:
    """Dummy LINE source for noninteractive mode when trust is unverified."""

    def probe(self) -> dict[str, Any]:
        return {"ok": False, "code": "LINE_BUILD_UNVERIFIED", "ms": 0}

    def recent_chats(self, *, days: int, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        raise LineSourceError("LINE_BUILD_UNVERIFIED")

    def messages(self, **kwargs: Any) -> dict[str, Any]:
        raise LineSourceError("LINE_BUILD_UNVERIFIED")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(argv: list[str], deps: Deps) -> int:
    noninteractive = deps.noninteractive or "--noninteractive" in argv

    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    h = deps.home
    now = deps.now_factory()

    # 1. Publish backlog check
    pub_status = state.ensure_published(h)
    if pub_status.get("status") == "failed":
        return emit({"ok": False, "code": "PUBLISH_FAILED"})

    # 2. Check runtime sync
    needs_runtime_sync = False
    try:
        rt_status = runtime.status(h, deps.plugin_root)
        mcp_comp = rt_status.get("components", {}).get("line-desktop-mcp", {})
        if mcp_comp.get("code") == "RUNTIME_OUTDATED":
            needs_runtime_sync = True
    except Exception:
        pass

    if needs_runtime_sync:
        return emit({"ok": False, "code": "RUNTIME_OUTDATED",
                      "needs": {"runtime_sync": True, "line_trust": False, "refine": False}})

    # 3. Acquire lock
    try:
        lock_ctx = state.acquire_lock(h)
        lock_path = lock_ctx.__enter__()
    except state.Locked:
        return emit({"ok": False, "code": "LOCKED"})

    try:
        _sweep_excluded_history(h, cfg)
        return _run_pipeline(h, cfg, now, deps, noninteractive)
    finally:
        lock_ctx.__exit__(None, None, None)


def _run_pipeline(
    h: Path,
    cfg: dict[str, Any],
    now: datetime,
    deps: Deps,
    noninteractive: bool,
) -> int:
    st = state.load_state(h)
    ldg = state.load_ledger(h)
    run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)

    # Build sources
    line_src: Any = None
    needs_line_trust = False
    line_enabled = cfg.get("line", {}).get("enabled", False)

    if line_enabled:
        if deps.line_source_factory:
            try:
                line_src = deps.line_source_factory(
                    main_database=cfg["line"].get("main_database", ""),
                    trusted_builds_path=str(h / "trusted_builds.json"),
                )
            except Exception:
                # Runtime not installed — collect will report LINE skipped
                line_src = None

            # Quick probe to check trust
            probe_result = line_src.probe() if line_src else {"ok": False, "code": "LINE_RUNTIME_MISSING"}
            if not probe_result.get("ok"):
                code = probe_result.get("code", "")
                if code == "LINE_BUILD_UNVERIFIED":
                    needs_line_trust = True
                    if noninteractive:
                        line_src = _UntrustedLine()
                    else:
                        # Interactive: signal to caller
                        return emit({
                            "ok": False, "code": "LINE_BUILD_UNVERIFIED",
                            "needs": {"line_trust": True, "runtime_sync": False, "refine": False},
                        })
        else:
            line_src = None

    graph_src: Any = None
    if deps.graph_factory and cfg.get("mail", {}).get("enabled", True):
        try:
            graph_src = deps.graph_factory()
        except Exception:
            pass

    # Collect
    try:
        result = collect.collect(
            cfg, st, now=now, line=line_src, graph=graph_src, run_id=run_id,
            progress=deps.progress, initial_days=_INITIAL_DAYS,
        )
    except collect.CollectError as exc:
        return emit({"ok": False, "code": exc.code})

    # Load criteria, ctx, and profile
    crit_text = criteria.read_latest(h)
    ctx_entries = _load_ctx(h)
    from daily_agenda import profile as _profile
    profile_text = _profile.read_latest(h)

    # Write bundle
    runs_dir = h / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)

    open_todos = [t for t in ldg if t["status"] in ("open", "maybe_done")]

    # Load chat history for bundle context (C4 §B) — non-fatal
    precomputed_history = bundle.load_chat_history(h, result.records, run_id)
    history_sizes = bundle.compute_history_sizes(precomputed_history)

    # Account for profile + criteria overhead in batch sizing
    batch_bytes = cfg.get("analysis", {}).get("batch_bytes", 300_000)
    overhead = 0
    if profile_text:
        overhead += len(profile_text.encode("utf-8")) + 100
    if crit_text:
        overhead += len(crit_text.encode("utf-8")) + 100
    effective_batch = max(batch_bytes - overhead, batch_bytes // 3)
    batches = bundle.split_batches(
        result.records, effective_batch, history_sizes=history_sizes,
    )
    if not batches:
        batches = [[]]  # At least one batch (may have zero records)

    bundle_paths: list[Path] = []
    for i, batch_records in enumerate(batches):
        bp = runs_dir / f"bundle-{i + 1}.jsonl"

        class _BatchResult:
            def __init__(self, records: list, base: Any, idx: int, total: int) -> None:
                self.run_id = base.run_id
                self.generated_at = base.generated_at
                self.tz = base.tz
                self.records = records
                self.source_status = base.source_status
                self.new_chats = base.new_chats
                self.criteria_version = base.criteria_version

        batch_result = _BatchResult(batch_records, result, i + 1, len(batches))
        bundle.write_bundle(
            bp, result=batch_result, criteria=crit_text, open_todos=open_todos,
            ctx=ctx_entries, profile_text=profile_text,
            batch_index=i + 1, batch_total=len(batches),
            chat_history=precomputed_history,
        )
        bundle_paths.append(bp)

    # Analyze — with progress events per batch, single retry on parse failure
    progress = deps.progress
    model_output: dict[str, Any]
    try:
        prompt = analyze.load_prompt("agenda")
        n_batches = len(bundle_paths)
        items: list[Any] = []
        batch_chat_digests: list[list[dict[str, Any]]] = []
        batch_mail_digests: list[list[dict[str, Any]]] = []
        for bi, bp in enumerate(bundle_paths, 1):
            progress.event("analyze_batch", i=bi, n=n_batches)
            ar = _analyze_batch_with_retry(
                bp, prompt, cfg, progress,
            )
            progress.event("analyze_batch_done", i=bi, n=n_batches)
            # items is guaranteed to be a list by _analyze_batch_with_retry
            items.extend(ar.output.get("items", []))
            # Collect per-batch digests
            batch_chat_digests.append(ar.output.get("chat_digests", []) if isinstance(ar.output.get("chat_digests"), list) else [])
            batch_mail_digests.append(ar.output.get("mail_digests", []) if isinstance(ar.output.get("mail_digests"), list) else [])
        # Merge digests across batches (C2)
        merged_chat_digests = _merge_chat_digests(batch_chat_digests)
        merged_mail_digests = _merge_mail_digests(batch_mail_digests)
        model_output = {
            "schema_version": 2,
            "items": items,
            "chat_digests": merged_chat_digests,
            "mail_digests": merged_mail_digests,
        }
    except analyze.AnalyzerError as exc:
        return emit({"ok": False, "code": exc.code})

    # Write model.json
    from daily_agenda.fsutil import atomic_write_json
    atomic_write_json(runs_dir / "model.json", model_output)

    # Validate
    bundle_src_ids: set[str] = set()
    for rec in result.records:
        if rec.get("src_id"):
            bundle_src_ids.add(rec["src_id"])
    for todo in open_todos:
        for sid in todo.get("src_ids", []):
            bundle_src_ids.add(sid)

    open_todo_ids = {t["id"] for t in open_todos}

    try:
        val_result = validate.validate(
            model_output, bundle_src_ids, open_todo_ids,
            records=result.records,
        )
    except validate.ModelOutputInvalid:
        return emit({"ok": False, "code": "MODEL_OUTPUT_INVALID"})

    rejected_count = len(val_result.rejected)

    # Apply to ledger
    new_ldg = ledger.apply_model(ldg, val_result.items, now=now)
    new_ldg = ledger.auto_collapse(new_ldg, now=now)

    # Build view
    report_code = ledger.new_report_code()
    events = [r for r in result.records if r.get("kind") == "event"]
    skipped = [item for item in val_result.items if item.get("action") == "skip"]
    new_chat_names = [c.get("name", "") for c in result.new_chats]
    # F4b: emit only refs+count, not attacker-controlled names
    new_chat_refs = [c.get("ref", "") for c in result.new_chats]
    src_index = _build_src_index(result.records)

    # Build meta dict for view
    meta: dict[str, Any] = {
        "sources": result.source_status,
        "new_chats": [{"ref": c.get("ref", "")} for c in result.new_chats],
        "batch": {"index": 1, "total": len(batches)},
    }

    notices: list[str] = []
    if needs_line_trust:
        notices.append("LINE 更新到新版本，需要手動確認一次。")

    # Attach chat_name from bundle records to validated digests
    chat_names: dict[str, str] = {}
    for rec in result.records:
        if rec.get("kind") == "line_message":
            cref = rec.get("chat_ref", "")
            ut = rec.get("untrusted", {})
            if cref and cref not in chat_names:
                chat_names[cref] = ut.get("chat_name", "")
    enriched_chat_digests = []
    for d in val_result.chat_digests:
        enriched = dict(d)
        enriched["chat_name"] = chat_names.get(d.get("chat_ref", ""), "")
        enriched_chat_digests.append(enriched)

    view = render.build_view(
        state=st, ledger=new_ldg, bundle_meta=meta, events=events,
        new_chat_names=new_chat_names, notices=notices, now=now,
        report_code=report_code, skipped=skipped, src_index=src_index,
        records=result.records,
        chat_digests=val_result.chat_digests,
        mail_digests=val_result.mail_digests,
        folder_labels=cfg.get("mail", {}).get("folder_labels", {}),
        chat_history=precomputed_history,
    )

    # Render outputs
    formats = cfg.get("output", {}).get("formats", ["html"])
    out_dir = cfg.get("output", {}).get("dir")
    if not out_dir:
        out_dir = str(h / "output")

    outputs: dict[str, str] = {}
    publish_targets: list[dict] = []
    date_str = now.strftime("%Y-%m-%d")

    for fmt in formats:
        content = render.render(view, fmt)
        outputs[fmt] = content
        target_path = state.output_filename(Path(out_dir), date_str, fmt)
        publish_targets.append({"fmt": fmt, "path": str(target_path)})

    # Build new state
    new_st = dict(st)
    new_st["checkpoints"] = result.new_checkpoints
    if result.known_chats:
        new_st["known_chats"] = result.known_chats
    new_st["mail_index"] = result.mail_index or st.get("mail_index", {})
    new_st["runs_since_refine"] = st.get("runs_since_refine", 0) + 1
    new_st["criteria_version"] = criteria.latest_version(h)
    new_st["report_code"] = report_code
    new_st["report_index"] = view.get("report_index", {})
    new_st["last_run"] = {
        "run_id": run_id,
        "at": now.isoformat(),
        "sources": {k: {"status": v.get("status")} for k, v in result.source_status.items()},
        # F4b: store new_chats in state so `chats new` can retrieve names
        "new_chats": [{"ref": c.get("ref", ""), "name": c.get("name", "")} for c in result.new_chats],
    }

    # Stage, commit, publish
    staging = state.stage_generation(
        h, state=new_st, ledger=new_ldg, outputs=outputs, publish_targets=publish_targets,
    )
    gen = state.commit_generation(h, staging)

    # Write chat digest history (C4) — after successful commit, non-fatal
    try:
        history.insert_digests(
            h, run_id, now.isoformat(),
            enriched_chat_digests,
            records=result.records,
        )
    except Exception:
        pass  # Generation already committed; history failure is non-fatal

    publish_result: dict[str, Any] = {"status": "ok"}
    try:
        state.publish(h, gen)
    except state.PublishFailed:
        publish_result = {"status": "failed", "code": "PUBLISH_FAILED"}

    # Check refine needs
    refine_cfg = cfg.get("refine", {})
    every_runs = refine_cfg.get("every_runs", 10)
    every_days = refine_cfg.get("every_days", 14)
    needs_refine = new_st["runs_since_refine"] >= every_runs
    if not needs_refine and new_st.get("last_refine_at"):
        try:
            last_dt = datetime.fromisoformat(new_st["last_refine_at"])
            needs_refine = (now - last_dt).days >= every_days
        except (ValueError, TypeError):
            pass

    # Build output list
    output_paths = [pt["path"] for pt in publish_targets]

    # Source summary (no raw content)
    sources_summary: dict[str, Any] = {}
    for k, v in result.source_status.items():
        entry: dict[str, Any] = {"status": v.get("status", "skipped")}
        if v.get("code"):
            entry["code"] = v["code"]
            from daily_agenda.messages import humanize as _humanize
            entry["message"] = _humanize(v["code"])
        if "messages" in v:
            entry["count"] = v["messages"]
        if "events" in v:
            entry["count"] = v["events"]
        if "chats" in v:
            entry["chats"] = v["chats"]
        sources_summary[k] = entry

    # Counts
    counts = {
        "open": sum(1 for t in new_ldg if t["status"] == "open"),
        "maybe_done": sum(1 for t in new_ldg if t["status"] == "maybe_done"),
        "collapsed": sum(1 for t in new_ldg if t["status"] == "collapsed"),
        "done": sum(1 for t in new_ldg if t["status"] == "done"),
        "dismissed": sum(1 for t in new_ldg if t["status"] == "dismissed"),
        "p0": sum(1 for t in new_ldg if t["status"] == "open" and t.get("priority") == "P0"),
    }

    return emit({
        "ok": publish_result["status"] != "failed",
        "code": publish_result.get("code"),
        "report_code": report_code,
        "outputs": output_paths,
        "sources": sources_summary,
        "counts": counts,
        "new_chats_count": len(new_chat_refs),
        "new_chat_refs": new_chat_refs,
        "needs": {
            "line_trust": needs_line_trust,
            "refine": needs_refine,
            "runtime_sync": False,
        },
        "rejected_items": rejected_count,
    })
