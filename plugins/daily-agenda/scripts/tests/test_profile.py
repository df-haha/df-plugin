"""Tests for the personal work profile module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daily_agenda import profile


# ---------------------------------------------------------------------------
# Dimension definitions sanity
# ---------------------------------------------------------------------------


class TestTemplatesAndConsent:
    """Verify templates and consent text mention profile-related content."""

    def test_analyze_prompt_mentions_profile(self) -> None:
        path = Path(__file__).resolve().parents[2] / "templates" / "analyze-prompt.md"
        text = path.read_text(encoding="utf-8")
        assert "profile" in text
        assert "Q1" in text

    def test_criteria_prompt_mentions_profile(self) -> None:
        path = Path(__file__).resolve().parents[2] / "templates" / "criteria-prompt.md"
        text = path.read_text(encoding="utf-8")
        assert "profile" in text

    def test_refine_prompt_mentions_profile(self) -> None:
        path = Path(__file__).resolve().parents[2] / "templates" / "refine-prompt.md"
        text = path.read_text(encoding="utf-8")
        assert "profile" in text

    def test_observe_prompt_mentions_profile(self) -> None:
        path = Path(__file__).resolve().parents[2] / "templates" / "criteria-observe-prompt.md"
        text = path.read_text(encoding="utf-8")
        assert "profile" in text

    def test_reduce_prompt_mentions_profile(self) -> None:
        path = Path(__file__).resolve().parents[2] / "templates" / "criteria-reduce-prompt.md"
        text = path.read_text(encoding="utf-8")
        assert "profile" in text

    def test_readme_mentions_docs(self) -> None:
        path = Path(__file__).resolve().parents[2] / "README.md"
        text = path.read_text(encoding="utf-8")
        assert "個人文件" in text

    def test_setup_skill_consent_mentions_docs(self) -> None:
        path = Path(__file__).resolve().parents[2] / "skills" / "agenda-setup" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        assert "個人文件" in text


class TestDimensions:
    def test_all_11_dimensions(self) -> None:
        assert len(profile.DIM_IDS) == 11
        assert profile.DIM_IDS == ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]

    def test_must_have_dims(self) -> None:
        assert set(profile.MUST_HAVE_DIMS) == {"A", "B", "C", "E"}

    def test_every_dim_has_required_fields(self) -> None:
        for dim_id, dim in profile.DIMENSIONS.items():
            assert "label" in dim, f"{dim_id} missing label"
            assert "questions" in dim, f"{dim_id} missing questions"
            assert "tier" in dim, f"{dim_id} missing tier"
            assert "guided" in dim, f"{dim_id} missing guided"
            assert dim["tier"] in ("must-have", "important", "learnable"), f"{dim_id} bad tier"

    def test_dimensions_md_mentions_all_dims(self) -> None:
        """Ensure the template mentions every dimension ID so they can't drift."""
        md_path = Path(__file__).resolve().parents[2] / "templates" / "profile-dimensions.md"
        if not md_path.exists():
            pytest.skip("profile-dimensions.md not yet created")
        text = md_path.read_text(encoding="utf-8")
        for dim_id in profile.DIM_IDS:
            assert f"## {dim_id}" in text or f"### {dim_id}" in text or f"**{dim_id}**" in text, \
                f"profile-dimensions.md missing dimension {dim_id}"


# ---------------------------------------------------------------------------
# Versioned profile (mirrors criteria tests)
# ---------------------------------------------------------------------------


class TestProfile:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert profile.list_versions(tmp_path) == []
        assert profile.latest_version(tmp_path) == 0
        assert profile.read_latest(tmp_path) is None

    def test_save_and_read(self, tmp_path: Path) -> None:
        v = profile.save(tmp_path, "# Profile v1\n## A 我是誰\n...")
        assert v == 1
        assert profile.read_version(tmp_path, 1) == "# Profile v1\n## A 我是誰\n..."
        assert profile.latest_version(tmp_path) == 1

    def test_multiple_versions(self, tmp_path: Path) -> None:
        profile.save(tmp_path, "Version 1")
        profile.save(tmp_path, "Version 2")
        profile.save(tmp_path, "Version 3")
        assert profile.list_versions(tmp_path) == [1, 2, 3]
        assert profile.latest_version(tmp_path) == 3
        assert profile.read_latest(tmp_path) == "Version 3"
        assert profile.read_version(tmp_path, 1) == "Version 1"

    def test_restore(self, tmp_path: Path) -> None:
        profile.save(tmp_path, "V1")
        profile.save(tmp_path, "V2")
        new_v = profile.restore(tmp_path, 1)
        assert new_v == 3
        assert profile.read_version(tmp_path, 3) == "V1"

    def test_restore_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            profile.restore(tmp_path, 99)

    def test_read_nonexistent_version(self, tmp_path: Path) -> None:
        assert profile.read_version(tmp_path, 99) is None

    def test_save_too_large(self, tmp_path: Path) -> None:
        big_text = "x" * (profile.MAX_PROFILE_BYTES + 1)
        with pytest.raises(ValueError, match="profile too large"):
            profile.save(tmp_path, big_text)

    def test_save_at_limit(self, tmp_path: Path) -> None:
        # ASCII: 1 byte per char
        text = "x" * profile.MAX_PROFILE_BYTES
        v = profile.save(tmp_path, text)
        assert v == 1


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def _make_coverage(
    *,
    overrides: dict[str, dict] | None = None,
    updated: str = "2026-10-15",
) -> dict:
    """Build a valid coverage dict."""
    dims: dict[str, dict] = {}
    for dim_id in profile.DIM_IDS:
        dims[dim_id] = {"status": "missing", "sources": []}
    if overrides:
        for k, v in overrides.items():
            dims[k].update(v)
    return {"dimensions": dims, "updated": updated}


class TestCoverage:
    def test_save_and_load(self, tmp_path: Path) -> None:
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)
        loaded = profile.load_coverage(tmp_path)
        assert loaded == cov

    def test_load_missing(self, tmp_path: Path) -> None:
        assert profile.load_coverage(tmp_path) is None

    def test_validate_all_dims_present(self) -> None:
        cov = _make_coverage()
        del cov["dimensions"]["K"]
        with pytest.raises(ValueError, match="missing dimension K"):
            profile.validate_coverage(cov)

    def test_validate_invalid_status(self) -> None:
        cov = _make_coverage(overrides={"A": {"status": "unknown", "sources": []}})
        with pytest.raises(ValueError, match="invalid status"):
            profile.validate_coverage(cov)

    def test_validate_sources_not_list(self) -> None:
        cov = _make_coverage(overrides={"A": {"status": "have", "sources": "bad"}})
        with pytest.raises(ValueError, match="sources must be a list"):
            profile.validate_coverage(cov)

    def test_validate_missing_updated(self) -> None:
        cov = _make_coverage()
        del cov["updated"]
        with pytest.raises(ValueError, match="updated"):
            profile.validate_coverage(cov)

    def test_valid_statuses(self) -> None:
        for status in ("have", "inferred", "missing"):
            cov = _make_coverage(overrides={"A": {"status": status, "sources": []}})
            profile.validate_coverage(cov)  # should not raise

    def test_validate_unknown_top_level_key(self) -> None:
        cov = _make_coverage()
        cov["evil"] = "injection"
        with pytest.raises(ValueError, match="unknown top-level"):
            profile.validate_coverage(cov)

    def test_validate_unknown_dim_key(self) -> None:
        cov = _make_coverage()
        cov["dimensions"]["A"]["evil"] = "injection"
        with pytest.raises(ValueError, match="unknown keys"):
            profile.validate_coverage(cov)

    def test_validate_unknown_dimension_id(self) -> None:
        cov = _make_coverage()
        cov["dimensions"]["Z"] = {"status": "missing", "sources": []}
        with pytest.raises(ValueError, match="unknown dimension keys"):
            profile.validate_coverage(cov)

    def test_validate_source_too_long(self) -> None:
        cov = _make_coverage(overrides={"A": {"status": "have", "sources": ["x" * 61]}})
        with pytest.raises(ValueError, match="source too long"):
            profile.validate_coverage(cov)

    def test_validate_updated_not_iso_date(self) -> None:
        cov = _make_coverage(updated="not-a-date")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            profile.validate_coverage(cov)

    def test_validate_notes_too_long(self) -> None:
        cov = _make_coverage()
        cov["dimensions"]["A"]["notes"] = "x" * 201
        with pytest.raises(ValueError, match="notes"):
            profile.validate_coverage(cov)

    def test_validate_with_valid_notes(self) -> None:
        cov = _make_coverage()
        cov["dimensions"]["A"]["notes"] = "Some inference note"
        profile.validate_coverage(cov)  # should not raise


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


class TestCadence:
    def test_default(self, tmp_path: Path) -> None:
        assert profile.load_cadence(tmp_path) == "later"

    def test_save_and_load(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "now")
        assert profile.load_cadence(tmp_path) == "now"

    def test_all_valid(self, tmp_path: Path) -> None:
        for c in ("now", "agenda", "refine", "later"):
            profile.save_cadence(tmp_path, c)
            assert profile.load_cadence(tmp_path) == c

    def test_invalid(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid cadence"):
            profile.save_cadence(tmp_path, "invalid")


# ---------------------------------------------------------------------------
# Next question
# ---------------------------------------------------------------------------


class TestNextQuestion:
    def test_cadence_later(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "later")
        result = profile.next_question(tmp_path, current_generation=1)
        assert result["cadence"] == "later"
        assert result["question"] is None
        assert result["dim"] is None

    def test_cadence_now(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "now")
        result = profile.next_question(tmp_path, current_generation=1)
        assert result["question"] is None

    def test_agenda_with_missing(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage(overrides={
            "A": {"status": "have", "sources": ["user"]},
            "B": {"status": "have", "sources": ["user"]},
            "C": {"status": "have", "sources": ["user"]},
            "E": {"status": "have", "sources": ["user"]},
        })
        profile.save_coverage(tmp_path, cov)

        result = profile.next_question(tmp_path, current_generation=1)
        assert result["cadence"] == "agenda"
        assert result["question"] is not None
        # Should pick D (first important missing) since all must-haves are done
        assert result["dim"] == "D"

    def test_must_have_first(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage()  # all missing
        profile.save_coverage(tmp_path, cov)

        result = profile.next_question(tmp_path, current_generation=1)
        assert result["dim"] == "A"  # first must-have

    def test_inferred_after_missing(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage(overrides={
            "A": {"status": "inferred", "sources": ["scan"]},
            "B": {"status": "have", "sources": ["user"]},
            "C": {"status": "have", "sources": ["user"]},
            "E": {"status": "have", "sources": ["user"]},
        })
        # No must-have missing, but A is inferred → ask A
        profile.save_coverage(tmp_path, cov)
        result = profile.next_question(tmp_path, current_generation=1)
        assert result["dim"] == "A"

    def test_rate_limit(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage()  # all missing
        profile.save_coverage(tmp_path, cov)

        result1 = profile.next_question(tmp_path, current_generation=5)
        assert result1["question"] is not None

        # Record that we asked (writes today's date + gen 5)
        profile.record_asked(tmp_path, 5)

        # Same generation AND same date → rate-limited
        result2 = profile.next_question(tmp_path, current_generation=5)
        assert result2["question"] is None

        # New generation but same date → still rate-limited (date-based)
        result3 = profile.next_question(tmp_path, current_generation=6)
        assert result3["question"] is None

        # Clear the date to simulate next day
        from daily_agenda.fsutil import atomic_write_json
        atomic_write_json(
            profile._questions_path(tmp_path),
            {"last_asked_generation": 5, "last_asked_date": "2020-01-01"},
        )
        result4 = profile.next_question(tmp_path, current_generation=6)
        assert result4["question"] is not None

    def test_all_have(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage(overrides={
            d: {"status": "have", "sources": ["user"]} for d in profile.DIM_IDS
        })
        profile.save_coverage(tmp_path, cov)

        result = profile.next_question(tmp_path, current_generation=1)
        assert result["question"] is None
        assert result["dim"] is None

    def test_no_coverage(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        result = profile.next_question(tmp_path, current_generation=1)
        assert result["question"] is None

    def test_context_agenda_matches(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)
        result = profile.next_question(tmp_path, current_generation=1, context="agenda")
        assert result["question"] is not None

    def test_context_agenda_blocks_refine(self, tmp_path: Path) -> None:
        """cadence=refine but called from agenda context → null."""
        profile.save_cadence(tmp_path, "refine")
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)
        result = profile.next_question(tmp_path, current_generation=1, context="agenda")
        assert result["question"] is None

    def test_context_refine_matches(self, tmp_path: Path) -> None:
        profile.save_cadence(tmp_path, "refine")
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)
        result = profile.next_question(tmp_path, current_generation=1, context="refine")
        assert result["question"] is not None

    def test_rate_limit_by_date(self, tmp_path: Path) -> None:
        """Rate limit works by date even without a generation."""
        profile.save_cadence(tmp_path, "agenda")
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)

        result1 = profile.next_question(tmp_path, current_generation=None)
        assert result1["question"] is not None

        # Record the ask (no generation)
        profile.record_asked(tmp_path, None)

        # Same day → rate-limited
        result2 = profile.next_question(tmp_path, current_generation=None)
        assert result2["question"] is None

    def test_context_none_allows_both(self, tmp_path: Path) -> None:
        """No context filter → cadence=agenda or refine both work."""
        profile.save_cadence(tmp_path, "refine")
        cov = _make_coverage()
        profile.save_coverage(tmp_path, cov)
        result = profile.next_question(tmp_path, current_generation=1)
        assert result["question"] is not None
