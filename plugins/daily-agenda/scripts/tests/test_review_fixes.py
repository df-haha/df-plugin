"""Tests for review findings: H3, M1, M3, M4, L1."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from daily_agenda import criteria, state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda.folder_walk import walk_folders, WalkResult

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 15, 9, 0, 0, tzinfo=TAIPEI)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        **overrides,
    )


def _run_cli(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")
    obj = json.loads(lines[-1])
    return code, obj


def _setup_home(tmp_path: Path) -> Path:
    state.accept_consent(tmp_path, NOW)
    cfg = state.load_config(tmp_path)
    cfg["line"]["enabled"] = False
    cfg["mail"]["enabled"] = False
    state.save_config(tmp_path, cfg)
    criteria.save(tmp_path, "# 判斷準則\n\n接受所有項目。")
    return tmp_path


def _make_folder(
    folder_id: str = "folder-001",
    name: str = "收件匣",
    total: int = 42,
    unread: int = 5,
    child_count: int = 0,
    hidden: bool = False,
) -> dict[str, Any]:
    return {
        "id": folder_id,
        "name": name,
        "total": total,
        "unread": unread,
        "child_count": child_count,
        "hidden": hidden,
    }


# ===========================================================================
# H3: criteria draft --from-run multi-batch crash
# ===========================================================================

class TestH3FromRunMultiBatch:
    """_BatchResult must accept dict base (from --from-run) without AttributeError."""

    def test_from_run_multi_batch_no_crash(self, tmp_path: Path, capsys: Any) -> None:
        """Force multiple batches with small batch_bytes; --from-run must not crash."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        # Tiny batch size to force multi-batch
        cfg["analysis"]["batch_bytes"] = 50
        state.save_config(h, cfg)

        # Create a fake scan run with records
        from daily_agenda.scan_raw import save_records, save_source_status
        run_id = "20261015-0900-ab12"
        run_dir = h / "runs" / run_id
        run_dir.mkdir(parents=True)
        records = [
            {"kind": "mail", "src_id": f"mail:msg-{i}",
             "untrusted": {"subject": f"Subject {i}" * 20, "body": "x" * 200}}
            for i in range(5)
        ]
        save_records(records, run_dir)
        save_source_status({"line": {"status": "skipped"}}, run_dir)

        deps = _deps(h)

        # Mock load_prompt to return distinguishable prompts
        def _fake_load_prompt(name: str) -> str:
            return f"PROMPT:{name}"

        # Mock analyzer: observe prompts → observations, reduce prompt → criteria
        def _fake_analyzer(stdin_bytes: bytes, prompt: str, **kw: Any) -> Any:
            from daily_agenda.analyze import AnalyzerResult
            if "observe" in prompt:
                return AnalyzerResult(
                    output={
                        "schema_version": 1,
                        "observations": [
                            {"category": "mail_pattern", "pattern": "test",
                             "frequency": "high", "priority_signal": "P1"},
                        ],
                    },
                    ms=100,
                )
            else:
                return AnalyzerResult(
                    output={
                        "schema_version": 1,
                        "criteria_markdown": "# 新準則\n\n根據分析結果。",
                    },
                    ms=100,
                )

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_fake_analyzer):
            with patch("daily_agenda.analyze.load_prompt", side_effect=_fake_load_prompt):
                code, obj = _run_cli(
                    ["criteria", "draft", "--from-run", run_id],
                    deps, capsys,
                )

        # Before the fix: AttributeError: 'dict' object has no attribute 'run_id'
        # After the fix: should succeed
        assert obj["ok"] is True, f"Expected ok=True, got: {obj}"
        assert "draft_path" in obj

    def test_batch_result_accepts_dict(self) -> None:
        """_BatchResult works with both dict and object base."""
        from daily_agenda.cli.cmd_criteria import _BatchResult

        # Dict base (from --from-run)
        dict_base = {
            "run_id": "test-123",
            "generated_at": "2026-10-15T09:00:00",
            "tz": "Asia/Taipei",
            "source_status": {},
            "new_chats": [],
            "criteria_version": 1,
        }
        br = _BatchResult([], dict_base)
        assert br.run_id == "test-123"

        # Object base (from fresh collect)
        class ObjBase:
            run_id = "test-456"
            generated_at = "2026-10-15T09:00:00"
            tz = "Asia/Taipei"
            source_status: dict = {}
            new_chats: list = []
            criteria_version = 1

        br2 = _BatchResult([], ObjBase())
        assert br2.run_id == "test-456"


# ===========================================================================
# M1: criteria draft / refine draft return fenced draft_text
# ===========================================================================

class TestM1FencedDraftText:
    """CLI draft commands return fenced text so Claude never Reads runs/ files."""

    def test_criteria_draft_returns_draft_text(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)

        from daily_agenda.scan_raw import save_records, save_source_status
        run_id = "20261015-0900-ab12"
        run_dir = h / "runs" / run_id
        run_dir.mkdir(parents=True)
        save_records([], run_dir)
        save_source_status({}, run_dir)

        deps = _deps(h)

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            from daily_agenda.analyze import AnalyzerResult
            mock_analyzer.return_value = AnalyzerResult(
                output={"schema_version": 1, "criteria_markdown": "# 準則草稿\n\n測試內容。"},
                ms=100,
            )
            code, obj = _run_cli(
                ["criteria", "draft", "--from-run", run_id],
                deps, capsys,
            )

        assert obj["ok"] is True
        assert "draft_text" in obj
        assert obj["draft_text"].startswith("<<<UNTRUSTED-")
        assert "準則草稿" in obj["draft_text"]

    def test_refine_draft_returns_draft_text(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)

        # Set up ledger with feedback
        from daily_agenda.fsutil import jsonl_line
        todo = {
            "id": "T-0001", "title": "Test", "priority": "P1", "status": "done",
            "src_ids": ["mail:AAMk-001"],
            "created_at": (NOW - __import__("datetime").timedelta(days=15)).isoformat(),
            "updated_at": NOW.isoformat(), "due": None,
            "maybe_done_since": None, "maybe_done_reason": None,
            "history": [{"at": (NOW - __import__("datetime").timedelta(days=1)).isoformat(),
                         "from": "open", "to": "done", "by": "user"}],
        }
        st = state.load_state(h)
        st["generation"] = 1
        gen_dir = h / "gen" / "1"
        gen_dir.mkdir(parents=True)
        (gen_dir / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (gen_dir / "ledger.jsonl").write_text(jsonl_line(todo) + "\n", encoding="utf-8")
        (h / "CURRENT").write_text("1\n", encoding="utf-8")

        deps = _deps(h)
        fake_output = {
            "schema_version": 1,
            "criteria_markdown": "# 改進後\n\n新版準則。",
            "changes": [{"summary": "改了 A", "reason": "因為 B"}],
        }
        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            from daily_agenda.analyze import AnalyzerResult
            mock_analyzer.return_value = AnalyzerResult(output=fake_output, ms=100)
            code, obj = _run_cli(["refine", "draft"], deps, capsys)

        assert obj["ok"] is True
        assert "draft_text" in obj
        assert obj["draft_text"].startswith("<<<UNTRUSTED-")
        assert "改進後" in obj["draft_text"]


# ===========================================================================
# M3: draft new / draft show
# ===========================================================================

class TestM3DraftNewShow:
    """Test the new 'draft' CLI subcommand."""

    def test_draft_new_criteria(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        code, obj = _run_cli(["draft", "new", "--kind", "criteria"], deps, capsys)
        assert obj["ok"] is True
        assert obj["kind"] == "criteria"
        p = Path(obj["draft_path"])
        assert p.name == "criteria-draft.md"
        assert "runs/drafts/" in obj["draft_path"]
        # Should be seeded with current criteria
        assert p.read_text(encoding="utf-8").startswith("# 判斷準則")
        assert obj["seeded"] is True

    def test_draft_new_profile(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        code, obj = _run_cli(["draft", "new", "--kind", "profile"], deps, capsys)
        assert obj["ok"] is True
        assert obj["kind"] == "profile"
        assert Path(obj["draft_path"]).name == "profile-draft.md"

    def test_draft_new_coverage(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        code, obj = _run_cli(["draft", "new", "--kind", "coverage"], deps, capsys)
        assert obj["ok"] is True
        assert obj["kind"] == "coverage"
        assert Path(obj["draft_path"]).name == "coverage-draft.json"
        # Empty seed
        assert Path(obj["draft_path"]).read_text(encoding="utf-8") == "{}"

    def test_draft_new_invalid_kind(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        code, obj = _run_cli(["draft", "new", "--kind", "badkind"], deps, capsys)
        assert obj["ok"] is False
        assert code == 2

    def test_draft_new_no_kind(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        code, obj = _run_cli(["draft", "new"], deps, capsys)
        assert obj["ok"] is False

    def test_draft_show_fenced(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        # Create a draft first
        code, obj = _run_cli(["draft", "new", "--kind", "criteria"], deps, capsys)
        draft_path = obj["draft_path"]
        # Write some content
        Path(draft_path).write_text("# My Draft\n\nContent here.", encoding="utf-8")

        code, obj = _run_cli(["draft", "show", "--file", draft_path], deps, capsys)
        assert obj["ok"] is True
        assert "text" in obj
        assert obj["text"].startswith("<<<UNTRUSTED-")
        assert "My Draft" in obj["text"]

    def test_draft_show_rejects_non_draft_filename(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        # Create a file with wrong name under runs/
        bad_file = h / "runs" / "drafts" / "test" / "bundle.jsonl"
        bad_file.parent.mkdir(parents=True)
        bad_file.write_text("{}", encoding="utf-8")
        code, obj = _run_cli(["draft", "show", "--file", str(bad_file)], deps, capsys)
        assert obj["ok"] is False
        detail = obj.get("detail", "")
        assert "known draft filename" in detail or "basename must be one of" in detail

    def test_draft_show_missing_file(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)
        missing = h / "runs" / "drafts" / "xxx" / "criteria-draft.md"
        missing.parent.mkdir(parents=True)
        code, obj = _run_cli(["draft", "show", "--file", str(missing)], deps, capsys)
        assert obj["ok"] is False
        assert "not found" in obj.get("detail", "")

    def test_draft_consent_required(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)  # No consent
        code, obj = _run_cli(["draft", "new", "--kind", "criteria"], deps, capsys)
        assert obj["code"] == "CONSENT_REQUIRED"


# ===========================================================================
# M4: folder walk completeness reporting
# ===========================================================================

class TestM4FolderWalkCompleteness:
    """walk_folders returns WalkResult with complete/skipped/truncated."""

    def test_complete_walk(self) -> None:
        tree = {"": [_make_folder("f1", "收件匣")]}
        result = walk_folders(lambda pid: tree.get(pid, []))
        assert isinstance(result, WalkResult)
        assert result.complete is True
        assert result.skipped == []
        assert result.truncated is None
        assert len(result.folders) == 1

    def test_skipped_subtree(self) -> None:
        def failing_list(parent_id: str) -> list[dict[str, Any]]:
            if parent_id == "f1":
                raise RuntimeError("Graph 404")
            return [_make_folder("f1", "Inbox", child_count=2)]

        result = walk_folders(failing_list)
        assert result.complete is False
        assert len(result.skipped) == 1
        assert result.skipped[0]["reason"] == "RuntimeError"
        assert len(result.folders) == 1

    def test_truncated_max_total(self) -> None:
        tree = {"": [_make_folder(f"f{i}", f"Folder{i}") for i in range(50)]}
        result = walk_folders(lambda pid: tree.get(pid, []), max_total=10)
        assert result.complete is False
        assert result.truncated is not None
        assert "max_total" in result.truncated
        assert len(result.folders) == 10

    def test_depth_truncation(self) -> None:
        """4-level tree with max_depth=3 must mark truncated, not complete."""
        tree: dict[str, list[dict[str, Any]]] = {
            "": [_make_folder("f1", "A", child_count=1)],
            "f1": [_make_folder("f2", "B", child_count=1)],
            "f2": [_make_folder("f3", "C", child_count=1)],
            "f3": [_make_folder("f4", "D")],
        }
        result = walk_folders(lambda pid: tree.get(pid, []), max_depth=3)
        paths = [f["path"] for f in result.folders]
        assert "A" in paths
        assert "A / B" in paths
        assert "A / B / C" in paths
        # D is at depth 4, beyond max_depth=3
        assert "A / B / C / D" not in paths
        assert result.complete is False
        assert result.truncated is not None
        assert "max_depth" in result.truncated

    def test_select_refuses_depth_truncated_ref(self, tmp_path: Path, capsys: Any) -> None:
        """A ref only reachable beyond max_depth triggers MAIL_FOLDERS_INCOMPLETE."""
        state.accept_consent(tmp_path, NOW)

        class DeepGraph:
            def folder_list(self, parent_id: str) -> list[dict[str, Any]]:
                tree: dict[str, list[dict[str, Any]]] = {
                    "": [_make_folder("f1", "收件匣", child_count=1)],
                    "f1": [_make_folder("f2", "Sub1", child_count=1)],
                    "f2": [_make_folder("f3", "Sub2", child_count=1)],
                    "f3": [_make_folder("deep-ref", "Deep")],
                }
                return tree.get(parent_id, [])

        deps = _deps(tmp_path, graph_factory=lambda: DeepGraph())
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "deep-ref"],
            deps, capsys,
        )
        assert obj["ok"] is False
        assert obj["code"] == "MAIL_FOLDERS_INCOMPLETE"

    def test_mail_folders_cli_surfaces_completeness(self, tmp_path: Path, capsys: Any) -> None:
        from tests.conftest import FakeGraph
        state.accept_consent(tmp_path, NOW)
        tree = {"": [_make_folder("f1", "收件匣")]}
        graph = FakeGraph(folder_tree=tree)
        deps = _deps(tmp_path, graph_factory=lambda: graph)
        code, obj = _run_cli(["mail", "folders"], deps, capsys)
        assert obj["ok"] is True
        assert obj["complete"] is True

    def test_mail_folders_cli_incomplete(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)

        class FailingGraph:
            def folder_list(self, parent_id: str) -> list[dict[str, Any]]:
                if parent_id == "f1":
                    raise RuntimeError("child fail")
                return [_make_folder("f1", "收件匣", child_count=1)]

        deps = _deps(tmp_path, graph_factory=lambda: FailingGraph())
        code, obj = _run_cli(["mail", "folders"], deps, capsys)
        assert obj["ok"] is True
        assert obj["complete"] is False
        assert len(obj["skipped"]) == 1

    def test_select_refuses_unverifiable_ref_on_incomplete(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)

        class PartialGraph:
            def folder_list(self, parent_id: str) -> list[dict[str, Any]]:
                if parent_id == "f1":
                    raise RuntimeError("subtree fail")
                return [_make_folder("f1", "收件匣", child_count=1)]

        deps = _deps(tmp_path, graph_factory=lambda: PartialGraph())
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "unknown-ref"],
            deps, capsys,
        )
        assert obj["ok"] is False
        assert obj["code"] == "MAIL_FOLDERS_INCOMPLETE"

    def test_select_allow_incomplete_override(self, tmp_path: Path, capsys: Any) -> None:
        """--allow-incomplete stores the ref as-is when inventory is incomplete."""
        state.accept_consent(tmp_path, NOW)

        class PartialGraph:
            def folder_list(self, parent_id: str) -> list[dict[str, Any]]:
                if parent_id == "f1":
                    raise RuntimeError("subtree fail")
                return [_make_folder("f1", "收件匣", child_count=1)]

        deps = _deps(tmp_path, graph_factory=lambda: PartialGraph())
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "unknown-ref", "--allow-incomplete"],
            deps, capsys,
        )
        # With --allow-incomplete the ref is accepted (user confirmed it exists)
        assert obj["ok"] is True
        assert obj["selected_count"] == 2
        assert "unknown-ref" in obj["folders"]

    def test_allow_incomplete_not_in_refs(self, tmp_path: Path, capsys: Any) -> None:
        """--allow-incomplete must not leak into the stored refs list."""
        state.accept_consent(tmp_path, NOW)

        class PartialGraph:
            def folder_list(self, parent_id: str) -> list[dict[str, Any]]:
                if parent_id == "f1":
                    raise RuntimeError("subtree fail")
                return [_make_folder("f1", "收件匣", child_count=1)]

        deps = _deps(tmp_path, graph_factory=lambda: PartialGraph())
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "deep-id", "--allow-incomplete"],
            deps, capsys,
        )
        assert obj["ok"] is True
        assert "--allow-incomplete" not in obj["folders"]

    def test_allow_incomplete_on_complete_inventory_still_rejects(self, tmp_path: Path, capsys: Any) -> None:
        """When inventory IS complete, a missing ref is ARG_ERROR regardless of flag."""
        from tests.conftest import FakeGraph
        state.accept_consent(tmp_path, NOW)
        tree = {"": [_make_folder("f1", "收件匣")]}
        graph = FakeGraph(folder_tree=tree)
        deps = _deps(tmp_path, graph_factory=lambda: graph)
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "nonexistent", "--allow-incomplete"],
            deps, capsys,
        )
        assert obj["ok"] is False
        assert obj["code"] == "ARG_ERROR"


# ===========================================================================
# L1: docs_convert atomic write
# ===========================================================================

class TestL1AtomicWrite:
    """docs_convert writes output files atomically: a crash during rename
    must not corrupt the previous file or leave temp debris."""

    def test_failed_write_preserves_original(self, tmp_path: Path) -> None:
        """Patch os.replace to raise mid-rename.  The previous output must
        survive intact and no .tmp file may remain."""
        from unittest.mock import patch as _patch
        from daily_agenda.docs_convert import convert_paths

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        src = input_dir / "note.txt"
        src.write_text("hello world", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        # Pre-populate the output file that atomic_write should leave untouched
        preexisting = output_dir / "note.txt"
        preexisting.write_text("previous content", encoding="utf-8")

        import os as _os
        real_replace = _os.replace

        def _exploding_replace(s: str, d: str) -> None:
            if str(output_dir) in str(d):
                Path(s).unlink(missing_ok=True)  # mimic except cleanup
                raise OSError("disk full")
            real_replace(s, d)

        with _patch("os.replace", side_effect=_exploding_replace):
            result = convert_paths([src], output_dir)

        assert result["skipped_count"] >= 1
        assert any(s["reason"] == "write_failed" for s in result["skipped"])
        # Original content intact
        assert preexisting.read_text(encoding="utf-8") == "previous content"
        # No temp files left
        temps = [f for f in output_dir.iterdir() if ".tmp" in f.name]
        assert temps == []
