"""Deterministic parser for the digest-page confirmation text block (C5).

The page produces a fixed format:

    確認待辦 R-XXX
    完成：1、8
    刪除：6
    改優先：3→P0
    其餘保留          (or 全部保留)

This module accepts the full-width and ASCII variants of the separators
(：/:, 、/,/，/spaces, →/->) and tolerates surrounding blank lines,
leading/trailing whitespace, and missing optional lines.  No eval, no shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INPUT_BYTE_CAP: int = 8192
INPUT_LINE_CAP: int = 20

_VALID_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2"})

# Header must start with 確認待辦; report code is optional.
_HEADER_RE = re.compile(r"^確認待辦(?:\s+(R-[A-Z0-9]{3}))?\s*$")

# Number separator: 、 or , or ， or whitespace
_NUM_SEP = re.compile(r"[、,，\s]+")

# Priority change: number → priority (→ or ->)
_PRI_RE = re.compile(r"(\d+)\s*(?:→|->)\s*(P[012])")

# Line key patterns — match the key prefix, ignoring colon variants
_KEY_RE = re.compile(
    r"^(完成|刪除|改優先)\s*[：:]\s*(.+)$"
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ParsedConfirm:
    """Result of parsing the confirmation text block."""

    report_code: str | None
    done_numbers: list[int] = field(default_factory=list)
    drop_numbers: list[int] = field(default_factory=list)
    priority_changes: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class ConfirmError:
    """A single validation error."""

    no: int | None  # item number, or None for structural errors
    reason: str


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_confirm_text(text: str) -> ParsedConfirm:
    """Parse the confirmation text block.

    Raises ``ValueError`` with a user-safe message on structural errors.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    if not lines:
        raise ValueError("確認文字為空")

    if len(lines) > INPUT_LINE_CAP:
        raise ValueError(f"行數超過上限 ({INPUT_LINE_CAP})")

    # Header
    header_match = _HEADER_RE.match(lines[0])
    if not header_match:
        raise ValueError("第一行必須以「確認待辦」開頭")

    report_code = header_match.group(1)  # may be None

    result = ParsedConfirm(report_code=report_code)
    seen_keys: set[str] = set()

    for line_idx, line in enumerate(lines[1:], start=2):
        # Skip trailing 其餘保留 / 全部保留
        if line in ("其餘保留", "全部保留"):
            continue

        m = _KEY_RE.match(line)
        if not m:
            raise ValueError(f"第 {line_idx} 行格式不正確（須為「完成：」「刪除：」「改優先：」或「其餘保留」）")

        key = m.group(1)
        value = m.group(2).strip()

        if key in seen_keys:
            raise ValueError(f"重複的動作行：{key}")
        seen_keys.add(key)

        if key == "完成":
            result.done_numbers = _parse_numbers(value)
        elif key == "刪除":
            result.drop_numbers = _parse_numbers(value)
        elif key == "改優先":
            result.priority_changes = _parse_priority_changes(value)

    return result


def _parse_numbers(value: str) -> list[int]:
    """Parse a separator-delimited list of integers."""
    parts = _NUM_SEP.split(value.strip())
    nums: list[int] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not p.isdigit():
            raise ValueError("項目編號必須是數字")
        nums.append(int(p))
    return nums


def _parse_priority_changes(value: str) -> list[tuple[int, str]]:
    """Parse priority change entries like ``3→P0、5→P1``."""
    parts = _NUM_SEP.split(value.strip())
    changes: list[tuple[int, str]] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = _PRI_RE.fullmatch(p)
        if not m:
            raise ValueError("改優先格式須為「編號→P0」「編號→P1」「編號→P2」")
        pri = m.group(2).upper()
        if pri not in _VALID_PRIORITIES:
            raise ValueError(f"無效的優先序：{pri}")
        changes.append((int(m.group(1)), pri))
    return changes


# ---------------------------------------------------------------------------
# Validation (semantic check against state)
# ---------------------------------------------------------------------------

def validate_confirm(
    parsed: ParsedConfirm,
    *,
    state: dict,
    ledger: list[dict],
) -> list[ConfirmError]:
    """Validate parsed confirmation against current state and ledger.

    Returns a list of errors.  Empty list means all items are valid.
    """
    errors: list[ConfirmError] = []

    report_index = state.get("report_index", {})

    # Collect all mentioned numbers and check for cross-action conflicts.
    all_numbers: dict[int, list[str]] = {}
    for n in parsed.done_numbers:
        all_numbers.setdefault(n, []).append("完成")
    for n in parsed.drop_numbers:
        all_numbers.setdefault(n, []).append("刪除")
    for n, _pri in parsed.priority_changes:
        all_numbers.setdefault(n, []).append("改優先")

    # Check duplicates within a single action (shouldn't happen from the page
    # but could from manual editing).
    for nums, action in [
        (parsed.done_numbers, "完成"),
        (parsed.drop_numbers, "刪除"),
    ]:
        seen: set[int] = set()
        for n in nums:
            if n in seen:
                errors.append(ConfirmError(no=n, reason=f"編號 {n} 在{action}中重複"))
            seen.add(n)
    pri_seen: set[int] = set()
    for n, _ in parsed.priority_changes:
        if n in pri_seen:
            errors.append(ConfirmError(no=n, reason=f"編號 {n} 在改優先中重複"))
        pri_seen.add(n)

    # Cross-action conflict: same number in multiple action types.
    for n, actions in all_numbers.items():
        if len(actions) > 1:
            errors.append(ConfirmError(
                no=n,
                reason=f"編號 {n} 同時出現在 {'、'.join(actions)}",
            ))

    # Resolve numbers to todo IDs; check existence.
    from daily_agenda.ledger import DISMISSABLE, PRIORITY_EDITABLE, _TRANSITIONS

    for n, actions in all_numbers.items():
        tid = report_index.get(str(n))
        if tid is None:
            errors.append(ConfirmError(no=n, reason=f"編號 {n} 不在目前報表中"))
            continue

        # Find the todo in the ledger.
        todo = None
        for t in ledger:
            if t["id"] == tid:
                todo = t
                break

        if todo is None:
            errors.append(ConfirmError(no=n, reason=f"編號 {n} 對應的待辦 {tid} 不存在"))
            continue

        status = todo["status"]

        # Check status compatibility for each action.
        for action in actions:
            if action == "完成":
                if (status, "done") not in _TRANSITIONS:
                    errors.append(ConfirmError(
                        no=n,
                        reason=f"編號 {n} 狀態為 {status}，無法標記完成",
                    ))
            elif action == "刪除":
                if status not in DISMISSABLE:
                    errors.append(ConfirmError(
                        no=n,
                        reason=f"編號 {n} 狀態為 {status}，無法刪除",
                    ))
            elif action == "改優先":
                if status not in PRIORITY_EDITABLE:
                    errors.append(ConfirmError(
                        no=n,
                        reason=f"編號 {n} 狀態為 {status}，無法改優先序",
                    ))

    return errors
