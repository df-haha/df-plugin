"""SC-10 crash injection: fail at every _fail_point, recover, and verify consistency."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from daily_agenda import state as st


class _InjectError(Exception):
    """Artificial failure for crash injection tests."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML_CONTENT = "<html><body>agenda</body></html>"
_MD_CONTENT = "# Agenda\n\nHello."


def _make_state(gen: int = 0) -> dict:
    return {
        "schema_version": 1,
        "generation": gen,
        "report_code": "R-7K2",
        "report_index": {"1": "T-0001"},
        "checkpoints": {"line": None, "mail": None},
    }


def _make_ledger() -> list[dict]:
    return [{"id": "T-0001", "title": "Test", "status": "open"}]


def _outputs() -> dict[str, str]:
    return {"html": _HTML_CONTENT, "md": _MD_CONTENT}


def _publish_targets(out_dir: Path) -> list[dict]:
    return [
        {"fmt": "html", "path": str(out_dir / "agenda-2026-10-08.html")},
        {"fmt": "md", "path": str(out_dir / "agenda-2026-10-08.md")},
    ]


def _full_cycle(
    h: Path, out_dir: Path, state_data: dict | None = None,
) -> int:
    """stage → commit → publish; return gen number."""
    sd = state_data or _make_state()
    staging = st.stage_generation(
        h, state=sd, ledger=_make_ledger(),
        outputs=_outputs(), publish_targets=_publish_targets(out_dir),
    )
    gen = st.commit_generation(h, staging)
    st.publish(h, gen)
    return gen


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_consistency(h: Path, out_dir: Path, gen: int) -> None:
    """Assert CURRENT, gen state/ledger, published outputs, and receipt all consistent."""
    # CURRENT points to gen.
    assert st.current_generation(h) == gen

    gen_dir = h / "gen" / str(gen)

    # state.json in gen dir.
    state = json.loads((gen_dir / "state.json").read_text(encoding="utf-8"))
    assert state["generation"] == gen
    assert state["report_code"] == "R-7K2"

    # ledger.jsonl in gen dir.
    ledger_lines = (gen_dir / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(ledger_lines) == 1
    assert json.loads(ledger_lines[0])["id"] == "T-0001"

    # publish.json.
    pub = json.loads((gen_dir / "publish.json").read_text(encoding="utf-8"))
    for target in pub["targets"]:
        dest = Path(target["path"])
        assert dest.exists(), f"published file missing: {dest}"
        actual_sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert actual_sha == target["sha256"]

    # published receipt.
    receipt_path = gen_dir / "published"
    assert receipt_path.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert len(receipt["targets"]) == len(pub["targets"])

    # Output file content.
    html_path = out_dir / "agenda-2026-10-08.html"
    if html_path.exists():
        assert html_path.read_text(encoding="utf-8") == _HTML_CONTENT


# ---------------------------------------------------------------------------
# Parametrized crash injection
# ---------------------------------------------------------------------------

# Points that happen BEFORE CURRENT is written — staging/gen may exist
# but CURRENT is still at the old value. Recovery = cleanup + redo full cycle.
_PRE_CURRENT_POINTS = ["staged", "before_current_replace"]

# Points AFTER CURRENT — gen dir exists and CURRENT points to it,
# but publish or receipt may not be written. Recovery = ensure_published.
_POST_CURRENT_POINTS = [
    "after_current_replace",
    "before_publish_rename",
    "after_publish_rename",
    "before_receipt",
]


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    h = tmp_path / "da-home"
    h.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    return h, out


class TestCrashInjectionPreCurrent:
    """Failures before CURRENT is replaced — staging is discarded, full redo works."""

    @pytest.mark.parametrize("point", _PRE_CURRENT_POINTS)
    def test_crash_and_recover(
        self,
        env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        point: str,
    ) -> None:
        h, out = env

        # Inject failure.
        def inject(name: str) -> None:
            if name == point:
                raise _InjectError(f"injected at {point}")

        monkeypatch.setattr(st, "_fail_point", inject)

        with pytest.raises(_InjectError):
            _full_cycle(h, out)

        # No CURRENT should exist (or remains at 0).
        cur = st.current_generation(h)
        assert cur is None or cur == 0

        # Remove injection, redo.
        monkeypatch.setattr(st, "_fail_point", lambda name: None)

        gen = _full_cycle(h, out)
        _assert_consistency(h, out, gen)


class TestCrashInjectionPostCurrent:
    """Failures after CURRENT — ensure_published recovers publishing."""

    @pytest.mark.parametrize("point", _POST_CURRENT_POINTS)
    def test_crash_and_recover(
        self,
        env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        point: str,
    ) -> None:
        h, out = env

        def inject(name: str) -> None:
            if name == point:
                raise _InjectError(f"injected at {point}")

        monkeypatch.setattr(st, "_fail_point", inject)

        # Stage and commit will succeed (they're before publish for these points
        # except after_current_replace which is after CURRENT write).
        staging = st.stage_generation(
            h, state=_make_state(), ledger=_make_ledger(),
            outputs=_outputs(), publish_targets=_publish_targets(out),
        )

        if point == "after_current_replace":
            # Fail is inside commit_generation, AFTER CURRENT is written.
            # commit_generation raises but CURRENT already points to gen.
            with pytest.raises(_InjectError):
                st.commit_generation(h, staging)
            # CURRENT was written before the fail point.
            gen = st.current_generation(h)
            assert gen is not None
        else:
            # commit succeeds, publish fails at the point.
            gen = st.commit_generation(h, staging)
            with pytest.raises(_InjectError):
                st.publish(h, gen)

        # CURRENT exists and points to gen.
        assert st.current_generation(h) == gen

        # Remove injection, recover.
        monkeypatch.setattr(st, "_fail_point", lambda name: None)

        result = st.ensure_published(h)
        assert result["status"] in ("republished", "ok")

        _assert_consistency(h, out, gen)


class TestEnsurePublishedFailure:
    """ensure_published returns failed (not raises) when republish itself fails."""

    def test_publish_fails_returns_failed(
        self,
        env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        h, out = env

        # Do a normal stage+commit without publish.
        staging = st.stage_generation(
            h, state=_make_state(), ledger=_make_ledger(),
            outputs=_outputs(), publish_targets=_publish_targets(out),
        )
        gen = st.commit_generation(h, staging)

        # Inject permanent failure.
        def always_fail(name: str) -> None:
            if name == "before_publish_rename":
                raise RuntimeError("permanent fail")

        monkeypatch.setattr(st, "_fail_point", always_fail)

        result = st.ensure_published(h)
        assert result["status"] == "failed"
        assert result["code"] == "PUBLISH_FAILED"

        # No published receipt.
        assert not (h / "gen" / str(gen) / "published").exists()
        # CURRENT unchanged.
        assert st.current_generation(h) == gen


_HTML_CONTENT_2 = "<html><body>agenda v2</body></html>"
_MD_CONTENT_2 = "# Agenda v2\n\nUpdated."

_ALL_POINTS = _PRE_CURRENT_POINTS + _POST_CURRENT_POINTS


class TestTwoGenerationCrashInjection:
    """SC-10: gen 1 clean, gen 2 crashes at each point, recover, both consistent."""

    @pytest.mark.parametrize("point", _ALL_POINTS)
    def test_crash_on_gen2(
        self,
        env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        point: str,
    ) -> None:
        h, out = env

        # Gen 1: clean full cycle.
        gen1 = _full_cycle(h, out)
        _assert_consistency(h, out, gen1)
        gen1_html_path = out / "agenda-2026-10-08.html"
        gen1_html_sha = hashlib.sha256(gen1_html_path.read_bytes()).hexdigest()

        # Gen 2: different content, -2 filenames.
        outputs2 = {"html": _HTML_CONTENT_2, "md": _MD_CONTENT_2}
        targets2 = [
            {"fmt": "html", "path": str(out / "agenda-2026-10-08-2.html")},
            {"fmt": "md", "path": str(out / "agenda-2026-10-08-2.md")},
        ]
        state2 = _make_state(gen1)

        # Inject failure.
        def inject(name: str) -> None:
            if name == point:
                raise _InjectError(f"injected at {point}")

        monkeypatch.setattr(st, "_fail_point", inject)

        # Try gen 2 — will fail somewhere.
        try:
            staging = st.stage_generation(
                h, state=state2, ledger=_make_ledger(),
                outputs=outputs2, publish_targets=targets2,
            )
            if point in ("after_current_replace",):
                with pytest.raises(_InjectError):
                    st.commit_generation(h, staging)
            else:
                gen2 = st.commit_generation(h, staging)
                with pytest.raises(_InjectError):
                    st.publish(h, gen2)
        except _InjectError:
            pass

        # Remove injection, recover.
        monkeypatch.setattr(st, "_fail_point", lambda name: None)

        if point in _PRE_CURRENT_POINTS:
            # CURRENT still at gen1 or None; redo full cycle.
            result = st.ensure_published(h)
            # Re-stage gen 2.
            staging = st.stage_generation(
                h, state=state2, ledger=_make_ledger(),
                outputs=outputs2, publish_targets=targets2,
            )
            gen2 = st.commit_generation(h, staging)
            st.publish(h, gen2)
        else:
            # CURRENT at gen2, need to republish.
            gen2 = st.current_generation(h)
            assert gen2 is not None
            result = st.ensure_published(h)
            assert result["status"] in ("ok", "republished")

        # Gen 1 output still present with original hash.
        assert gen1_html_path.exists()
        assert hashlib.sha256(gen1_html_path.read_bytes()).hexdigest() == gen1_html_sha

        # Gen 2 consistent.
        gen2_dir = h / "gen" / str(gen2)
        assert gen2_dir.exists()
        assert (gen2_dir / "published").exists()


class TestFullCycleNoInjection:
    """Sanity: a full cycle without injection is consistent."""

    def test_full_cycle(self, env: tuple[Path, Path]) -> None:
        h, out = env
        gen = _full_cycle(h, out)
        _assert_consistency(h, out, gen)

    def test_two_generations(self, env: tuple[Path, Path]) -> None:
        h, out = env
        gen1 = _full_cycle(h, out)
        _assert_consistency(h, out, gen1)

        # Second generation.
        gen2 = _full_cycle(h, out, _make_state(gen1))
        assert gen2 == gen1 + 1
        _assert_consistency(h, out, gen2)
