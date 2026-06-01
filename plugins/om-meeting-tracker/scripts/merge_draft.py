from __future__ import annotations

"""merge_draft.py — thin CLI shell for M5 merge contract.

PLAN mode (default): parse a draft md, compute a merge plan, print JSON.
APPLY mode (--apply <decisions.json>): apply decisions, write tracking file + state.

Guard passthrough (spec clause 5): reads <repo_root>/<--strategy-index> if it exists
before any tracking-file read/write, to satisfy the read-before-write protocol that
the tenant-local strategy-edit-guard PreToolUse hook enforces.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# mt_core is importable via conftest sys.path manipulation in tests; for CLI use,
# scripts/ must be on sys.path.  We add it here so merge_draft.py can be run directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mt_core.config import load_config
from mt_core.merge import plan_merge, apply_decision
from mt_core.state import load_state, save_state


# ---------------------------------------------------------------------------
# Draft parser — extracts proposals from render_draft() output
# ---------------------------------------------------------------------------

# Matches "## <title>（<owner_name>）" — the section headings that render_draft emits.
_SECTION_HEAD = re.compile(r"^## (.+?)（.+?）\s*$", re.MULTILINE)

# Matches "- 回報：<text> (source: owner email)" lines; the text may contain any chars.
# Skips the placeholder "（尚無，列入待回填）" value.
_REPORT_LINE = re.compile(r"^- 回報：(.+?)\s*\(source:.*?\)\s*$", re.MULTILINE)

_PLACEHOLDER_TEXT = "（尚無，列入待回填）"


def _parse_draft_proposals(draft_md: str, title_to_metric_id: dict[str, str]) -> list[dict]:
    """Extract proposals from a render_draft() md string.

    Sections are matched by title → metric_id via config.  Lines with the
    placeholder text are skipped.  Each proposal gets field="owner_report".
    source_message_id is synthetic: "<metric_id>|<week>" where week is derived
    from the draft header, or just metric_id when not parseable.
    """
    # Extract week from draft header "# 準會議版 draft — YYYY-Www"
    week_match = re.search(r"#\s*準會議版 draft\s*[—-]\s*(\S+)", draft_md)
    week = week_match.group(1) if week_match else "unknown-week"

    # Split by section headings; collect (title, body) pairs.
    parts = _SECTION_HEAD.split(draft_md)
    # parts = [pre, title1, body1, title2, body2, ...]
    # Odd indices are titles; even (except 0) are bodies.
    proposals: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()
        body = parts[i + 1]
        metric_id = title_to_metric_id.get(title)
        if metric_id is None:
            continue  # Title not in config → skip

        for match in _REPORT_LINE.finditer(body):
            report_text = match.group(1).strip()
            if _PLACEHOLDER_TEXT in report_text:
                continue
            # NOTE (#3, codex P7 — consciously DEFERRED to M3.6 live integration):
            # source_message_id is synthesized as "<metric_id>|<week>" because the real
            # Gmail reply id is dropped at the draft-rendering boundary — render_draft
            # only emits r.text (draft.py:50); the real id lives upstream in
            # ReplyAttribution.msg_id but is only trustworthy once the Gmail connector
            # populates it, which cannot be verified without live mail. fact_hash in
            # reject_key already provides fact-level identity, so the only behavioral
            # delta is suppressing a re-report of an already-rejected identical fact
            # within the same week (desirable/neutral; single-owner-per-metric config
            # rules out the multi-owner over-suppression edge).
            # Fix recipe (M3.6): declare msg_id on the Report Protocol (draft.py:8-12),
            # embed it at draft.py:50, parse it back here via _REPORT_LINE (merge_draft.py:39).
            # Migration caveat: switching synthetic->real changes reject_key, so
            # synthetic-era merge.rejected entries won't match new proposals (one-time
            # re-surface of previously-rejected items — a decision, not a surprise).
            src_msg_id = f"{metric_id}|{week}"
            proposals.append({
                "metric_id": metric_id,
                "field": "owner_report",
                "new_value": report_text,
                "source_message_id": src_msg_id,
            })

    return proposals


# ---------------------------------------------------------------------------
# Index guard — read strategy index if it exists (spec clause 5)
# ---------------------------------------------------------------------------

def _read_strategy_index(repo_root: Path, index_name: str) -> bool:
    """Attempt to read <repo_root>/<index_name>. Returns True if found and read.

    This satisfies the read-before-write protocol for the strategy-edit-guard
    PreToolUse hook.  The CLI's responsibility is ONLY to honour the protocol
    (read first); enforcement of the guard itself is the tenant-local hook's job.
    """
    index_path = repo_root / index_name
    if index_path.exists():
        _ = index_path.read_text(encoding="utf-8")
        return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="merge_draft: compute or apply a merge plan from a rolling draft."
    )
    p.add_argument("--config", required=True, metavar="CONFIG_MD",
                   help="Path to the tenant config.md")
    p.add_argument("--repo-root", default=".", metavar="DIR",
                   help="Repo root for the index probe (default: current dir)")
    p.add_argument("--draft", required=True, metavar="DRAFT_MD",
                   help="Path to the draft md produced by render_draft()")
    p.add_argument("--today", default="", metavar="YYYY-MM-DD",
                   help="Optional: today's date for bookkeeping (unused in pure plan)")
    p.add_argument("--strategy-index", default="00_INDEX.md", metavar="FILENAME",
                   help="Filename to probe under repo-root as the strategy index "
                        "(default: 00_INDEX.md). Skipped silently if absent.")
    p.add_argument("--apply", metavar="DECISIONS_JSON",
                   help="Path to a JSON file with a list of "
                        "{item, decision, human_value?} objects; apply and write files.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()

    # --- Guard passthrough: read strategy index before any tracking-file I/O ---
    index_read = _read_strategy_index(repo_root, args.strategy_index)
    print(f"[merge_draft] strategy index read: {index_read} "
          f"({repo_root / args.strategy_index})")

    # --- Load config ---
    try:
        config = load_config(Path(args.config))
    except Exception as exc:
        print(f"[ERROR] config load failed: {exc}", file=sys.stderr)
        return 1

    # Build title → metric_id map
    title_to_mid: dict[str, str] = {m.title: m.metric_id for m in config.metrics}

    # --- Load draft ---
    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"[ERROR] draft not found: {draft_path}", file=sys.stderr)
        return 1
    draft_md = draft_path.read_text(encoding="utf-8")

    # Extract proposals
    proposals = _parse_draft_proposals(draft_md, title_to_mid)

    # --- Load tracking file ---
    tracking_path = repo_root / config.paths.tracking_file
    if tracking_path.exists():
        tracking_md = tracking_path.read_text(encoding="utf-8")
    else:
        tracking_md = ""

    # --- Load state ---
    state_path = repo_root / config.paths.state_file
    state = load_state(state_path, config.tenant_id)

    # --- Compute merge plan ---
    items = plan_merge(tracking_md, state, proposals)

    if args.apply is None:
        # PLAN mode: print JSON, write nothing.
        unreviewed = sum(1 for it in items if not it["reviewed"])
        output = {
            "items": items,
            "unreviewed": unreviewed,
            "index_read": index_read,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    # APPLY mode
    decisions_path = Path(args.apply)
    if not decisions_path.exists():
        print(f"[ERROR] decisions file not found: {decisions_path}", file=sys.stderr)
        return 1
    decisions_data = json.loads(decisions_path.read_text(encoding="utf-8"))
    if not isinstance(decisions_data, list):
        print("[ERROR] decisions JSON must be a list", file=sys.stderr)
        return 1

    # Build a lookup from reject_key → item for matching decisions to items.
    items_by_rk: dict[str, dict] = {it["reject_key"]: it for it in items}

    current_md = tracking_md
    current_state = state

    for decision_entry in decisions_data:
        rk = decision_entry.get("item", {}).get("reject_key") or ""
        item = items_by_rk.get(rk)
        if item is None:
            print(f"[WARN] reject_key not found in plan, skipping: {rk!r}")
            continue
        decision = decision_entry.get("decision", "")
        human_value = decision_entry.get("human_value")
        try:
            current_md, current_state = apply_decision(
                current_md, current_state, item, decision, human_value
            )
        except Exception as exc:
            print(f"[ERROR] apply_decision failed for {rk!r}: {exc}", file=sys.stderr)
            return 1

    # Write tracking file.
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    tracking_path.write_text(current_md, encoding="utf-8")

    # Write state.
    save_state(state_path, current_state)

    print(f"[merge_draft] applied {len(decisions_data)} decision(s). "
          f"Tracking file: {tracking_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
