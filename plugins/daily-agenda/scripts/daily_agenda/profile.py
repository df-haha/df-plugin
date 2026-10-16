"""Personal work profile management — versioned like criteria.

Stores ``profile/v<N>.md`` (append-only) plus ``profile/coverage.json``
and ``profile/cadence.json`` as sidecar state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text, atomic_write_json, read_json


# ---------------------------------------------------------------------------
# Dimension definitions (SSOT — templates/profile-dimensions.md mirrors this)
# ---------------------------------------------------------------------------

DIMENSIONS: dict[str, dict[str, Any]] = {
    "A": {
        "label": "我是誰",
        "questions": ["Q1"],
        "tier": "must-have",
        "guided": "你的全名、暱稱、英文名、LINE 顯示名稱、email、職稱、部門？",
    },
    "B": {
        "label": "職責範圍",
        "questions": ["Q1"],
        "tier": "must-have",
        "guided": "你負責什麼工作？有沒有明確不歸你管的事？",
    },
    "C": {
        "label": "重要的人",
        "questions": ["Q2"],
        "tier": "must-have",
        "guided": "列出上級、下屬、重要同事、客戶、廠商、政府窗口——他們的稱呼/LINE名/email、跟你的關係、通常找你做什麼？",
    },
    "D": {
        "label": "工作夥伴分工",
        "questions": ["Q4"],
        "tier": "important",
        "guided": "主管、同事、下屬、跨部門窗口各負責什麼？",
    },
    "E": {
        "label": "目前重點與目標",
        "questions": ["Q3"],
        "tier": "must-have",
        "guided": "今年/本季有哪些專案、KPI、績效目標？優先順序？",
    },
    "F": {
        "label": "時程與週期",
        "questions": ["Q3"],
        "tier": "important",
        "guided": "有哪些固定週期（週會、月結、請款日、報表日）？近期有哪些關鍵日期？",
    },
    "G": {
        "label": "已結束不用追",
        "questions": ["Q5"],
        "tier": "important",
        "guided": "有哪些已結案的專案、離職/轉調的人、退場站點？",
    },
    "H": {
        "label": "專有名詞對照",
        "questions": ["Q1", "Q3"],
        "tier": "important",
        "guided": "站名、客戶簡稱、產品名、內部常用縮寫有哪些？",
    },
    "I": {
        "label": "你怎麼結案",
        "questions": ["Q4"],
        "tier": "learnable",
        "guided": "你通常怎麼說一件事做完了？常用的完成說法、轉交方式？",
    },
    "J": {
        "label": "各管道用途",
        "questions": ["Q5"],
        "tier": "learnable",
        "guided": "哪些事走 LINE、哪些走信件？哪些群組是公告用、哪些是閒聊？",
    },
    "K": {
        "label": "背景知識",
        "questions": [],
        "tier": "learnable",
        "guided": "你的公司、產業、品牌有什麼 AI 需要知道的背景？",
    },
}

# Ordered dim IDs for iteration
DIM_IDS: list[str] = list(DIMENSIONS.keys())

# Must-have dimensions that should be asked first
MUST_HAVE_DIMS: list[str] = [d for d, v in DIMENSIONS.items() if v["tier"] == "must-have"]

# Valid cadence values
VALID_CADENCES = frozenset({"now", "agenda", "refine", "later"})

# Valid coverage statuses
VALID_STATUSES = frozenset({"have", "inferred", "missing"})

# Valid source types for coverage
VALID_SOURCES = frozenset({"docs", "raw", "interview", "existing"})

# ISO date regex (YYYY-MM-DD)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Max length for notes field
_MAX_NOTES_LEN = 200

# Max profile text size (bytes)
MAX_PROFILE_BYTES = 32 * 1024  # 32 KB


# ---------------------------------------------------------------------------
# Profile directory helpers
# ---------------------------------------------------------------------------

def _profile_dir(h: Path) -> Path:
    return h / "profile"


# ---------------------------------------------------------------------------
# Versioned profile (like criteria)
# ---------------------------------------------------------------------------

def list_versions(h: Path) -> list[int]:
    """Return sorted list of available profile version numbers."""
    d = _profile_dir(h)
    if not d.exists():
        return []
    versions = []
    for p in d.iterdir():
        m = re.match(r"^v(\d+)\.md$", p.name)
        if m:
            versions.append(int(m.group(1)))
    return sorted(versions)


def latest_version(h: Path) -> int:
    """Return the latest version number, or 0 if none."""
    versions = list_versions(h)
    return versions[-1] if versions else 0


def read_version(h: Path, version: int) -> str | None:
    """Read profile text for a specific version. Returns None if not found."""
    path = _profile_dir(h) / f"v{version}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def read_latest(h: Path) -> str | None:
    """Read the latest profile text."""
    v = latest_version(h)
    if v == 0:
        return None
    return read_version(h, v)


def save(h: Path, text: str) -> int:
    """Save new profile version; return the new version number.

    Raises ValueError if text exceeds MAX_PROFILE_BYTES.
    """
    text_bytes = len(text.encode("utf-8"))
    if text_bytes > MAX_PROFILE_BYTES:
        raise ValueError(
            f"profile too large: {text_bytes} bytes (max {MAX_PROFILE_BYTES})"
        )
    v = latest_version(h) + 1
    d = _profile_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / f"v{v}.md", text)
    return v


def restore(h: Path, version: int) -> int:
    """Copy an older version as the next version number. Returns new version."""
    text = read_version(h, version)
    if text is None:
        raise FileNotFoundError(f"profile version {version} not found")
    return save(h, text)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _coverage_path(h: Path) -> Path:
    return _profile_dir(h) / "coverage.json"


def load_coverage(h: Path) -> dict[str, Any] | None:
    """Load coverage data. Returns None if no coverage saved."""
    return read_json(_coverage_path(h))


def save_coverage(h: Path, coverage: dict[str, Any]) -> None:
    """Save coverage data after validation."""
    validate_coverage(coverage)
    d = _profile_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_coverage_path(h), coverage)


def validate_coverage(coverage: dict[str, Any]) -> None:
    """Validate coverage schema. Raises ValueError on invalid data."""
    if not isinstance(coverage, dict):
        raise ValueError("coverage must be a dict")

    # Reject unknown top-level keys
    _allowed_top = {"dimensions", "updated", "notes"}
    unknown_top = set(coverage.keys()) - _allowed_top
    if unknown_top:
        raise ValueError(f"unknown top-level keys: {unknown_top}")

    dims = coverage.get("dimensions")
    if not isinstance(dims, dict):
        raise ValueError("coverage.dimensions must be a dict")

    for dim_id in DIM_IDS:
        if dim_id not in dims:
            raise ValueError(f"missing dimension {dim_id}")

        entry = dims[dim_id]
        if not isinstance(entry, dict):
            raise ValueError(f"dimension {dim_id} must be a dict")

        # Reject unknown keys in dimension entry
        _allowed_dim = {"status", "sources", "notes"}
        unknown_dim = set(entry.keys()) - _allowed_dim
        if unknown_dim:
            raise ValueError(f"dimension {dim_id}: unknown keys {unknown_dim}")

        status = entry.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"dimension {dim_id}: invalid status {status!r}")

        sources = entry.get("sources")
        if not isinstance(sources, list):
            raise ValueError(f"dimension {dim_id}: sources must be a list")

        # Validate each source item
        for src in sources:
            if not isinstance(src, str):
                raise ValueError(f"dimension {dim_id}: source must be a string")
            if len(src) > 60:
                raise ValueError(f"dimension {dim_id}: source too long (max 60)")

        # Validate optional notes
        notes = entry.get("notes")
        if notes is not None:
            if not isinstance(notes, str) or len(notes) > _MAX_NOTES_LEN:
                raise ValueError(f"dimension {dim_id}: notes must be a string <= {_MAX_NOTES_LEN} chars")

    # Reject unknown dimension keys beyond A-K
    unknown_dims = set(dims.keys()) - set(DIM_IDS)
    if unknown_dims:
        raise ValueError(f"unknown dimension keys: {unknown_dims}")

    updated = coverage.get("updated")
    if not isinstance(updated, str):
        raise ValueError("coverage.updated must be a string")
    if not _ISO_DATE_RE.match(updated):
        raise ValueError(f"coverage.updated must be YYYY-MM-DD, got {updated!r}")

    # Validate optional top-level notes
    notes = coverage.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > _MAX_NOTES_LEN:
            raise ValueError(f"notes must be a string <= {_MAX_NOTES_LEN} chars")


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------

def _cadence_path(h: Path) -> Path:
    return _profile_dir(h) / "cadence.json"


def load_cadence(h: Path) -> str:
    """Load cadence setting. Returns 'later' if not set."""
    data = read_json(_cadence_path(h))
    if isinstance(data, dict):
        c = data.get("cadence", "later")
        if c in VALID_CADENCES:
            return c
    return "later"


def save_cadence(h: Path, cadence: str) -> None:
    """Save cadence setting."""
    if cadence not in VALID_CADENCES:
        raise ValueError(f"invalid cadence: {cadence!r}")
    d = _profile_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_cadence_path(h), {"cadence": cadence})


# ---------------------------------------------------------------------------
# Next question (rate-limited per run)
# ---------------------------------------------------------------------------

def _questions_path(h: Path) -> Path:
    return _profile_dir(h) / "questions.json"


def next_question(
    h: Path,
    current_generation: int | None,
    *,
    context: str | None = None,
) -> dict[str, Any]:
    """Return the next dimension to ask about.

    Parameters
    ----------
    context : ``"agenda"`` or ``"refine"`` — the calling context.
        When set, returns null if cadence doesn't match (e.g. cadence=refine
        but called from agenda → null).
    current_generation : used for rate limiting alongside date.

    Rate-limited: returns null question if already asked today or this generation.
    Returns ``{"cadence": str, "question": str|None, "dim": str|None}``.
    """
    cadence = load_cadence(h)

    # Only ask if cadence is 'agenda' or 'refine'
    if cadence not in ("agenda", "refine"):
        return {"cadence": cadence, "question": None, "dim": None}

    # Context filter: if caller specifies context, only return a question
    # when cadence matches that context
    if context is not None and cadence != context:
        return {"cadence": cadence, "question": None, "dim": None}

    # Check rate limit (by generation AND by date)
    qdata = read_json(_questions_path(h), default={})
    last_gen = qdata.get("last_asked_generation")
    last_date = qdata.get("last_asked_date")
    from datetime import date as _date
    today = _date.today().isoformat()

    # Rate-limited if asked this generation OR today
    if last_date == today:
        return {"cadence": cadence, "question": None, "dim": None}
    if current_generation is not None and last_gen == current_generation:
        return {"cadence": cadence, "question": None, "dim": None}

    # Find the next dimension with missing or inferred status
    coverage = load_coverage(h)
    if coverage is None:
        return {"cadence": cadence, "question": None, "dim": None}

    dims = coverage.get("dimensions", {})

    # Priority: must-have missing, then must-have inferred, then others
    for tier_filter, status_filter in [
        (MUST_HAVE_DIMS, "missing"),
        (MUST_HAVE_DIMS, "inferred"),
        (DIM_IDS, "missing"),
        (DIM_IDS, "inferred"),
    ]:
        for dim_id in tier_filter:
            entry = dims.get(dim_id, {})
            if entry.get("status") == status_filter:
                dim_def = DIMENSIONS[dim_id]
                return {
                    "cadence": cadence,
                    "question": dim_def["guided"],
                    "dim": dim_id,
                    "dim_label": dim_def["label"],
                }

    # All filled
    return {"cadence": cadence, "question": None, "dim": None}


def record_asked(h: Path, generation: int | None) -> None:
    """Record that a question was asked (rate limit by generation + date)."""
    d = _profile_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    from datetime import date as _date
    data: dict[str, Any] = {"last_asked_date": _date.today().isoformat()}
    if generation is not None:
        data["last_asked_generation"] = generation
    atomic_write_json(_questions_path(h), data)
