"""Tests for daily_agenda.state — config, consent, generations, lock."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daily_agenda import state as st

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TZ)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """Provide a temporary DAILY_AGENDA_HOME."""
    h = tmp_path / "da-home"
    h.mkdir()
    return h


# ---------------------------------------------------------------------------
# home()
# ---------------------------------------------------------------------------

class TestHome:
    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = tmp_path / "custom"
        monkeypatch.setenv("DAILY_AGENDA_HOME", str(custom))
        assert st.home() == custom

    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DAILY_AGENDA_HOME", raising=False)
        assert st.home() == Path.home() / ".daily-agenda"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_missing(self, home: Path) -> None:
        cfg = st.load_config(home)
        assert cfg["consent"] is None
        assert cfg["schema_version"] == 1

    def test_save_and_load(self, home: Path) -> None:
        cfg = st.load_config(home)
        cfg["tz"] = "America/New_York"
        st.save_config(home, cfg)
        loaded = st.load_config(home)
        assert loaded["tz"] == "America/New_York"


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

class TestConsent:
    def test_require_consent_missing(self, home: Path) -> None:
        cfg = st.load_config(home)
        with pytest.raises(st.ConsentRequired):
            st.require_consent(cfg)

    def test_require_consent_null(self) -> None:
        with pytest.raises(st.ConsentRequired):
            st.require_consent({"consent": None})

    def test_require_consent_empty(self) -> None:
        with pytest.raises(st.ConsentRequired):
            st.require_consent({"consent": {}})

    def test_accept_and_require(self, home: Path) -> None:
        st.accept_consent(home, NOW)
        cfg = st.load_config(home)
        st.require_consent(cfg)  # should not raise
        assert cfg["consent"]["accepted_at"] == NOW.isoformat()

    def test_revoke(self, home: Path) -> None:
        st.accept_consent(home, NOW)
        st.revoke_consent(home)
        cfg = st.load_config(home)
        with pytest.raises(st.ConsentRequired):
            st.require_consent(cfg)


# ---------------------------------------------------------------------------
# Generation directory
# ---------------------------------------------------------------------------

class TestGeneration:
    def test_no_current(self, home: Path) -> None:
        assert st.current_generation(home) is None

    def test_load_state_no_gen(self, home: Path) -> None:
        s = st.load_state(home)
        assert s["generation"] == 0
        assert s["checkpoints"]["line"] is None

    def test_load_ledger_no_gen(self, home: Path) -> None:
        assert st.load_ledger(home) == []


# ---------------------------------------------------------------------------
# stage + commit
# ---------------------------------------------------------------------------

class TestStageCommit:
    def test_stage_and_commit(self, home: Path) -> None:
        state_data = {"schema_version": 1, "generation": 0, "report_code": "R-7K2"}
        ledger = [{"id": "T-0001", "title": "test"}]
        outputs = {"html": "<html>hello</html>"}
        targets = [{"fmt": "html", "path": str(home / "out" / "agenda-2026-10-08.html")}]

        staging = st.stage_generation(
            home, state=state_data, ledger=ledger,
            outputs=outputs, publish_targets=targets,
        )
        assert staging.name == "1.staging"
        assert (staging / "state.json").exists()
        assert (staging / "ledger.jsonl").exists()
        assert (staging / "agenda.html").exists()
        assert (staging / "publish.json").exists()

        gen = st.commit_generation(home, staging)
        assert gen == 1
        assert st.current_generation(home) == 1
        assert not staging.exists()  # renamed away

        # Verify state.
        loaded_state = st.load_state(home)
        assert loaded_state["generation"] == 1
        assert loaded_state["report_code"] == "R-7K2"

        # Verify ledger.
        loaded_ledger = st.load_ledger(home)
        assert len(loaded_ledger) == 1
        assert loaded_ledger[0]["id"] == "T-0001"

    def test_multiple_generations(self, home: Path) -> None:
        for i in range(3):
            state_data = {"schema_version": 1, "generation": i, "report_code": f"R-{i}"}
            outputs = {"md": f"# Gen {i + 1}"}
            targets = [{"fmt": "md", "path": str(home / "out" / f"agenda-{i}.md")}]
            staging = st.stage_generation(
                home, state=state_data, ledger=[],
                outputs=outputs, publish_targets=targets,
            )
            st.commit_generation(home, staging)

        assert st.current_generation(home) == 3


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

class TestPublish:
    def _stage_and_commit(self, home: Path, content: str = "<html>ok</html>") -> int:
        out_dir = home / "out"
        out_dir.mkdir(exist_ok=True)
        targets = [{"fmt": "html", "path": str(out_dir / "agenda.html")}]
        staging = st.stage_generation(
            home,
            state={"schema_version": 1, "generation": 0},
            ledger=[],
            outputs={"html": content},
            publish_targets=targets,
        )
        return st.commit_generation(home, staging)

    def test_publish(self, home: Path) -> None:
        gen = self._stage_and_commit(home)
        receipt = st.publish(home, gen)
        assert len(receipt["targets"]) == 1
        assert Path(receipt["targets"][0]["path"]).exists()
        # Receipt file written.
        assert (home / "gen" / str(gen) / "published").exists()

    def test_publish_idempotent(self, home: Path) -> None:
        gen = self._stage_and_commit(home)
        st.publish(home, gen)
        receipt2 = st.publish(home, gen)
        assert len(receipt2["targets"]) == 1  # skipped (already correct)


# ---------------------------------------------------------------------------
# ensure_published
# ---------------------------------------------------------------------------

class TestEnsurePublished:
    def test_no_current(self, home: Path) -> None:
        result = st.ensure_published(home)
        assert result["status"] == "ok"

    def test_already_published(self, home: Path) -> None:
        out = home / "out"
        out.mkdir()
        targets = [{"fmt": "html", "path": str(out / "a.html")}]
        staging = st.stage_generation(
            home, state={"schema_version": 1, "generation": 0},
            ledger=[], outputs={"html": "hi"}, publish_targets=targets,
        )
        gen = st.commit_generation(home, staging)
        st.publish(home, gen)
        result = st.ensure_published(home)
        assert result["status"] == "ok"

    def test_republish(self, home: Path) -> None:
        out = home / "out"
        out.mkdir()
        targets = [{"fmt": "html", "path": str(out / "a.html")}]
        staging = st.stage_generation(
            home, state={"schema_version": 1, "generation": 0},
            ledger=[], outputs={"html": "content"}, publish_targets=targets,
        )
        gen = st.commit_generation(home, staging)
        # Don't publish — ensure_published should do it.
        result = st.ensure_published(home)
        assert result["status"] == "republished"
        assert (out / "a.html").exists()

    def test_gen_dir_missing(self, home: Path) -> None:
        # Write a CURRENT pointing to nonexistent gen.
        (home / "gen").mkdir(parents=True, exist_ok=True)
        st._write_current(home, 99)
        result = st.ensure_published(home)
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# cleanup_uncommitted
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_removes_staging(self, home: Path) -> None:
        staging = home / "gen" / "5.staging"
        staging.mkdir(parents=True)
        (staging / "state.json").write_text("{}", encoding="utf-8")
        st.cleanup_uncommitted(home)
        assert not staging.exists()

    def test_removes_future_gen(self, home: Path) -> None:
        st._write_current(home, 3)
        future = home / "gen" / "5"
        future.mkdir(parents=True)
        st.cleanup_uncommitted(home)
        assert not future.exists()
        # gen/3 wouldn't be removed (not > current).

    def test_keeps_current(self, home: Path) -> None:
        gen3 = home / "gen" / "3"
        gen3.mkdir(parents=True)
        st._write_current(home, 3)
        st.cleanup_uncommitted(home)
        assert gen3.exists()


# ---------------------------------------------------------------------------
# output_filename
# ---------------------------------------------------------------------------

class TestOutputFilename:
    def test_first_file(self, tmp_path: Path) -> None:
        p = st.output_filename(tmp_path, "2026-10-08", "html")
        assert p == tmp_path / "agenda-2026-10-08.html"

    def test_same_day_dedup(self, tmp_path: Path) -> None:
        (tmp_path / "agenda-2026-10-08.html").write_text("x", encoding="utf-8")
        p = st.output_filename(tmp_path, "2026-10-08", "html")
        assert p == tmp_path / "agenda-2026-10-08-2.html"

    def test_same_day_triple(self, tmp_path: Path) -> None:
        (tmp_path / "agenda-2026-10-08.md").write_text("x", encoding="utf-8")
        (tmp_path / "agenda-2026-10-08-2.md").write_text("x", encoding="utf-8")
        p = st.output_filename(tmp_path, "2026-10-08", "md")
        assert p == tmp_path / "agenda-2026-10-08-3.md"


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

class TestLock:
    def test_acquire_and_release(self, home: Path) -> None:
        with st.acquire_lock(home) as lock_path:
            assert lock_path.exists()
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()
        assert not lock_path.exists()

    def test_double_lock_raises(self, home: Path) -> None:
        with st.acquire_lock(home):
            with pytest.raises(st.Locked):
                with st.acquire_lock(home):
                    pass  # pragma: no cover

    def test_stale_lock_takeover(self, home: Path) -> None:
        lock_dir = home / "runs"
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / "lock"
        old_time = (datetime.now().astimezone() - timedelta(seconds=2000)).isoformat()
        lock_path.write_text(
            json.dumps({"pid": 99999, "at": old_time}),
            encoding="utf-8",
        )
        with st.acquire_lock(home, stale_after_s=1200) as lp:
            assert lp.exists()
        assert not lock_path.exists()

    def test_corrupt_lock_takeover(self, home: Path) -> None:
        lock_dir = home / "runs"
        lock_dir.mkdir(parents=True)
        (lock_dir / "lock").write_text("corrupt", encoding="utf-8")
        with st.acquire_lock(home) as lp:
            assert lp.exists()
