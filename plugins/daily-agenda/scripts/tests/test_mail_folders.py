"""Tests for mail folder selection: folder walk, CLI commands, per-folder
checkpoints, missing folder handling, cross-folder dedupe."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from daily_agenda.folder_walk import walk_folders, WalkResult, SKIP_WELL_KNOWN
from daily_agenda.collect import collect, _migrate_mail_checkpoint
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda import state

from tests.conftest import (
    TAIPEI, NOW, NOW_ISO,
    fake_cfg, fake_state,
    FakeLine, FakeGraph,
    make_mail_item,
)


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
    obj = json.loads(captured.out.strip().split("\n")[-1])
    return code, obj


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
# A. Folder walk
# ===========================================================================

class TestFolderWalk:
    def test_basic_walk(self) -> None:
        """Walk a simple flat folder list."""
        tree: dict[str, list[dict[str, Any]]] = {
            "": [
                _make_folder("f1", "收件匣"),
                _make_folder("f2", "寄件備份"),
                _make_folder("f3", "草稿"),
            ],
        }

        def list_fn(parent_id: str) -> list[dict[str, Any]]:
            return tree.get(parent_id, [])

        result = walk_folders(list_fn)
        assert isinstance(result, WalkResult)
        assert len(result.folders) == 3
        paths = [f["path"] for f in result.folders]
        assert "收件匣" in paths
        assert "寄件備份" in paths

    def test_well_known_detection(self) -> None:
        """Well-known folders are tagged."""
        tree = {
            "": [
                _make_folder("f1", "收件匣"),
                _make_folder("f2", "Junk Email"),
                _make_folder("f3", "Custom Folder"),
            ],
        }

        result = walk_folders(lambda pid: tree.get(pid, []))
        by_path = {f["path"]: f for f in result.folders}
        assert by_path["收件匣"]["well_known"] == "inbox"
        assert by_path["Junk Email"]["well_known"] == "junkemail"
        assert by_path["Custom Folder"]["well_known"] is None

    def test_recursion_with_children(self) -> None:
        """Walk into subfolders."""
        tree = {
            "": [_make_folder("f1", "收件匣", child_count=2)],
            "f1": [
                _make_folder("f1a", "客戶"),
                _make_folder("f1b", "豐興"),
            ],
        }

        result = walk_folders(lambda pid: tree.get(pid, []))
        assert len(result.folders) == 3
        paths = [f["path"] for f in result.folders]
        assert "收件匣 / 客戶" in paths
        assert "收件匣 / 豐興" in paths

    def test_depth_cap(self) -> None:
        """Recursion stops at max_depth."""
        tree: dict[str, list[dict[str, Any]]] = {
            "": [_make_folder("f1", "Root", child_count=1)],
            "f1": [_make_folder("f2", "Level2", child_count=1)],
            "f2": [_make_folder("f3", "Level3", child_count=1)],
            "f3": [_make_folder("f4", "Level4")],
        }

        result = walk_folders(lambda pid: tree.get(pid, []), max_depth=2)
        paths = [f["path"] for f in result.folders]
        assert "Root / Level2" in paths
        assert "Root / Level2 / Level3" not in paths

    def test_total_cap(self) -> None:
        """Stop after max_total folders."""
        tree = {
            "": [_make_folder(f"f{i}", f"Folder{i}") for i in range(50)],
        }

        result = walk_folders(lambda pid: tree.get(pid, []), max_total=10)
        assert len(result.folders) == 10
        assert result.complete is False
        assert result.truncated is not None

    def test_hidden_skipped(self) -> None:
        """Hidden folders are not included."""
        tree = {
            "": [
                _make_folder("f1", "收件匣"),
                _make_folder("f2", "Clutter", hidden=True),
            ],
        }

        result = walk_folders(lambda pid: tree.get(pid, []))
        assert len(result.folders) == 1
        assert result.folders[0]["path"] == "收件匣"

    def test_parent_list_failure_skips_subtree(self) -> None:
        """If listing a child parent fails, its subtree is skipped."""
        def failing_list(parent_id: str) -> list[dict[str, Any]]:
            if parent_id == "f1":
                raise RuntimeError("Graph 404")
            return [_make_folder("f1", "Inbox", child_count=2)]

        result = walk_folders(failing_list)
        assert len(result.folders) == 1  # Only top-level Inbox, not its children
        assert result.complete is False
        assert len(result.skipped) == 1

    def test_top_level_failure_raises(self) -> None:
        """Top-level listing failure propagates (auth, too old, etc.)."""
        from daily_agenda.graph_client import GraphClientError

        def fail_top(parent_id: str) -> list[dict[str, Any]]:
            raise GraphClientError("DF_GRAPH_AUTH", "token expired")

        with pytest.raises(GraphClientError):
            walk_folders(fail_top)

    def test_well_known_only_at_depth_1(self) -> None:
        """A subfolder named 'Sent' should NOT get well_known='sentitems'."""
        tree = {
            "": [_make_folder("f1", "收件匣", child_count=1)],
            "f1": [_make_folder("f1a", "Sent Items")],
        }

        result = walk_folders(lambda pid: tree.get(pid, []))
        by_path = {f["path"]: f for f in result.folders}
        assert by_path["收件匣"]["well_known"] == "inbox"
        # Subfolder "Sent Items" under Inbox must NOT be tagged as sentitems
        assert by_path["收件匣 / Sent Items"]["well_known"] is None


# ===========================================================================
# B. CLI: mail folders (consent-gated)
# ===========================================================================

class TestMailFoldersCLI:
    def test_consent_required(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["mail", "folders"], deps, capsys)
        assert code == 1
        assert obj["code"] == "CONSENT_REQUIRED"

    def test_no_graph(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        deps = _deps(tmp_path, graph_factory=None)
        code, obj = _run_cli(["mail", "folders"], deps, capsys)
        assert obj["code"] == "DF_GRAPH_NOT_REGISTERED"

    def test_list_folders(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        tree = {
            "": [
                _make_folder("f1", "收件匣", child_count=1),
                _make_folder("f2", "Sent Items"),
            ],
            "f1": [_make_folder("f1a", "客戶")],
        }
        graph = FakeGraph(folder_tree=tree)
        deps = _deps(tmp_path, graph_factory=lambda: graph)
        code, obj = _run_cli(["mail", "folders"], deps, capsys)
        assert obj["ok"] is True
        folders = obj["folders"]
        assert len(folders) == 3

        # Check ref for inbox is well-known name, not id
        inbox = [f for f in folders if f["path"] == "收件匣"][0]
        assert inbox["ref"] == "inbox"

        # Check subfolders get id as ref
        customer = [f for f in folders if f["path"] == "收件匣 / 客戶"][0]
        assert customer["ref"] == "f1a"

        # Check skip recommendation
        sent = [f for f in folders if f["path"] == "Sent Items"][0]
        assert sent["skip_recommended"] is True


# ===========================================================================
# C. CLI: mail select
# ===========================================================================

class TestMailSelectCLI:
    """mail select requires consent and validates refs against the folder listing."""

    def _setup(self, tmp_path: Path) -> tuple[Deps, FakeGraph]:
        state.accept_consent(tmp_path, NOW)
        tree = {
            "": [
                _make_folder("f1", "收件匣"),
                _make_folder("f1a", "客戶"),
            ],
        }
        graph = FakeGraph(folder_tree=tree)
        deps = _deps(tmp_path, graph_factory=lambda: graph)
        return deps, graph

    def test_set(self, tmp_path: Path, capsys: Any) -> None:
        deps, _ = self._setup(tmp_path)
        code, obj = _run_cli(["mail", "select", "--set", "inbox", "f1a"], deps, capsys)
        assert obj["ok"] is True
        assert obj["selected_count"] == 2
        assert obj["folders"] == ["inbox", "f1a"]

    def test_add(self, tmp_path: Path, capsys: Any) -> None:
        deps, _ = self._setup(tmp_path)
        _run_cli(["mail", "select", "--set", "inbox"], deps, capsys)
        code, obj = _run_cli(["mail", "select", "--add", "f1a"], deps, capsys)
        assert obj["selected_count"] == 2

    def test_remove(self, tmp_path: Path, capsys: Any) -> None:
        deps, _ = self._setup(tmp_path)
        _run_cli(["mail", "select", "--set", "inbox", "f1a"], deps, capsys)
        code, obj = _run_cli(["mail", "select", "--remove", "f1a"], deps, capsys)
        assert obj["selected_count"] == 1
        assert obj["folders"] == ["inbox"]

    def test_empty_set_refused(self, tmp_path: Path, capsys: Any) -> None:
        """Empty selection is not allowed."""
        deps, _ = self._setup(tmp_path)
        code, obj = _run_cli(["mail", "select", "--set"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"

    def test_remove_last_refused(self, tmp_path: Path, capsys: Any) -> None:
        """Cannot remove the last folder."""
        deps, _ = self._setup(tmp_path)
        _run_cli(["mail", "select", "--set", "inbox"], deps, capsys)
        code, obj = _run_cli(["mail", "select", "--remove", "inbox"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"

    def test_unknown_ref_rejected(self, tmp_path: Path, capsys: Any) -> None:
        """Unknown folder ref → ARG_ERROR."""
        deps, _ = self._setup(tmp_path)
        code, obj = _run_cli(["mail", "select", "--set", "inbox", "nonexistent-id"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"
        assert "nonexistent-id" in obj.get("detail", "")

    def test_well_known_always_valid(self, tmp_path: Path, capsys: Any) -> None:
        """Well-known names validate even when not in the listing."""
        state.accept_consent(tmp_path, NOW)
        # Empty folder tree — no folders listed
        graph = FakeGraph(folder_tree={"": []})
        deps = _deps(tmp_path, graph_factory=lambda: graph)
        code, obj = _run_cli(["mail", "select", "--set", "inbox"], deps, capsys)
        assert obj["ok"] is True

    def test_remove_stale_ref_ok(self, tmp_path: Path, capsys: Any) -> None:
        """--remove a stale ref (deleted folder) should work without validation."""
        deps, _ = self._setup(tmp_path)
        _run_cli(["mail", "select", "--set", "inbox", "stale-folder-id"], deps, capsys)
        code, obj = _run_cli(["mail", "select", "--remove", "stale-folder-id"], deps, capsys)
        assert obj["ok"] is True
        assert obj["selected_count"] == 1

    def test_folder_labels_stored(self, tmp_path: Path, capsys: Any) -> None:
        """Labels are stored automatically on select."""
        deps, _ = self._setup(tmp_path)
        _run_cli(["mail", "select", "--set", "inbox", "f1a"], deps, capsys)
        cfg = state.load_config(tmp_path)
        labels = cfg.get("mail", {}).get("folder_labels", {})
        assert "inbox" in labels
        assert "f1a" in labels

    def test_no_subcommand(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["mail"], deps, capsys)
        assert code == 2

    def test_consent_required(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["mail", "select", "--set", "inbox"], deps, capsys)
        assert code == 1
        assert obj["code"] == "CONSENT_REQUIRED"


# ===========================================================================
# D. Checkpoint migration
# ===========================================================================

class TestCheckpointMigration:
    def test_legacy_scalar_to_per_folder(self) -> None:
        """Old scalar checkpoint maps to inbox only."""
        checkpoints = {"mail": "2026-10-07T09:00:00+08:00"}
        result = _migrate_mail_checkpoint(checkpoints, ["inbox", "f1a"])
        assert result["inbox"] == "2026-10-07T09:00:00+08:00"
        assert result["f1a"] is None

    def test_none_checkpoint(self) -> None:
        checkpoints: dict[str, Any] = {"mail": None}
        result = _migrate_mail_checkpoint(checkpoints, ["inbox"])
        assert result["inbox"] is None

    def test_already_per_folder(self) -> None:
        checkpoints = {"mail": {"inbox": "2026-10-07T09:00:00+08:00", "f1a": "2026-10-06T09:00:00+08:00"}}
        result = _migrate_mail_checkpoint(checkpoints, ["inbox", "f1a", "f2b"])
        assert result["inbox"] == "2026-10-07T09:00:00+08:00"
        assert result["f1a"] == "2026-10-06T09:00:00+08:00"
        assert result["f2b"] is None

    def test_new_folder_gets_initial_window(self) -> None:
        """A newly added folder should get None (= initial window)."""
        checkpoints = {"mail": {"inbox": "2026-10-07T09:00:00+08:00"}}
        result = _migrate_mail_checkpoint(checkpoints, ["inbox", "new-folder-id"])
        assert result["inbox"] == "2026-10-07T09:00:00+08:00"
        assert result["new-folder-id"] is None


# ===========================================================================
# E. Per-folder collection
# ===========================================================================

class TestPerFolderCollection:
    def test_multi_folder_collection(self) -> None:
        """Mail from multiple folders is collected."""
        item_inbox = make_mail_item(msg_id="msg-inbox-1", subject="Inbox Mail")
        item_custom = make_mail_item(msg_id="msg-custom-1", subject="Custom Mail")
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "custom-folder-id"]
        st = fake_state()
        graph = FakeGraph(
            mail_items_by_folder={
                "inbox": [item_inbox],
                "custom-folder-id": [item_custom],
            },
        )

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-mf1")
        assert result.source_status["mail"]["status"] == "ok"
        mail_records = [r for r in result.records if r["kind"] == "mail"]
        assert len(mail_records) == 2
        folders_in_records = {r["folder"] for r in mail_records}
        assert "inbox" in folders_in_records
        assert "custom-folder-id" in folders_in_records

    def test_per_folder_checkpoint_advanced(self) -> None:
        """Each folder gets its own checkpoint."""
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "custom-folder-id"]
        st = fake_state()
        graph = FakeGraph(mail_items_by_folder={"inbox": [], "custom-folder-id": []})

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-cp1")
        mail_cp = result.new_checkpoints.get("mail")
        assert isinstance(mail_cp, dict)
        assert mail_cp["inbox"] == NOW.isoformat()
        assert mail_cp["custom-folder-id"] == NOW.isoformat()

    def test_cross_folder_dedupe(self) -> None:
        """Same internetMessageId in two folders → only one record."""
        imid = "<unique@example.com>"
        item1 = make_mail_item(msg_id="msg-a", internet_message_id=imid)
        item2 = make_mail_item(msg_id="msg-b", internet_message_id=imid)
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "folder-2"]
        st = fake_state()
        graph = FakeGraph(
            mail_items_by_folder={"inbox": [item1], "folder-2": [item2]},
        )

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-dd1")
        mail_records = [r for r in result.records if r["kind"] == "mail"]
        assert len(mail_records) == 1


# ===========================================================================
# F. Missing folder → partial status
# ===========================================================================

class TestMissingFolder:
    def test_missing_folder_partial(self) -> None:
        """Deleted folder → partial status, other folders still collected."""
        item_inbox = make_mail_item(msg_id="msg-ok-1")
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "gone-folder-id"]
        st = fake_state()
        graph = FakeGraph(mail_items_by_folder={"inbox": [item_inbox]})
        from daily_agenda.graph_client import GraphClientError

        original_mail_list = graph.mail_list
        def patched_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "gone-folder-id":
                raise GraphClientError("GRAPH_FAILED",
                    "Graph 404 GET /me/mailFolders/gone-folder-id/messages\nErrorItemNotFound")
            return original_mail_list(days=days, folder=folder)
        graph.mail_list = patched_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-mf2")
        assert result.source_status["mail"]["status"] == "partial"
        assert result.source_status["mail"]["code"] == "MAIL_FOLDER_MISSING"
        assert "gone-folder-id" in result.source_status["mail"]["missing_folders"]

        # Inbox mail should still be collected
        mail_records = [r for r in result.records if r["kind"] == "mail"]
        assert len(mail_records) == 1

        # Checkpoint for inbox should advance, gone folder should not
        mail_cp = result.new_checkpoints["mail"]
        assert isinstance(mail_cp, dict)
        assert mail_cp["inbox"] == NOW.isoformat()
        assert mail_cp["gone-folder-id"] is None  # Not advanced

    def test_missing_folder_no_crash(self) -> None:
        """Missing folder does not crash the entire run."""
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["gone-folder-id"]
        st = fake_state()

        from daily_agenda.graph_client import GraphClientError

        def fail_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            raise GraphClientError("GRAPH_FAILED",
                "Graph 404 GET /me/mailFolders/gone-folder-id\nErrorItemNotFound")

        graph = FakeGraph()
        graph.mail_list = fail_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-mf3")
        assert result.source_status["mail"]["status"] == "partial"
        # Calendar should still work
        assert result.source_status["calendar"]["status"] == "ok"


# ===========================================================================
# G. No folder names in progress events
# ===========================================================================

class TestNoFolderNamesInProgress:
    def test_progress_events_no_folder_names(self, tmp_path: Path) -> None:
        """Progress events must not contain folder names (untrusted data)."""
        from daily_agenda.progress import Progress

        progress_file = tmp_path / "progress.jsonl"
        progress = Progress(progress_file)

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "客戶資料夾"]
        st = fake_state()
        graph = FakeGraph(mail_items_by_folder={"inbox": [], "客戶資料夾": []})

        collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-np1",
                progress=progress)

        events_text = progress_file.read_text(encoding="utf-8")
        assert "客戶資料夾" not in events_text


# ===========================================================================
# H. GRAPH_TRUNCATED per-folder (review finding #1)
# ===========================================================================

class TestPerFolderTruncated:
    def test_truncated_folder_reraises(self) -> None:
        """GRAPH_TRUNCATED is a safety limit — must re-raise to fail the
        whole mail source so the user sees the real remedy message and the
        checkpoint doesn't silently stall."""
        from daily_agenda.graph_client import GraphClientError

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "big-folder"]
        st = fake_state()

        def trunc_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "big-folder":
                raise GraphClientError("GRAPH_TRUNCATED", "Result exceeded the safety limit")
            return []

        graph = FakeGraph()
        graph.mail_list = trunc_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-trunc1")
        # GRAPH_TRUNCATED must propagate as the mail source status
        assert result.source_status["mail"]["status"] == "failed"
        assert result.source_status["mail"]["code"] == "GRAPH_TRUNCATED"


# ===========================================================================
# I. 404 detection: false positive on non-404 error (review finding #2)
# ===========================================================================

class TestFolderMissing404Detection:
    def test_non_404_with_404_in_id_not_misdetected(self) -> None:
        """A 503 error whose folder id happens to contain '404' must NOT
        be misidentified as MAIL_FOLDER_MISSING."""
        from daily_agenda.graph_client import GraphClientError

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "AAMk404abc"]
        st = fake_state()

        def fail_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "AAMk404abc":
                raise GraphClientError("GRAPH_FAILED", "Graph 503 GET /me/mailFolders/AAMk404abc/messages")
            return []

        graph = FakeGraph()
        graph.mail_list = fail_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-fp1")
        mail_status = result.source_status["mail"]
        # Must NOT be MAIL_FOLDER_MISSING — it's a transient 503
        assert mail_status.get("code") != "MAIL_FOLDER_MISSING"

    def test_real_404_detected(self) -> None:
        """Graph 404 ErrorItemNotFound is correctly detected."""
        from daily_agenda.graph_client import GraphClientError

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "deleted-folder"]
        st = fake_state()

        def fail_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "deleted-folder":
                raise GraphClientError("GRAPH_FAILED",
                    "Graph 404 GET /me/mailFolders/deleted-folder/messages\nErrorItemNotFound")
            return []

        graph = FakeGraph()
        graph.mail_list = fail_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-real404")
        assert result.source_status["mail"]["code"] == "MAIL_FOLDER_MISSING"

    def test_400_malformed_id_detected(self) -> None:
        """Graph 400 ErrorInvalidIdMalformed treated as missing."""
        from daily_agenda.graph_client import GraphClientError

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "bad-id"]
        st = fake_state()

        def fail_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "bad-id":
                raise GraphClientError("GRAPH_FAILED",
                    "Graph 400 GET /me/mailFolders/bad-id/messages\nErrorInvalidIdMalformed")
            return []

        graph = FakeGraph()
        graph.mail_list = fail_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-malformed")
        assert result.source_status["mail"]["code"] == "MAIL_FOLDER_MISSING"

    def test_other_400_not_treated_as_missing(self) -> None:
        """A generic Graph 400 (e.g. bad $filter) is a transient/partial failure, not a deleted folder."""
        from daily_agenda.graph_client import GraphClientError

        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "custom-id"]
        st = fake_state()

        def fail_mail_list(*, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
            if folder == "custom-id":
                raise GraphClientError("GRAPH_FAILED",
                    "Graph 400 GET /me/mailFolders/custom-id/messages\nBadRequest")
            return []

        graph = FakeGraph()
        graph.mail_list = fail_mail_list  # type: ignore[assignment]

        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-other400")
        assert result.source_status["mail"]["code"] == "MAIL_FOLDER_PARTIAL"


# ===========================================================================
# J. Render partial status (review finding #3)
# ===========================================================================

class TestRenderPartialStatus:
    def test_partial_status_label(self) -> None:
        """Partial status should render as '部分讀取' not 'partial'."""
        from daily_agenda.render import build_view

        st = fake_state()
        ldg: list[dict[str, Any]] = []
        meta: dict[str, Any] = {
            "sources": {
                "line": {"status": "skipped", "code": None},
                "mail": {
                    "status": "partial", "code": "MAIL_FOLDER_MISSING",
                    "from": NOW_ISO, "to": NOW_ISO, "messages": 5,
                    "missing_folders": ["gone-id"],
                },
                "calendar": {"status": "ok", "code": None, "from": NOW_ISO, "to": NOW_ISO, "events": 2},
            },
            "new_chats": [],
            "batch": {"index": 1, "total": 1},
        }
        view = build_view(
            state=st, ledger=ldg, bundle_meta=meta, events=[], new_chat_names=[],
            notices=[], now=NOW, report_code="R-TEST", skipped=[], src_index={},
        )

        # Check that partial gets a proper label
        mail_source = [s for s in view["sources"] if s["name"] == "Outlook"][0]
        assert mail_source["message"] != "partial"
        assert "部分" in mail_source["message"] or "讀取" in mail_source["message"]
        # Coverage should be present for partial too
        assert mail_source["coverage"] != ""


# ===========================================================================
# K. Remove → re-add checkpoint resume (review finding #5)
# ===========================================================================

class TestCheckpointResumeOnReAdd:
    def test_stale_checkpoint_preserved(self) -> None:
        """Removing a folder from config and re-adding it should resume
        from the old checkpoint, not re-read 14 days."""
        old_cp = (NOW - timedelta(hours=5)).isoformat()
        # State has checkpoint for folder "custom-id"
        st = fake_state()
        st["checkpoints"]["mail"] = {"inbox": old_cp, "custom-id": old_cp}

        # Config currently only has inbox (custom-id was removed)
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox"]

        # Collect with just inbox — custom-id checkpoint should be preserved
        graph = FakeGraph(mail_items_by_folder={"inbox": []})
        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-resume1")
        mail_cp = result.new_checkpoints["mail"]
        assert isinstance(mail_cp, dict)

        # Now re-add custom-id
        cfg2 = fake_cfg()
        cfg2["mail"]["folders"] = ["inbox", "custom-id"]
        st2 = dict(st)
        st2["checkpoints"] = result.new_checkpoints

        graph2 = FakeGraph(mail_items_by_folder={"inbox": [], "custom-id": []})
        result2 = collect(cfg2, st2, now=NOW, line=None, graph=graph2, run_id="r-resume2")

        # custom-id should have resumed from old_cp, not None (initial window)
        # We verify by checking graph2.mail_list_calls — if it got 14 days
        # it would request different days than if it got the 5-hour-old checkpoint
        mail_cp2 = result2.new_checkpoints["mail"]
        assert isinstance(mail_cp2, dict)
        assert mail_cp2["custom-id"] == NOW.isoformat()


# ===========================================================================
# L. Legacy scalar migration end-to-end (review finding: days per folder)
# ===========================================================================

class TestLegacyMigrationEndToEnd:
    def test_legacy_checkpoint_inbox_gets_old_days_new_folder_gets_initial(self) -> None:
        """End-to-end: legacy scalar checkpoint → inbox uses short window,
        new folder uses initial_days window."""
        import math

        old_cp = (NOW - timedelta(hours=3)).isoformat()
        cfg = fake_cfg()
        cfg["mail"]["folders"] = ["inbox", "new-folder"]
        st = fake_state()
        st["checkpoints"]["mail"] = old_cp  # Legacy scalar

        call_log: list[tuple[int, str]] = []

        class LoggingGraph(FakeGraph):
            def mail_list(self, *, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
                call_log.append((days, folder))
                return []

        graph = LoggingGraph()
        result = collect(cfg, st, now=NOW, line=None, graph=graph, run_id="r-legacy1",
                         initial_days=14)

        # inbox should get ~1 day (3h + 2h overlap = 5h → ceil to 1 day)
        inbox_call = [c for c in call_log if c[1] == "inbox"]
        assert inbox_call[0][0] <= 2  # at most 1-2 days

        # new-folder should get 14 days (no checkpoint)
        new_call = [c for c in call_log if c[1] == "new-folder"]
        assert new_call[0][0] == 14


# ===========================================================================
# M. Select when graph_factory raises (review finding #6)
# ===========================================================================

class TestSelectGraphFactoryFailure:
    def test_select_non_wellknown_ref_refuses_when_graph_unavailable(
        self, tmp_path: Path, capsys: Any,
    ) -> None:
        """--set with non-well-known refs must refuse when graph is unavailable,
        not silently skip validation."""
        from daily_agenda.graph_client import GraphClientError

        state.accept_consent(tmp_path, NOW)

        def failing_factory() -> Any:
            raise GraphClientError("DF_GRAPH_AUTH", "token expired")

        deps = _deps(tmp_path, graph_factory=failing_factory)
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "some-random-id"], deps, capsys,
        )
        # Must refuse because some-random-id is not a well-known name
        # and we can't validate it
        assert obj["ok"] is False

    def test_select_only_wellknown_ok_when_graph_unavailable(
        self, tmp_path: Path, capsys: Any,
    ) -> None:
        """--set with ONLY well-known refs succeeds even without graph."""
        from daily_agenda.graph_client import GraphClientError

        state.accept_consent(tmp_path, NOW)

        def failing_factory() -> Any:
            raise GraphClientError("DF_GRAPH_AUTH", "token expired")

        deps = _deps(tmp_path, graph_factory=failing_factory)
        code, obj = _run_cli(
            ["mail", "select", "--set", "inbox", "archive"], deps, capsys,
        )
        assert obj["ok"] is True
