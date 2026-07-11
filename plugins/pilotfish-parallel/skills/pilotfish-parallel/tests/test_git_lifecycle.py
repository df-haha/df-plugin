from __future__ import annotations

import dataclasses
import copy
import fcntl
import gc
import hashlib
import json
import os
import signal
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import runner
from tests.helpers import (
    git_text,
    make_job,
    make_repo,
    manifest_dict,
    manifest_for,
    repo_lock,
    run_git,
)


def lifecycle_job(
    job_id: str,
    allowed_paths: tuple[str, ...],
    *,
    role: str = "executor",
    denied_paths: tuple[str, ...] = (),
    commands: tuple[runner.CommandSpec, ...] = (),
) -> runner.JobSpec:
    return runner.JobSpec(
        id=job_id,
        role=role,
        goal=f"exercise {job_id}",
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
        acceptance_criteria=(f"{job_id} is complete.",),
        verification_commands=commands,
        timeout_seconds=30,
    )


def lifecycle_command(
    command_id: str,
    *argv: str,
    timeout_seconds: int = 10,
    effect_scope: str = "repo-local",
) -> runner.CommandSpec:
    return runner.CommandSpec(
        id=command_id,
        argv=tuple(argv),
        timeout_seconds=timeout_seconds,
        effect_scope=effect_scope,
    )


def lifecycle_manifest(
    repo: Path,
    jobs: tuple[runner.JobSpec, ...],
    *,
    commands: tuple[runner.CommandSpec, ...] = (),
    run_id: str = "task-5",
) -> runner.Manifest:
    return dataclasses.replace(
        manifest_for(repo, run_id=run_id),
        jobs=jobs,
        integration_verification_commands=commands,
        max_parallel=max(1, min(3, len(jobs))),
    )


def manifest_for_two_writers(
    repo: Path,
    first_paths: tuple[str, ...],
    second_paths: tuple[str, ...],
) -> runner.Manifest:
    return lifecycle_manifest(
        repo,
        (
            lifecycle_job("writer-a", first_paths),
            lifecycle_job("writer-b", second_paths),
        ),
    )


class ManifestSemanticTests(unittest.TestCase):
    def test_rejects_integration_as_reserved_job_id(self) -> None:
        manifest = manifest_dict(
            jobs=(
                make_job("scout-a", "scout", ("docs",)),
                make_job("integration", "executor", ("src",)),
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="pilotfish-manifest-"
        ) as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                runner.PilotfishError, "reserved"
            ) as raised:
                runner.load_manifest(path)

        self.assertEqual(raised.exception.state, "PRECHECK_FAILED")


class PreflightTests(unittest.TestCase):
    def assert_precheck_failed(self, error: runner.PilotfishError) -> None:
        self.assertEqual(error.state, "PRECHECK_FAILED")

    def test_accepts_clean_branch_at_exact_base(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo)

            baseline = runner.preflight_repo(manifest)

            self.assertEqual(baseline.root, repo.resolve())
            self.assertEqual(baseline.branch, "main")
            self.assertEqual(baseline.base_sha, manifest.base_sha)
            self.assertEqual(
                baseline.base_tree,
                git_text(repo, "rev-parse", "HEAD^{tree}"),
            )
            self.assertEqual(baseline.index_tree, baseline.base_tree)
            self.assertEqual(baseline.git_dir, (repo / ".git").resolve())
            self.assertEqual(baseline.common_dir, (repo / ".git").resolve())
            common_dir_stat = baseline.common_dir.stat()
            self.assertEqual(
                baseline.common_dir_device_inode,
                (common_dir_stat.st_dev, common_dir_stat.st_ino),
            )

    def test_rejects_non_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pilotfish-not-git-") as directory:
            manifest = manifest_for(Path(directory), base_sha="a" * 40)

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.preflight_repo(manifest)

            self.assert_precheck_failed(raised.exception)

    def test_rejects_nested_repo_root(self) -> None:
        with make_repo() as repo:
            nested = repo / "nested"
            nested.mkdir()

            with self.assertRaisesRegex(runner.PilotfishError, "top-level") as raised:
                runner.preflight_repo(manifest_for(nested))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_detached_head(self) -> None:
        with make_repo() as repo:
            run_git(repo, "checkout", "--detach")

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_unborn_branch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pilotfish-unborn-") as directory:
            repo = Path(directory).resolve()
            run_git(repo, "init", "-b", "main")
            run_git(repo, "config", "user.name", "Pilotfish Test")
            run_git(repo, "config", "user.email", "pilotfish@example.invalid")

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.preflight_repo(manifest_for(repo, base_sha="a" * 40))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_wrong_base_branch(self) -> None:
        with make_repo() as repo:
            with self.assertRaisesRegex(runner.PilotfishError, "branch mismatch") as raised:
                runner.preflight_repo(manifest_for(repo, base_branch="other"))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_wrong_base_sha(self) -> None:
        with make_repo() as repo:
            with self.assertRaisesRegex(runner.PilotfishError, "base SHA mismatch") as raised:
                runner.preflight_repo(manifest_for(repo, base_sha="a" * 40))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_untracked_change(self) -> None:
        with make_repo() as repo:
            (repo / "untracked.txt").write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(runner.PilotfishError, "clean") as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_tracked_worktree_change(self) -> None:
        with make_repo() as repo:
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(runner.PilotfishError, "clean") as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_staged_change(self) -> None:
        with make_repo() as repo:
            (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
            run_git(repo, "add", "tracked.txt")

            with self.assertRaisesRegex(runner.PilotfishError, "clean") as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_active_git_operation_sentinels(self) -> None:
        markers = (
            ("MERGE_HEAD", False),
            ("CHERRY_PICK_HEAD", False),
            ("REVERT_HEAD", False),
            ("BISECT_LOG", False),
            ("rebase-apply", True),
            ("rebase-merge", True),
        )
        for marker_name, is_directory in markers:
            with self.subTest(marker=marker_name), make_repo() as repo:
                marker = repo / ".git" / marker_name
                if is_directory:
                    marker.mkdir()
                else:
                    marker.write_text(
                        git_text(repo, "rev-parse", "HEAD") + "\n",
                        encoding="utf-8",
                    )

                with self.assertRaisesRegex(
                    runner.PilotfishError, "active Git operation"
                ) as raised:
                    runner.preflight_repo(manifest_for(repo))

                self.assert_precheck_failed(raised.exception)

    def test_rejects_sparse_checkout(self) -> None:
        with make_repo() as repo:
            run_git(repo, "config", "core.sparseCheckout", "true")

            with self.assertRaisesRegex(runner.PilotfishError, "sparse checkout") as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_unsafe_local_git_config(self) -> None:
        settings = (
            ("core.fsmonitor", "true"),
            ("diff.external", "/bin/false"),
            ("core.attributesFile", "/tmp/pilotfish-attributes"),
        )
        for key, value in settings:
            with self.subTest(key=key), make_repo() as repo:
                run_git(repo, "config", key, value)

                with self.assertRaisesRegex(
                    runner.PilotfishError, "unsafe local Git config"
                ) as raised:
                    runner.preflight_repo(manifest_for(repo))

                self.assert_precheck_failed(raised.exception)

    def test_rejects_custom_filters_and_merge_drivers(self) -> None:
        keys = (
            "filter.pilotfish.clean",
            "filter.pilotfish.smudge",
            "filter.pilotfish.process",
            "merge.pilotfish.driver",
        )
        for key in keys:
            with self.subTest(key=key), make_repo() as repo:
                run_git(repo, "config", key, "/bin/false")

                with self.assertRaisesRegex(
                    runner.PilotfishError, "filters/merge drivers"
                ) as raised:
                    runner.preflight_repo(manifest_for(repo))

                self.assert_precheck_failed(raised.exception)

    def test_rejects_filter_before_worktree_probe_can_execute(self) -> None:
        with make_repo() as repo:
            (repo / ".gitattributes").write_text(
                "tracked.txt filter=pilotfish\n",
                encoding="utf-8",
            )
            run_git(repo, "add", ".gitattributes")
            run_git(repo, "commit", "-m", "add filter attributes fixture")
            marker = repo / ".git" / "filter-executed"
            probe = repo / ".git" / "filter-probe.sh"
            probe.write_text(
                "#!/bin/sh\n"
                f": > {shlex.quote(str(marker))}\n"
                "printf 'baseline\\n'\n",
                encoding="utf-8",
            )
            run_git(
                repo,
                "config",
                "filter.pilotfish.clean",
                f"sh {shlex.quote(str(probe))}",
            )
            (repo / "tracked.txt").write_text(
                "dirty-but-filtered\n",
                encoding="utf-8",
            )

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)
            self.assertFalse(
                marker.exists(),
                "unsafe clean filter executed before preflight rejected it",
            )
            self.assertRegex(str(raised.exception), "filters/merge drivers")

    def test_rejects_local_signing_config(self) -> None:
        settings = (
            ("commit.gpgSign", "true"),
            ("tag.gpgSign", "true"),
            ("tag.forceSignAnnotated", "true"),
            ("user.signingKey", "fixture-key"),
            ("gpg.format", "ssh"),
            ("gpg.program", "/bin/false"),
            ("gpg.openpgp.program", "/bin/false"),
            ("gpg.x509.program", "/bin/false"),
            ("gpg.ssh.program", "/bin/false"),
            ("gpg.ssh.defaultKeyCommand", "/bin/false"),
        )
        for key, value in settings:
            with self.subTest(key=key), make_repo() as repo:
                run_git(repo, "config", key, value)

                with self.assertRaisesRegex(
                    runner.PilotfishError, "signing Git config"
                ) as raised:
                    runner.preflight_repo(manifest_for(repo))

                self.assert_precheck_failed(raised.exception)

    def test_ignores_global_git_config(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-home-"
        ) as home:
            (Path(home) / ".gitconfig").write_text(
                "[core]\n\tattributesFile = /tmp/global-attributes\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"HOME": home}):
                baseline = runner.preflight_repo(manifest_for(repo))

            self.assertEqual(baseline.root, repo)

    def test_git_environment_rejects_unsupported_override(self) -> None:
        with self.assertRaisesRegex(
            runner.PilotfishError, "unsupported Git environment keys"
        ) as raised:
            runner.git_environment({"GIT_DIR": "/tmp/not-allowed"})

        self.assert_precheck_failed(raised.exception)

    def test_rejects_gitlink_entries(self) -> None:
        with make_repo() as repo, make_repo() as source:
            run_git(
                repo,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(source),
                "vendor/module",
            )
            run_git(repo, "commit", "-m", "add submodule fixture")

            with self.assertRaisesRegex(
                runner.PilotfishError, "submodule/gitlink"
            ) as raised:
                runner.preflight_repo(manifest_for(repo))

            self.assert_precheck_failed(raised.exception)

    def test_rejects_index_flags_hiding_dirty_worktree(self) -> None:
        cases = (
            ("assume-unchanged", "--assume-unchanged"),
            ("skip-worktree", "--skip-worktree"),
        )
        for label, flag in cases:
            with self.subTest(flag=label), make_repo() as repo:
                run_git(repo, "update-index", flag, "tracked.txt")
                (repo / "tracked.txt").write_text(
                    f"hidden by {label}\n",
                    encoding="utf-8",
                )
                self.assertEqual(
                    run_git(
                        repo,
                        "status",
                        "--porcelain=v1",
                        "-z",
                        "--untracked-files=all",
                    ).stdout,
                    b"",
                )

                with self.assertRaisesRegex(
                    runner.PilotfishError, "index flags"
                ) as raised:
                    runner.preflight_repo(manifest_for(repo))

                self.assert_precheck_failed(raised.exception)

    def test_lock_rejects_replaced_common_directory_before_second_lock(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            common_dir_stat = baseline.common_dir.stat()
            expected_identity = (
                common_dir_stat.st_dev,
                common_dir_stat.st_ino,
            )

            def lock_for(run_id: str) -> runner.RepoLock:
                try:
                    return runner.RepoLock(
                        baseline.common_dir,
                        run_id,
                        expected_identity,
                    )
                except TypeError:
                    return runner.RepoLock(baseline.common_dir, run_id)

            displaced_common_dir = repo / ".git-displaced"
            with lock_for("one"):
                baseline.common_dir.rename(displaced_common_dir)
                baseline.common_dir.mkdir()

                with self.assertRaisesRegex(
                    runner.PilotfishError, "common directory identity"
                ):
                    with lock_for("two"):
                        self.fail(
                            "replacement common directory acquired a second lock"
                        )

            self.assertFalse(
                (baseline.common_dir / "pilotfish-parallel.lock").exists()
            )
            self.assertEqual(
                getattr(baseline, "common_dir_device_inode", None),
                expected_identity,
            )

    def test_lock_rejects_second_active_run(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo, run_id="one"))

            with repo_lock(baseline, "one"):
                with self.assertRaisesRegex(runner.PilotfishError, "active run") as raised:
                    with repo_lock(baseline, "two"):
                        self.fail("second lock must not be acquired")

            self.assert_precheck_failed(raised.exception)

    def test_lock_records_and_checks_ownership(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            lock = repo_lock(baseline, "run-1")

            with lock:
                self.assertEqual(lock.path.read_text(encoding="utf-8"), "run-1\n")
                lock.assert_owned()

            self.assertEqual(lock.path.read_text(encoding="utf-8"), "")

    def test_lock_rejects_symlink_path(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            target = baseline.common_dir / "lock-target"
            target.write_text("target\n", encoding="utf-8")
            lock_path = baseline.common_dir / "pilotfish-parallel.lock"
            lock_path.symlink_to(target)

            with self.assertRaisesRegex(runner.PilotfishError, "safely open") as raised:
                with repo_lock(baseline, "run-1"):
                    self.fail("symlink lock must not be acquired")

            self.assert_precheck_failed(raised.exception)

    def test_lock_rejects_group_or_world_permissions(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            lock_path = baseline.common_dir / "pilotfish-parallel.lock"
            lock_path.write_text("", encoding="utf-8")
            lock_path.chmod(0o644)

            with self.assertRaisesRegex(runner.PilotfishError, "owner-only") as raised:
                with repo_lock(baseline, "run-1"):
                    self.fail("insecure lock must not be acquired")

            self.assert_precheck_failed(raised.exception)

    def test_lock_detects_ownership_content_drift(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))

            with repo_lock(baseline, "run-1") as lock:
                lock.path.write_text("other\n", encoding="utf-8")

                with self.assertRaisesRegex(runner.PilotfishError, "content drifted") as raised:
                    lock.assert_owned()

                self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_lock_detects_inode_replacement(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))

            with repo_lock(baseline, "run-1") as lock:
                replacement = baseline.common_dir / "replacement.lock"
                replacement.write_text("run-1\n", encoding="utf-8")
                replacement.chmod(0o600)
                replacement.replace(lock.path)

                with self.assertRaisesRegex(runner.PilotfishError, "inode") as raised:
                    lock.assert_owned()

                self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")


class TransactionalApplyTests(unittest.TestCase):
    def test_git_helpers_use_caller_selected_error_state(self) -> None:
        with make_repo() as repo:
            with self.assertRaises(runner.PilotfishError) as git_raised:
                runner.git(
                    repo,
                    "pilotfish-command-that-does-not-exist",
                    error_state="INTEGRATION_FAILED",
                )
            with self.assertRaises(runner.PilotfishError) as text_raised:
                runner.git_text(
                    repo,
                    "pilotfish-command-that-does-not-exist",
                    error_state="SOURCE_DRIFTED",
                )

        self.assertEqual(git_raised.exception.state, "INTEGRATION_FAILED")
        self.assertEqual(text_raised.exception.state, "SOURCE_DRIFTED")

    def test_binary_full_index_patch_proves_tree_without_touching_index(
        self,
    ) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-6-patch-proof")
            baseline = runner.preflight_repo(manifest)
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)
            runner.create_worktrees(
                manifest,
                baseline,
                runner.load_roles(),
                layout,
            )
            integration = layout.integration_worktree
            (integration / "tracked.txt").write_text(
                "integrated\n", encoding="utf-8"
            )
            (integration / "binary.bin").write_bytes(
                b"\x00\xffpilotfish\x80\x00"
            )
            run_git(integration, "add", "-A")
            run_git(integration, "commit", "-m", "integration fixture")
            expected_tree = git_text(integration, "rev-parse", "HEAD^{tree}")
            original_index_tree = runner.git_text(repo, "write-tree")
            patch_path = layout.artifacts / "combined.patch"

            patch_artifact = runner.write_binary_patch(
                baseline,
                integration,
                patch_path,
                runner.load_policy(),
            )
            patch_bytes = patch_path.read_bytes()
            patch_path.write_bytes(b"tampered disk evidence\n")
            runner.prove_patch_tree(
                repo,
                baseline,
                patch_artifact,
                layout.root / "preflight.index",
                expected_tree,
            )

            self.assertEqual(
                patch_artifact.sha256,
                hashlib.sha256(patch_bytes).hexdigest(),
            )
            self.assertEqual(patch_artifact.path, patch_path)
            self.assertEqual(patch_artifact.bytes, patch_bytes)
            self.assertNotEqual(patch_path.read_bytes(), patch_artifact.bytes)
            self.assertIn(b"GIT binary patch", patch_bytes)
            self.assertRegex(
                patch_bytes,
                rb"index [0-9a-f]{40}\.\.[0-9a-f]{40}",
            )
            self.assertEqual(
                runner.git_text(repo, "write-tree"), original_index_tree
            )
            self.assertEqual(
                runner.git_text(
                    repo,
                    "write-tree",
                    env=runner.git_env_with_index(
                        layout.root / "preflight.index"
                    ),
                ),
                expected_tree,
            )

    def test_rollback_bundle_restores_bytes_modes_absence_and_parents(
        self,
    ) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-rollback-"
        ) as directory:
            tracked = repo / "tracked.txt"
            deleted = repo / "deleted.bin"
            executable = repo / "mode.sh"
            deleted.write_bytes(b"\x00original-deleted\xff")
            executable.write_bytes(b"#!/bin/sh\nexit 0\n")
            executable.chmod(0o640)
            original_tracked = tracked.read_bytes()
            original_deleted = deleted.read_bytes()
            original_executable = executable.read_bytes()
            original_mode = executable.lstat().st_mode & 0o7777
            bundle = Path(directory) / "bundle"
            changed_paths = (
                "tracked.txt",
                "deleted.bin",
                "mode.sh",
                "new/deep/payload.bin",
            )

            rollback_bundle = runner.create_rollback_bundle(
                repo, changed_paths, bundle
            )
            tracked.write_bytes(b"\x00changed\x80")
            deleted.unlink()
            executable.write_bytes(b"changed mode and bytes\n")
            executable.chmod(0o755)
            created = repo / "new" / "deep" / "payload.bin"
            created.parent.mkdir(parents=True)
            created.write_bytes(b"\x00new binary\xff")

            runner.restore_rollback_bundle(repo, rollback_bundle)
            runner.verify_rollback_bundle(repo, rollback_bundle)

            self.assertEqual(rollback_bundle.manifest_path.parent, bundle)
            self.assertEqual(
                rollback_bundle.manifest_sha256,
                hashlib.sha256(
                    rollback_bundle.manifest_path.read_bytes()
                ).hexdigest(),
            )
            with self.assertRaises(dataclasses.FrozenInstanceError):
                rollback_bundle.manifest_sha256 = "0" * 64
            self.assertEqual(tracked.read_bytes(), original_tracked)
            self.assertEqual(deleted.read_bytes(), original_deleted)
            self.assertEqual(executable.read_bytes(), original_executable)
            self.assertEqual(
                executable.lstat().st_mode & 0o7777, original_mode
            )
            self.assertFalse(created.exists())
            self.assertFalse((repo / "new").exists())

    def test_rollback_restore_rejects_tampered_payload(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-rollback-"
        ) as directory:
            bundle = Path(directory) / "bundle"
            rollback_bundle = runner.create_rollback_bundle(
                repo, ("tracked.txt",), bundle
            )
            manifest = json.loads(
                rollback_bundle.manifest_path.read_text(encoding="utf-8")
            )
            payload = bundle / manifest["records"][0]["payload"]
            payload.write_bytes(b"tampered")
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(
                runner.PilotfishError, "payload hash mismatch"
            ) as raised:
                runner.restore_rollback_bundle(repo, rollback_bundle)

            self.assertEqual(raised.exception.state, "ROLLBACK_FAILED")
            self.assertEqual(
                (repo / "tracked.txt").read_text(encoding="utf-8"),
                "changed\n",
            )

    def test_rollback_verifier_detects_restored_file_tamper(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-rollback-"
        ) as directory:
            rollback_bundle = runner.create_rollback_bundle(
                repo, ("tracked.txt",), Path(directory) / "bundle"
            )
            (repo / "tracked.txt").write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(
                runner.PilotfishError, "restored byte hash mismatch"
            ) as raised:
                runner.verify_rollback_bundle(repo, rollback_bundle)

            self.assertEqual(raised.exception.state, "ROLLBACK_FAILED")

    def test_rollback_bundle_rejects_non_regular_existing_path(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-rollback-"
        ) as directory:
            (repo / "link.txt").symlink_to("tracked.txt")

            with self.assertRaisesRegex(
                runner.PilotfishError, "unsupported rollback path"
            ) as raised:
                runner.create_rollback_bundle(
                    repo, ("link.txt",), Path(directory) / "bundle"
                )

            self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")

    def test_rollback_restore_refuses_unexpected_non_regular_path(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-rollback-"
        ) as directory:
            rollback_bundle = runner.create_rollback_bundle(
                repo, ("new.txt",), Path(directory) / "bundle"
            )
            (repo / "new.txt").symlink_to("tracked.txt")

            with self.assertRaisesRegex(
                runner.PilotfishError, "unexpected path"
            ) as raised:
                runner.restore_rollback_bundle(repo, rollback_bundle)

            self.assertEqual(raised.exception.state, "ROLLBACK_FAILED")


class TransactionPrimitiveTests(unittest.TestCase):
    def test_lock_assert_owned_detects_released_flock(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))

            with repo_lock(baseline, "task-6-flock") as lock:
                fcntl.flock(lock.handle.fileno(), fcntl.LOCK_UN)

                with self.assertRaisesRegex(
                    runner.PilotfishError, "lock.*held"
                ) as raised:
                    lock.assert_owned()

                self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_assert_source_unchanged_rejects_branch_drift(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            run_git(repo, "checkout", "-b", "other")

            with repo_lock(baseline, "task-6-branch") as lock:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "branch drifted"
                ) as raised:
                    runner.assert_source_unchanged(baseline, lock)

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_assert_source_unchanged_rejects_head_drift(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            (repo / "tracked.txt").write_text("new head\n", encoding="utf-8")
            run_git(repo, "add", "tracked.txt")
            run_git(repo, "commit", "-m", "move source head")

            with repo_lock(baseline, "task-6-head") as lock:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "HEAD drifted"
                ) as raised:
                    runner.assert_source_unchanged(baseline, lock)

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_assert_source_unchanged_rejects_worktree_drift(self) -> None:
        with make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            (repo / "untracked.txt").write_text("drift\n", encoding="utf-8")

            with repo_lock(baseline, "task-6-worktree") as lock:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "working tree drifted"
                ) as raised:
                    runner.assert_source_unchanged(baseline, lock)

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_assert_source_unchanged_rejects_index_and_git_operation(
        self,
    ) -> None:
        with self.subTest(gate="index"), make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            run_git(repo, "update-index", "--chmod=+x", "tracked.txt")
            with repo_lock(baseline, "task-6-index") as lock:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "index drifted"
                ) as raised:
                    runner.assert_source_unchanged(baseline, lock)
            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

        with self.subTest(gate="git-operation"), make_repo() as repo:
            baseline = runner.preflight_repo(manifest_for(repo))
            (baseline.git_dir / "MERGE_HEAD").write_text(
                baseline.base_sha + "\n", encoding="ascii"
            )
            with repo_lock(baseline, "task-6-operation") as lock:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "active Git operation"
                ) as raised:
                    runner.assert_source_unchanged(baseline, lock)
            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")

    def test_working_tree_hash_uses_temporary_index_only(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-index-"
        ) as directory:
            baseline = runner.preflight_repo(manifest_for(repo))
            original_index_tree = runner.git_text(repo, "write-tree")
            (repo / "tracked.txt").write_bytes(b"\x00changed\xff")
            (repo / "new.txt").write_text("new\n", encoding="utf-8")
            index_path = Path(directory) / "working-tree.index"

            observed_tree = runner.working_tree_hash(
                repo, baseline, index_path
            )

            self.assertNotEqual(observed_tree, baseline.base_tree)
            self.assertEqual(
                runner.git_text(repo, "write-tree"), original_index_tree
            )
            self.assertEqual(
                runner.git(repo, "show", f"{observed_tree}:tracked.txt").stdout,
                b"\x00changed\xff",
            )
            self.assertEqual(
                runner.git(repo, "show", f"{observed_tree}:new.txt").stdout,
                b"new\n",
            )

    def test_deferred_signals_rejects_controller_recorded_signal(self) -> None:
        with runner.CancellationController() as cancellation:
            cancellation._record(signal.SIGINT, None)
            deferred = runner.DeferredSignals(cancellation)

            with self.assertRaisesRegex(
                runner.PilotfishError, "before final apply"
            ) as raised:
                with deferred:
                    self.fail("controller-recorded signal must stop entry")

        self.assertEqual(raised.exception.state, "CANCELLED")
        self.assertIn(signal.SIGINT, deferred.received)

    def test_deferred_signals_consumes_kernel_pending_signal(self) -> None:
        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)

            with deferred:
                signal.raise_signal(signal.SIGTERM)
                self.assertIn(signal.SIGTERM, deferred.pending())

            self.assertNotIn(signal.SIGTERM, deferred.pending())
            self.assertEqual(cancellation.received, set())

        self.assertIn(signal.SIGTERM, deferred.received)


class TransactionExecutionTests(unittest.TestCase):
    def prepare_integration(self, run_id: str) -> SimpleNamespace:
        repository_context = make_repo()
        repo = repository_context.__enter__()
        self.addCleanup(repository_context.__exit__, None, None, None)
        (repo / "deleted.txt").write_bytes(b"delete me\n")
        tool = repo / "tool.sh"
        tool.write_bytes(b"#!/bin/sh\nexit 0\n")
        tool.chmod(0o640)
        victim = repo / "victim.txt"
        victim.write_bytes(b"safe victim\n")
        run_git(repo, "add", "deleted.txt", "tool.sh", "victim.txt")
        run_git(repo, "commit", "-m", "expand transaction fixture")
        manifest = manifest_for(repo, run_id=run_id)
        baseline = runner.preflight_repo(manifest)
        layout = runner.create_layout(manifest)
        self.addCleanup(shutil.rmtree, layout.root, True)
        runner.create_worktrees(
            manifest,
            baseline,
            runner.load_roles(),
            layout,
        )
        integration = layout.integration_worktree
        (integration / "tracked.txt").write_text(
            "verified integration\n", encoding="utf-8"
        )
        (integration / "deleted.txt").unlink()
        (integration / "tool.sh").chmod(0o755)
        payload = integration / "new" / "deep" / "payload.bin"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"\x00verified\xffpayload\x80")
        run_git(integration, "add", "-A")
        run_git(integration, "commit", "-m", "verified integration")
        return SimpleNamespace(
            repo=repo,
            baseline=baseline,
            layout=layout,
            integration=integration,
            expected_head=git_text(integration, "rev-parse", "HEAD"),
            expected_tree=git_text(integration, "rev-parse", "HEAD^{tree}"),
            original_index=runner.git_text(repo, "write-tree"),
            original_tool_mode=(repo / "tool.sh").lstat().st_mode & 0o7777,
            original_tracked_mode=(repo / "tracked.txt").lstat().st_mode
            & 0o7777,
        )

    def apply(
        self, fixture: SimpleNamespace, signals: object
    ) -> dict[str, object]:
        return runner.apply_integration_transactionally(
            fixture.baseline,
            fixture.layout,
            fixture.integration,
            self.lock,
            signals,
            expected_integration_head=fixture.expected_head,
            expected_integration_tree=fixture.expected_tree,
        )

    def assert_source_restored(self, fixture: SimpleNamespace) -> None:
        self.assertEqual(
            runner.git(
                fixture.repo,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ).stdout,
            b"",
        )
        self.assertEqual(
            (fixture.repo / "tracked.txt").read_text(encoding="utf-8"),
            "baseline\n",
        )
        self.assertEqual(
            (fixture.repo / "deleted.txt").read_bytes(), b"delete me\n"
        )
        self.assertEqual(
            (fixture.repo / "tool.sh").lstat().st_mode & 0o7777,
            fixture.original_tool_mode,
        )
        self.assertEqual(
            (fixture.repo / "tracked.txt").lstat().st_mode & 0o7777,
            fixture.original_tracked_mode,
        )
        self.assertEqual(
            (fixture.repo / "victim.txt").read_bytes(), b"safe victim\n"
        )
        self.assertFalse((fixture.repo / "new").exists())
        self.assertEqual(
            runner.git_text(fixture.repo, "write-tree"),
            fixture.original_index,
        )

    def run_signal_fault(
        self,
        fixture: SimpleNamespace,
        injected_signal: signal.Signals,
        install_fault: object,
    ) -> tuple[runner.PilotfishError, runner.DeferredSignals]:
        trigger = threading.Event()
        sent = threading.Event()
        sender_errors: list[BaseException] = []
        main_thread_id = threading.get_ident()

        def sender() -> None:
            try:
                if not trigger.wait(timeout=5):
                    raise AssertionError("signal fault trigger was not reached")
                signal.pthread_kill(main_thread_id, injected_signal)
            except BaseException as exc:
                sender_errors.append(exc)
            finally:
                sent.set()

        thread = threading.Thread(target=sender, name="transaction-signal-fault")
        thread.start()
        try:
            with runner.CancellationController() as cancellation:
                deferred = runner.DeferredSignals(cancellation)
                self.lock = repo_lock(fixture.baseline, "task-6-transaction")
                with self.lock, self.assertRaises(
                    runner.PilotfishError
                ) as raised:
                    with deferred:
                        with install_fault(trigger, sent, deferred):
                            self.apply(fixture, deferred)
        finally:
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sender_errors, [])
        return raised.exception, deferred

    def test_applies_verified_tree_with_pinned_head_and_unchanged_index(
        self,
    ) -> None:
        fixture = self.prepare_integration("task-6-transaction-success")
        real_write_patch = runner.write_binary_patch

        def advance_after_gate(
            baseline: runner.RepoBaseline,
            integration: Path,
            path: Path,
            policy: runner.InvocationPolicy,
            *,
            end_ref: str,
        ) -> runner.PatchArtifact:
            (integration / "tracked.txt").write_text(
                "unverified later head\n", encoding="utf-8"
            )
            run_git(integration, "add", "tracked.txt")
            run_git(integration, "commit", "-m", "unverified later head")
            return real_write_patch(
                baseline,
                integration,
                path,
                policy,
                end_ref=end_ref,
            )

        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)
            self.lock = repo_lock(fixture.baseline, "task-6-success")
            with self.lock, deferred, patch.object(
                runner, "write_binary_patch", side_effect=advance_after_gate
            ):
                result = self.apply(fixture, deferred)

        self.assertEqual(result["applied_tree"], fixture.expected_tree)
        rollback_bundle = result["_rollback_bundle_internal"]
        self.assertIsInstance(rollback_bundle, runner.RollbackBundle)
        self.assertEqual(
            result["rollback_manifest"],
            str(rollback_bundle.manifest_path),
        )
        self.assertEqual(
            result["rollback_manifest_sha256"],
            rollback_bundle.manifest_sha256,
        )
        with self.assertRaises(TypeError):
            json.dumps(result)
        self.assertEqual(
            runner.git_text(fixture.repo, "write-tree"),
            fixture.original_index,
        )
        self.assertEqual(
            runner.working_tree_hash(
                fixture.repo,
                fixture.baseline,
                fixture.layout.root / "test-success.index",
            ),
            fixture.expected_tree,
        )
        self.assertEqual(
            (fixture.repo / "tracked.txt").read_text(encoding="utf-8"),
            "verified integration\n",
        )
        self.assertNotEqual(
            git_text(fixture.integration, "rev-parse", "HEAD"),
            fixture.expected_head,
        )

    def test_source_drift_before_real_apply_is_never_overwritten(self) -> None:
        for phase in ("initial", "after-bundle"):
            with self.subTest(phase=phase):
                fixture = self.prepare_integration(
                    f"task-6-source-{phase}"
                )
                drift = fixture.repo / "external-drift.txt"
                real_bundle = runner.create_rollback_bundle

                def create_then_drift(*args: object, **kwargs: object) -> Path:
                    manifest_path = real_bundle(*args, **kwargs)
                    drift.write_text("external\n", encoding="utf-8")
                    return manifest_path

                if phase == "initial":
                    drift.write_text("external\n", encoding="utf-8")
                    bundle_patch = patch.object(
                        runner, "create_rollback_bundle", wraps=real_bundle
                    )
                else:
                    bundle_patch = patch.object(
                        runner,
                        "create_rollback_bundle",
                        side_effect=create_then_drift,
                    )
                with runner.CancellationController() as cancellation:
                    deferred = runner.DeferredSignals(cancellation)
                    self.lock = repo_lock(
                        fixture.baseline, f"task-6-source-{phase}"
                    )
                    with self.lock, self.assertRaises(
                        runner.PilotfishError
                    ) as raised, deferred, bundle_patch, patch.object(
                        runner, "restore_rollback_bundle"
                    ) as restore:
                        self.apply(fixture, deferred)

                self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")
                self.assertEqual(drift.read_text(encoding="utf-8"), "external\n")
                restore.assert_not_called()
                self.assertEqual(
                    (fixture.repo / "tracked.txt").read_text(encoding="utf-8"),
                    "baseline\n",
                )

    def test_integration_head_tree_or_clean_drift_refuses_before_source_write(
        self,
    ) -> None:
        for gate in ("head", "tree", "clean"):
            with self.subTest(gate=gate):
                fixture = self.prepare_integration(
                    f"task-6-integration-{gate}"
                )
                if gate == "head":
                    fixture.expected_head = "0" * 40
                elif gate == "tree":
                    fixture.expected_tree = "0" * 40
                else:
                    (fixture.integration / "unverified.txt").write_text(
                        "dirty\n", encoding="utf-8"
                    )
                with runner.CancellationController() as cancellation:
                    deferred = runner.DeferredSignals(cancellation)
                    self.lock = repo_lock(
                        fixture.baseline, f"task-6-integration-{gate}"
                    )
                    with self.lock, self.assertRaises(
                        runner.PilotfishError
                    ) as raised, deferred, patch.object(
                        runner, "write_binary_patch"
                    ) as write_patch:
                        self.apply(fixture, deferred)

                self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
                write_patch.assert_not_called()
                self.assert_source_restored(fixture)

    def test_postapply_tree_mismatch_restores_exact_source(self) -> None:
        fixture = self.prepare_integration("task-6-postapply-mismatch")
        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)
            self.lock = repo_lock(fixture.baseline, "task-6-mismatch")
            with self.lock, self.assertRaisesRegex(
                runner.PilotfishError, "exact rollback restored"
            ) as raised, deferred, patch.object(
                runner, "working_tree_hash", return_value="0" * 40
            ):
                self.apply(fixture, deferred)

        self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
        self.assert_source_restored(fixture)

    def test_tampered_rollback_manifest_mode_cannot_self_validate(self) -> None:
        fixture = self.prepare_integration("task-6-rollback-mode-tamper")
        real_bundle = runner.create_rollback_bundle
        captured: list[object] = []

        def capture_bundle(*args: object, **kwargs: object) -> object:
            bundle = real_bundle(*args, **kwargs)
            captured.append(bundle)
            return bundle

        def tamper_manifest_then_force_mismatch(
            *_args: object, **_kwargs: object
        ) -> str:
            self.assertEqual(len(captured), 1)
            bundle = captured[0]
            manifest_path = getattr(bundle, "manifest_path", bundle)
            self.assertIsInstance(manifest_path, Path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            tracked = next(
                record
                for record in manifest["records"]
                if record["path"] == "tracked.txt"
            )
            tracked["mode"] = fixture.original_tracked_mode ^ 0o020
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return "0" * 40

        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)
            self.lock = repo_lock(fixture.baseline, "task-6-mode-tamper")
            with self.lock, deferred, self.assertRaises(
                runner.PilotfishError
            ) as raised, patch.object(
                runner,
                "create_rollback_bundle",
                side_effect=capture_bundle,
            ), patch.object(
                runner,
                "working_tree_hash",
                side_effect=tamper_manifest_then_force_mismatch,
            ):
                self.apply(fixture, deferred)

        self.assert_source_restored(fixture)
        self.assertEqual(raised.exception.state, "ROLLBACK_FAILED")
        self.assertRegex(str(raised.exception), "rollback|artifact|integrity")

    def test_combined_patch_path_swap_never_changes_unverified_victim(
        self,
    ) -> None:
        fixture = self.prepare_integration("task-6-combined-patch-swap")
        old = b"safe victim\n"
        new = b"tampered victim\n"
        old_oid = runner.git(
            fixture.repo, "hash-object", "--stdin", input_bytes=old
        ).stdout.decode("ascii").strip()
        new_oid = runner.git(
            fixture.repo, "hash-object", "--stdin", input_bytes=new
        ).stdout.decode("ascii").strip()
        malicious_patch = (
            "diff --git a/victim.txt b/victim.txt\n"
            f"index {old_oid}..{new_oid} 100644\n"
            "--- a/victim.txt\n"
            "+++ b/victim.txt\n"
            "@@ -1 +1 @@\n"
            "-safe victim\n"
            "+tampered victim\n"
        ).encode("ascii")
        # Prove the replacement is a syntactically and semantically applicable
        # patch, rather than merely corrupting the evidence file.
        runner.git(
            fixture.repo,
            "apply",
            "--check",
            "--binary",
            "-",
            input_bytes=malicious_patch,
            error_state="INTEGRATION_FAILED",
        )
        real_bundle = runner.create_rollback_bundle

        def replace_patch_after_bundle(
            *args: object, **kwargs: object
        ) -> object:
            bundle = real_bundle(*args, **kwargs)
            patch_path = fixture.layout.artifacts / "combined.patch"
            patch_path.write_bytes(patch_path.read_bytes() + malicious_patch)
            return bundle

        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)
            self.lock = repo_lock(fixture.baseline, "task-6-patch-swap")
            with self.lock, deferred, self.assertRaises(
                runner.PilotfishError
            ) as raised, patch.object(
                runner,
                "create_rollback_bundle",
                side_effect=replace_patch_after_bundle,
            ):
                self.apply(fixture, deferred)

        self.assertIn(
            raised.exception.state, {"INTEGRATION_FAILED", "ROLLBACK_FAILED"}
        )
        self.assert_source_restored(fixture)

    def test_partial_git_apply_failure_restores_exact_source(self) -> None:
        fixture = self.prepare_integration("task-6-partial-apply")
        partial_written = threading.Event()
        partial_observed = threading.Event()
        observer_errors: list[BaseException] = []
        real_git = runner.git

        def observer() -> None:
            try:
                if not partial_written.wait(timeout=5):
                    raise AssertionError("partial apply was not observed")
                if (fixture.repo / "tracked.txt").read_text(
                    encoding="utf-8"
                ) != "partial\n":
                    raise AssertionError("tracked partial write missing")
                if not (fixture.repo / "new" / "deep" / "payload.bin").exists():
                    raise AssertionError("new partial write missing")
            except BaseException as exc:
                observer_errors.append(exc)
            finally:
                partial_observed.set()

        def fail_after_partial(
            repo: Path, *args: str, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if repo == fixture.repo and args[:2] == ("apply", "--binary"):
                (fixture.repo / "tracked.txt").write_text(
                    "partial\n", encoding="utf-8"
                )
                payload = fixture.repo / "new" / "deep" / "payload.bin"
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_bytes(b"partial")
                partial_written.set()
                if not partial_observed.wait(timeout=5):
                    raise AssertionError("partial observer did not finish")
                raise runner.PilotfishError(
                    "INTEGRATION_FAILED", "synthetic partial git apply"
                )
            return real_git(repo, *args, **kwargs)

        thread = threading.Thread(target=observer, name="partial-apply-observer")
        thread.start()
        try:
            with runner.CancellationController() as cancellation:
                deferred = runner.DeferredSignals(cancellation)
                self.lock = repo_lock(fixture.baseline, "task-6-partial")
                with self.lock, self.assertRaises(
                    runner.PilotfishError
                ) as raised, deferred, patch.object(
                    runner, "git", side_effect=fail_after_partial
                ):
                    self.apply(fixture, deferred)
        finally:
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(observer_errors, [])
        self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
        self.assert_source_restored(fixture)

    def test_sigint_after_apply_before_proof_cancels_and_restores(self) -> None:
        fixture = self.prepare_integration("task-6-sigint-before-proof")
        real_hash = runner.working_tree_hash

        def install_fault(
            trigger: threading.Event,
            sent: threading.Event,
            _deferred: runner.DeferredSignals,
        ) -> object:
            def inject_before_proof(*args: object, **kwargs: object) -> str:
                trigger.set()
                if not sent.wait(timeout=5):
                    raise AssertionError("SIGINT was not sent")
                return real_hash(*args, **kwargs)

            return patch.object(
                runner, "working_tree_hash", side_effect=inject_before_proof
            )

        error, deferred = self.run_signal_fault(
            fixture, signal.SIGINT, install_fault
        )

        self.assertEqual(error.state, "CANCELLED")
        self.assertIn(signal.SIGINT, deferred.received)
        self.assert_source_restored(fixture)

    def test_sigterm_after_proof_before_pending_cancels_and_restores(self) -> None:
        fixture = self.prepare_integration("task-6-sigterm-after-proof")

        def install_fault(
            trigger: threading.Event,
            sent: threading.Event,
            deferred: runner.DeferredSignals,
        ) -> object:
            real_pending = deferred.pending

            def inject_after_proof() -> set[signal.Signals]:
                trigger.set()
                if not sent.wait(timeout=5):
                    raise AssertionError("SIGTERM was not sent")
                return real_pending()

            return patch.object(
                deferred, "pending", side_effect=inject_after_proof
            )

        error, deferred = self.run_signal_fault(
            fixture, signal.SIGTERM, install_fault
        )

        self.assertEqual(error.state, "CANCELLED")
        self.assertIn(signal.SIGTERM, deferred.received)
        self.assert_source_restored(fixture)

    def test_restore_failure_is_rollback_failed_without_later_git_mutation(
        self,
    ) -> None:
        fixture = self.prepare_integration("task-6-rollback-failure")
        restore_failed = threading.Event()
        later_git_calls: list[tuple[str, ...]] = []
        real_git = runner.git

        def fail_restore(*_args: object, **_kwargs: object) -> None:
            restore_failed.set()
            raise runner.PilotfishError(
                "ROLLBACK_FAILED", "synthetic restore failure"
            )

        def track_later_git(
            repo: Path, *args: str, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if restore_failed.is_set():
                later_git_calls.append(tuple(args))
            return real_git(repo, *args, **kwargs)

        with runner.CancellationController() as cancellation:
            deferred = runner.DeferredSignals(cancellation)
            self.lock = repo_lock(fixture.baseline, "task-6-rollback-failure")
            with self.lock, self.assertRaises(
                runner.PilotfishError
            ) as raised, deferred, patch.object(
                runner, "working_tree_hash", return_value="0" * 40
            ), patch.object(
                runner, "restore_rollback_bundle", side_effect=fail_restore
            ), patch.object(
                runner, "git", side_effect=track_later_git
            ):
                self.apply(fixture, deferred)

        self.assertEqual(raised.exception.state, "ROLLBACK_FAILED")
        self.assertEqual(later_git_calls, [])


class RunStateTests(unittest.TestCase):
    def initialize(
        self, repo: Path, run_id: str
    ) -> tuple[
        runner.Manifest,
        runner.RepoBaseline,
        runner.RunLayout,
        dict[str, object],
    ]:
        manifest = manifest_for(repo, run_id=run_id)
        baseline = runner.preflight_repo(manifest)
        layout = runner.create_layout(manifest)
        self.addCleanup(shutil.rmtree, layout.root, True)
        with repo_lock(baseline, run_id) as lock:
            state = runner.initialize_run_state(
                manifest, baseline, layout, lock
            )
        return manifest, baseline, layout, state

    def test_initial_state_has_exact_schema_owner_and_round_trips(self) -> None:
        with make_repo() as repo:
            manifest, baseline, layout, state = self.initialize(
                repo, "task-6-state-roundtrip"
            )

            runner.persist_state(layout, state)
            loaded = runner.load_and_validate_run_state(
                layout.state_path, repo
            )

            self.assertEqual(set(loaded), runner.RUN_STATE_KEYS)
            self.assertRegex(loaded["owner_nonce"], r"^[0-9a-f]{64}$")
            self.assertEqual(loaded["run_id"], manifest.run_id)
            self.assertEqual(loaded["base_sha"], baseline.base_sha)
            self.assertEqual(
                runner.layout_from_state(loaded), layout
            )
            owner = json.loads(
                (layout.root / "owner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(owner["owner_nonce"], loaded["owner_nonce"])

    def test_rejects_unknown_missing_keys_and_owner_mismatch(self) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-schema"
            )
            malformed = dict(state)
            malformed["unknown"] = True
            with self.assertRaisesRegex(runner.PilotfishError, "schema"):
                runner.validate_run_state(malformed, layout)
            malformed = dict(state)
            malformed.pop("result")
            with self.assertRaisesRegex(runner.PilotfishError, "schema"):
                runner.validate_run_state(malformed, layout)

            owner_path = layout.root / "owner.json"
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            owner["owner_nonce"] = "0" * 64
            owner_path.write_text(json.dumps(owner), encoding="utf-8")
            with self.assertRaisesRegex(runner.PilotfishError, "owner marker"):
                runner.validate_run_state(state, layout)

    def test_rejects_layout_root_traversal_and_owned_symlink(self) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-paths"
            )
            escaped = dict(state)
            escaped["layout_root"] = str(layout.root / ".." / layout.root.name)
            with self.assertRaisesRegex(runner.PilotfishError, "root"):
                runner.validate_run_state(escaped, layout)

            outside = layout.root.parent / "outside-state-file"
            escaped = dict(state)
            escaped["owned_files"] = [*state["owned_files"], str(outside)]
            with self.assertRaisesRegex(runner.PilotfishError, "escapes"):
                runner.validate_run_state(escaped, layout)

            target = layout.artifacts / "target.txt"
            target.write_text("target\n", encoding="utf-8")
            link = layout.artifacts / "linked.txt"
            link.symlink_to(target)
            linked = dict(state)
            linked["owned_files"] = [*state["owned_files"], str(link)]
            with self.assertRaisesRegex(runner.PilotfishError, "symlink"):
                runner.validate_run_state(linked, layout)

    def test_load_rejects_state_symlink_and_oversize_before_read(self) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-load"
            )
            runner.persist_state(layout, state)
            real_state = layout.root / "real-state.json"
            layout.state_path.replace(real_state)
            layout.state_path.symlink_to(real_state)
            with self.assertRaisesRegex(runner.PilotfishError, "regular"):
                runner.load_and_validate_run_state(layout.state_path, repo)
            layout.state_path.unlink()
            with layout.state_path.open("wb") as handle:
                handle.truncate(16 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(runner.PilotfishError, "byte limit"):
                runner.load_and_validate_run_state(layout.state_path, repo)

    def test_persist_uses_exact_exclusive_atomic_temporary(self) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-atomic"
            )
            nonce = state["owner_nonce"]
            temporary = layout.root / f".run-state.{nonce}.tmp"
            real_replace = os.replace
            replacements: list[tuple[Path, Path]] = []

            def record_replace(source: object, target: object) -> None:
                replacements.append((Path(source), Path(target)))
                real_replace(source, target)

            with patch.object(runner.os, "replace", side_effect=record_replace):
                runner.persist_state(layout, state)

            self.assertEqual(
                replacements, [(temporary, layout.state_path)]
            )
            self.assertFalse(os.path.lexists(temporary))
            before = layout.state_path.read_bytes()
            temporary.write_text("occupied\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                runner.persist_state(layout, state)
            self.assertEqual(layout.state_path.read_bytes(), before)
            self.assertEqual(
                temporary.read_text(encoding="utf-8"), "occupied\n"
            )

    def test_initializer_preowns_deterministic_inventory_and_refresh_rejects_unknowns(
        self,
    ) -> None:
        with make_repo() as repo:
            job = lifecycle_job(
                "writer-a",
                ("tracked.txt",),
                commands=(lifecycle_command("unit", "python3", "-V"),),
            )
            manifest = lifecycle_manifest(
                repo,
                (job,),
                commands=(lifecycle_command("all", "python3", "-V"),),
                run_id="task-6-state-inventory",
            )
            baseline = runner.preflight_repo(manifest)
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)
            with repo_lock(baseline, manifest.run_id) as lock:
                state = runner.initialize_run_state(
                    manifest, baseline, layout, lock
                )

            for unexpected in (
                layout.artifacts / "rogue.txt",
                layout.artifacts / "rogue-directory",
            ):
                if unexpected.suffix:
                    unexpected.write_text("rogue\n", encoding="utf-8")
                else:
                    unexpected.mkdir()
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(
                    runner.PilotfishError, "unexpected"
                ) as raised:
                    runner.refresh_owned_state_records(
                        state, layout, workers=None
                    )
                self.assertEqual(raised.exception.state, "QUARANTINED")
                self.assertEqual(state, before)
                if unexpected.is_dir():
                    unexpected.rmdir()
                else:
                    unexpected.unlink()

            policy = runner.load_policy()
            expected_files = {
                layout.artifacts / "combined.patch",
                layout.root / "preflight.index",
                layout.root / "verify.index",
                layout.rollback / "rollback.json",
                layout.rollback / "0000.bin",
                layout.rollback
                / f"{policy.max_changed_files - 1:04d}.bin",
                layout.artifacts / "writer-a" / "events.jsonl",
                layout.artifacts / "writer-a" / "stderr.log",
                layout.artifacts / "writer-a" / "final.json",
                layout.artifacts
                / "job-checks"
                / "writer-a"
                / "unit.stdout",
                layout.artifacts
                / "job-checks"
                / "writer-a"
                / "unit.stderr",
                layout.artifacts / "integration" / "all.stdout",
                layout.artifacts / "integration" / "all.stderr",
            }
            self.assertTrue(
                {str(path) for path in expected_files}.issubset(
                    state["owned_files"]
                )
            )
            expected_directories = {
                layout.rollback,
                layout.artifacts / "writer-a",
                layout.artifacts / "job-checks",
                layout.artifacts / "job-checks" / "writer-a",
                layout.artifacts / "integration",
            }
            self.assertTrue(
                {str(path) for path in expected_directories}.issubset(
                    state["owned_directories"]
                )
            )

    def test_worktree_records_enforce_exact_namespace(self) -> None:
        with make_repo() as repo:
            _manifest, baseline, layout, state = self.initialize(
                repo, "task-6-state-namespace"
            )
            cases = (
                {
                    "path": str(layout.worktrees / "other"),
                    "branch_ref": f"refs/heads/{layout.integration_branch}",
                    "kind": "integration",
                },
                {
                    "path": str(layout.integration_worktree),
                    "branch_ref": "refs/heads/pf/task-6-state-namespace/other",
                    "kind": "integration",
                },
                {
                    "path": str(layout.worktrees / "nested" / "writer-a"),
                    "branch_ref": "refs/heads/pf/task-6-state-namespace/writer-a",
                    "kind": "worker",
                },
                {
                    "path": str(layout.worktrees / "integration"),
                    "branch_ref": "refs/heads/pf/task-6-state-namespace/integration",
                    "kind": "worker",
                },
            )
            for case in cases:
                with self.subTest(case=case):
                    malformed = copy.deepcopy(state)
                    malformed["worktrees"] = [{
                        **case,
                        "expected_ref_sha": baseline.base_sha,
                        "head_sha": baseline.base_sha,
                    }]
                    with self.assertRaisesRegex(
                        runner.PilotfishError, "namespace"
                    ):
                        runner.validate_run_state(malformed, layout)

    def test_initialize_requires_live_lock_and_persists_recovery_state(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(
                repo, run_id="task-6-state-live-lock"
            )
            baseline = runner.preflight_repo(manifest)
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)
            inactive = repo_lock(baseline, manifest.run_id)
            with inactive:
                pass

            with self.assertRaisesRegex(
                runner.PilotfishError, "lock"
            ):
                runner.initialize_run_state(
                    manifest, baseline, layout, inactive
                )
            self.assertFalse((layout.root / "owner.json").exists())

            with repo_lock(baseline, manifest.run_id) as lock:
                state = runner.initialize_run_state(
                    manifest, baseline, layout, lock
                )
                self.assertTrue(layout.state_path.is_file())
                self.assertEqual(
                    runner.load_and_validate_run_state(
                        layout.state_path, repo
                    ),
                    state,
                )

    def test_owner_marker_is_read_from_nofollow_inode_stable_descriptor(
        self,
    ) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-owner-fd"
            )
            real_open = os.open
            owner_opens: list[int] = []

            def observe_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if Path(path) == layout.root / "owner.json":
                    owner_opens.append(flags)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("pathname owner read"),
            ), patch.object(runner.os, "open", side_effect=observe_open):
                runner.validate_run_state(state, layout)

            self.assertEqual(len(owner_opens), 1)
            self.assertTrue(owner_opens[0] & os.O_NOFOLLOW)

    def test_load_rejects_json_constants_and_validate_rejects_nonfinite_numbers(
        self,
    ) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-finite"
            )
            raw = layout.state_path.read_bytes()
            poisoned = raw.replace(
                b'"result": null', b'"result": {"bad": NaN}'
            )
            self.assertNotEqual(poisoned, raw)
            layout.state_path.write_bytes(poisoned)
            with patch.object(runner, "validate_run_state") as validate:
                with self.assertRaisesRegex(
                    runner.PilotfishError, "constant"
                ):
                    runner.load_and_validate_run_state(
                        layout.state_path, repo
                    )
            validate.assert_not_called()

            for field in ("transition", "process"):
                with self.subTest(field=field):
                    malformed = copy.deepcopy(state)
                    if field == "transition":
                        malformed["transitions"] = [{
                            "state": "PRECHECK",
                            "monotonic": float("inf"),
                            "evidence": {},
                        }]
                    else:
                        malformed["processes"] = [{
                            "job_id": "writer-a",
                            "pid": None,
                            "pgid": None,
                            "started": float("inf"),
                            "finished": None,
                            "exit_code": None,
                            "status": "CREATED",
                        }]
                    with self.assertRaisesRegex(
                        runner.PilotfishError, "finite"
                    ):
                        runner.validate_run_state(malformed, layout)

    def test_persist_rejects_oversize_before_replace_and_preserves_old_state(
        self,
    ) -> None:
        with make_repo() as repo:
            _manifest, _baseline, layout, state = self.initialize(
                repo, "task-6-state-persist-limit"
            )
            before = layout.state_path.read_bytes()
            state["result"] = {"payload": "x" * (16 * 1024 * 1024)}
            with self.assertRaisesRegex(
                runner.PilotfishError, "byte limit"
            ):
                runner.persist_state(layout, state)

            self.assertEqual(layout.state_path.read_bytes(), before)
            temporary = (
                layout.root
                / f".run-state.{state['owner_nonce']}.tmp"
            )
            self.assertFalse(os.path.lexists(temporary))

    def test_rejects_invalid_cleanup_worktree_and_process_records(self) -> None:
        with make_repo() as repo:
            _manifest, baseline, layout, state = self.initialize(
                repo, "task-6-state-records"
            )
            malformed = dict(state)
            malformed["cleanup_progress"] = {"removed_worktrees": []}
            with self.assertRaisesRegex(runner.PilotfishError, "cleanup"):
                runner.validate_run_state(malformed, layout)

            record = {
                "path": str(layout.worktrees / "writer-a"),
                "branch_ref": "refs/heads/pf/task-6-state-records/writer-a",
                "expected_ref_sha": baseline.base_sha,
                "head_sha": baseline.base_sha,
                "kind": "worker-pending",
            }
            malformed = dict(state)
            malformed["worktrees"] = [record, dict(record)]
            with self.assertRaisesRegex(runner.PilotfishError, "duplicate"):
                runner.validate_run_state(malformed, layout)

            process = {
                "job_id": "writer-a",
                "pid": 1001,
                "pgid": 1001,
                "started": 1.0,
                "finished": None,
                "exit_code": None,
                "status": "PARALLEL_RUNNING",
            }
            malformed = dict(state)
            malformed["processes"] = [process, dict(process)]
            with self.assertRaisesRegex(runner.PilotfishError, "duplicate"):
                runner.validate_run_state(malformed, layout)

    def test_refresh_updates_ref_head_processes_and_artifact_ownership(
        self,
    ) -> None:
        with make_repo() as repo:
            manifest, baseline, layout, state = self.initialize(
                repo, "task-6-state-refresh"
            )
            workers = runner.create_worktrees(
                manifest,
                baseline,
                runner.load_roles(),
                layout,
                state=state,
            )
            worker = workers[0]
            (worker.worktree / "tracked.txt").write_text(
                "snapshot\n", encoding="utf-8"
            )
            run_git(worker.worktree, "add", "tracked.txt")
            run_git(worker.worktree, "commit", "-m", "state refresh")
            worker.process = SimpleNamespace(pid=424242)
            worker.started_monotonic = 1.5
            worker.finished_monotonic = 2.5
            worker.exit_code = 0
            worker.status = "DONE"
            worker.events_path.parent.mkdir(parents=True, exist_ok=True)
            worker.events_path.write_text("{}\n", encoding="utf-8")
            internal = worker.worktree / "not-owned-directly.txt"
            internal.write_text("worktree\n", encoding="utf-8")

            runner.refresh_owned_state_records(
                state, layout, workers=workers
            )

            records = {
                record["kind"]: record for record in state["worktrees"]
            }
            worker_head = git_text(worker.worktree, "rev-parse", "HEAD")
            self.assertEqual(records["worker"]["expected_ref_sha"], worker_head)
            self.assertEqual(records["worker"]["head_sha"], worker_head)
            integration_head = git_text(
                layout.integration_worktree, "rev-parse", "HEAD"
            )
            self.assertEqual(
                records["integration"]["expected_ref_sha"], integration_head
            )
            self.assertEqual(records["integration"]["head_sha"], integration_head)
            self.assertEqual(
                state["processes"],
                [{
                    "job_id": worker.job.id,
                    "pid": 424242,
                    "pgid": 424242,
                    "started": 1.5,
                    "finished": 2.5,
                    "exit_code": 0,
                    "status": "DONE",
                }],
            )
            self.assertIn(str(worker.events_path), state["owned_files"])
            self.assertNotIn(str(internal), state["owned_files"])
            self.assertEqual(state["owned_files"], sorted(state["owned_files"]))
            self.assertEqual(
                state["owned_directories"], sorted(state["owned_directories"])
            )

    def test_refresh_none_preserves_pending_and_rejects_artifact_symlink(
        self,
    ) -> None:
        with make_repo() as repo:
            _manifest, baseline, layout, state = self.initialize(
                repo, "task-6-state-pending"
            )
            pending = {
                "path": str(layout.worktrees / "writer-a"),
                "branch_ref": "refs/heads/pf/task-6-state-pending/writer-a",
                "expected_ref_sha": baseline.base_sha,
                "head_sha": baseline.base_sha,
                "kind": "worker-pending",
            }
            state["worktrees"] = [pending]

            runner.refresh_owned_state_records(state, layout, workers=None)
            self.assertEqual(state["worktrees"], [pending])

            target = layout.state_path
            link = layout.artifacts / "linked-artifact"
            link.symlink_to(target)
            with self.assertRaisesRegex(runner.PilotfishError, "symlink"):
                runner.refresh_owned_state_records(
                    state, layout, workers=None
                )


class CleanupValidationTests(unittest.TestCase):
    def prepare_cleanup(self, run_id: str) -> SimpleNamespace:
        repository_context = make_repo()
        repo = repository_context.__enter__()
        self.addCleanup(repository_context.__exit__, None, None, None)
        manifest = manifest_for(repo, run_id=run_id)
        baseline = runner.preflight_repo(manifest)
        layout = runner.create_layout(manifest)
        self.addCleanup(shutil.rmtree, layout.root, True)
        with repo_lock(baseline, run_id) as lock:
            state = runner.initialize_run_state(
                manifest, baseline, layout, lock
            )
            workers = runner.create_worktrees(
                manifest,
                baseline,
                runner.load_roles(),
                layout,
                state=state,
            )
            state["state"] = "WORKER_FAILED"
            runner.refresh_owned_state_records(
                state, layout, workers=workers
            )
            runner.persist_state(layout, state)
        return SimpleNamespace(
            repo=repo,
            manifest=manifest,
            baseline=baseline,
            layout=layout,
            state=state,
            workers=workers,
        )

    def verify(self, fixture: SimpleNamespace) -> None:
        actual = runner.parse_worktree_porcelain(
            runner.git(
                fixture.repo,
                "worktree",
                "list",
                "--porcelain",
                "-z",
            ).stdout
        )
        runner.verify_all_owned_paths_and_objects(
            fixture.state,
            actual,
            removed_worktrees=set(),
            removed_refs=set(),
        )

    def assert_objects_intact(self, fixture: SimpleNamespace) -> None:
        for record in fixture.state["worktrees"]:
            self.assertTrue(Path(record["path"]).is_dir())
            self.assertEqual(
                runner.git_text(
                    fixture.repo,
                    "rev-parse",
                    "--verify",
                    record["branch_ref"],
                ),
                record["expected_ref_sha"],
            )

    def test_rejects_unknown_files_directories_and_symlinks_without_mutation(
        self,
    ) -> None:
        cases = ("file", "directory", "symlink")
        for kind in cases:
            with self.subTest(kind=kind):
                fixture = self.prepare_cleanup(
                    f"task-6-cleanup-object-{kind}"
                )
                unexpected = fixture.layout.artifacts / f"unknown-{kind}"
                if kind == "file":
                    unexpected.write_text("foreign\n", encoding="utf-8")
                elif kind == "directory":
                    unexpected.mkdir()
                else:
                    unexpected.symlink_to(fixture.layout.state_path)

                with self.assertRaises(runner.PilotfishError) as raised:
                    self.verify(fixture)

                self.assertEqual(raised.exception.state, "QUARANTINED")
                self.assertTrue(os.path.lexists(unexpected))
                self.assert_objects_intact(fixture)

    def test_rejects_live_recorded_pid_and_pgid(self) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-live-process")
        base_record = {
            "job_id": "writer-a",
            "pid": None,
            "pgid": None,
            "started": 1.0,
            "finished": None,
            "exit_code": None,
            "status": "PARALLEL_RUNNING",
        }
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            for key, value in (("pid", os.getpid()), ("pgid", child.pid)):
                with self.subTest(key=key):
                    record = dict(base_record)
                    record[key] = value
                    fixture.state["processes"] = [record]

                    with self.assertRaisesRegex(
                        runner.PilotfishError, "alive"
                    ) as raised:
                        runner.verify_recorded_processes_dead(fixture.state)

                    self.assertEqual(raised.exception.state, "QUARANTINED")
                    self.assert_objects_intact(fixture)
        finally:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)

    def test_rejects_unknown_run_worktree_or_ref_but_ignores_other_namespaces(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-unknown-owned")
        unknown_path = fixture.layout.worktrees / "unknown"
        unknown_branch = "pf/task-6-cleanup-unknown-owned/unknown"
        run_git(
            fixture.repo,
            "worktree",
            "add",
            "-b",
            unknown_branch,
            str(unknown_path),
            fixture.baseline.base_sha,
        )
        try:
            with self.assertRaisesRegex(
                runner.PilotfishError, "unknown.*worktree"
            ) as raised:
                self.verify(fixture)
            self.assertEqual(raised.exception.state, "QUARANTINED")
            self.assertTrue(unknown_path.is_dir())
            self.assert_objects_intact(fixture)
        finally:
            run_git(
                fixture.repo,
                "worktree",
                "remove",
                "--force",
                str(unknown_path),
            )
            run_git(fixture.repo, "branch", "-D", unknown_branch)

        unknown_ref = (
            "refs/heads/pf/task-6-cleanup-unknown-owned/orphan"
        )
        run_git(
            fixture.repo,
            "update-ref",
            unknown_ref,
            fixture.baseline.base_sha,
        )
        try:
            with self.assertRaisesRegex(
                runner.PilotfishError, "unknown.*ref"
            ) as raised:
                self.verify(fixture)
            self.assertEqual(raised.exception.state, "QUARANTINED")
            self.assert_objects_intact(fixture)
        finally:
            run_git(fixture.repo, "update-ref", "-d", unknown_ref)

        run_git(
            fixture.repo,
            "update-ref",
            "refs/heads/unrelated-cleanup-fixture",
            fixture.baseline.base_sha,
        )
        try:
            self.verify(fixture)
        finally:
            run_git(
                fixture.repo,
                "update-ref",
                "-d",
                "refs/heads/unrelated-cleanup-fixture",
            )

    def test_rejects_owned_worktree_head_branch_or_ref_drift(self) -> None:
        for kind in ("head", "branch", "ref"):
            with self.subTest(kind=kind):
                fixture = self.prepare_cleanup(
                    f"task-6-cleanup-drift-{kind}"
                )
                record = next(
                    item
                    for item in fixture.state["worktrees"]
                    if item["kind"] == "worker"
                )
                worktree = Path(record["path"])
                if kind == "head":
                    (worktree / "tracked.txt").write_text(
                        "drift\n", encoding="utf-8"
                    )
                    run_git(worktree, "add", "tracked.txt")
                    run_git(worktree, "commit", "-m", "cleanup head drift")
                elif kind == "branch":
                    run_git(worktree, "checkout", "--detach")
                else:
                    record["expected_ref_sha"] = "0" * 40

                with self.assertRaisesRegex(
                    runner.PilotfishError, "drift|branch"
                ) as raised:
                    self.verify(fixture)

                self.assertEqual(raised.exception.state, "QUARANTINED")
                self.assertTrue(worktree.is_dir())

    def test_rejects_removed_progress_objects_that_reappear(self) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-reappeared")
        record = next(
            item
            for item in fixture.state["worktrees"]
            if item["kind"] == "worker"
        )
        run_git(
            fixture.repo,
            "worktree",
            "remove",
            "--force",
            record["path"],
        )
        run_git(fixture.repo, "update-ref", "-d", record["branch_ref"])
        fixture.state["cleanup_progress"] = {
            "removed_worktrees": [record["path"]],
            "removed_refs": [record["branch_ref"]],
        }
        runner.persist_state(fixture.layout, fixture.state)
        actual = runner.parse_worktree_porcelain(
            runner.git(
                fixture.repo,
                "worktree",
                "list",
                "--porcelain",
                "-z",
            ).stdout
        )
        runner.verify_all_owned_paths_and_objects(
            fixture.state,
            actual,
            removed_worktrees={record["path"]},
            removed_refs={record["branch_ref"]},
        )

        Path(record["path"]).mkdir()
        try:
            with self.assertRaisesRegex(
                runner.PilotfishError, "removed worktree reappeared"
            ):
                runner.verify_all_owned_paths_and_objects(
                    fixture.state,
                    actual,
                    removed_worktrees={record["path"]},
                    removed_refs={record["branch_ref"]},
                )
        finally:
            Path(record["path"]).rmdir()

        run_git(
            fixture.repo,
            "update-ref",
            record["branch_ref"],
            record["expected_ref_sha"],
        )
        try:
            with self.assertRaisesRegex(
                runner.PilotfishError, "removed ref reappeared"
            ):
                runner.verify_all_owned_paths_and_objects(
                    fixture.state,
                    actual,
                    removed_worktrees={record["path"]},
                    removed_refs={record["branch_ref"]},
                )
        finally:
            run_git(
                fixture.repo,
                "update-ref",
                "-d",
                record["branch_ref"],
            )


class CleanupExecutionTests(unittest.TestCase):
    def prepare_cleanup(self, run_id: str) -> SimpleNamespace:
        return CleanupValidationTests.prepare_cleanup(self, run_id)

    def assert_objects_intact(self, fixture: SimpleNamespace) -> None:
        CleanupValidationTests.assert_objects_intact(self, fixture)

    def test_cleanup_removes_exact_owned_objects_and_preserves_unrelated_ref(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-success")
        unrelated_ref = "refs/heads/unrelated-cleanup-preserved"
        run_git(
            fixture.repo,
            "update-ref",
            unrelated_ref,
            fixture.baseline.base_sha,
        )
        owned_refs = {
            record["branch_ref"] for record in fixture.state["worktrees"]
        }
        original_lock_identity = (
            fixture.state["lock_device"], fixture.state["lock_inode"]
        )
        artifact = fixture.workers[0].events_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")

        with repo_lock(
            fixture.baseline, "task-6-cleanup-success"
        ) as lock:
            result = runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )
            self.assertEqual(lock.device_inode, original_lock_identity)

        self.assertEqual(result["status"], "CLEANED")
        self.assertFalse(fixture.layout.root.exists())
        self.assertFalse(fixture.layout.root.parent.exists())
        for ref in owned_refs:
            self.assertNotEqual(
                runner.git(
                    fixture.repo,
                    "rev-parse",
                    "--verify",
                    ref,
                    check=False,
                ).returncode,
                0,
            )
        self.assertEqual(
            runner.git_text(
                fixture.repo, "rev-parse", "--verify", unrelated_ref
            ),
            fixture.baseline.base_sha,
        )
        lock_stat = fixture.baseline.common_dir.joinpath(
            "pilotfish-parallel.lock"
        ).stat()
        self.assertEqual(
            (lock_stat.st_dev, lock_stat.st_ino), original_lock_identity
        )
        self.assertEqual(
            fixture.baseline.common_dir.joinpath(
                "pilotfish-parallel.lock"
            ).read_text(encoding="utf-8"),
            "",
        )

    def test_cleanup_accepts_fully_absent_pending_objects(self) -> None:
        repository_context = make_repo()
        repo = repository_context.__enter__()
        self.addCleanup(repository_context.__exit__, None, None, None)
        run_id = "task-6-cleanup-pending"
        manifest = manifest_for(repo, run_id=run_id)
        baseline = runner.preflight_repo(manifest)
        layout = runner.create_layout(manifest)
        self.addCleanup(shutil.rmtree, layout.root, True)
        with repo_lock(baseline, run_id) as lock:
            state = runner.initialize_run_state(
                manifest, baseline, layout, lock
            )
            state["worktrees"] = [
                {
                    "path": str(layout.worktrees / "writer-a"),
                    "branch_ref": f"refs/heads/pf/{run_id}/writer-a",
                    "expected_ref_sha": baseline.base_sha,
                    "head_sha": baseline.base_sha,
                    "kind": "worker-pending",
                }
            ]
            state["state"] = "PRECHECK_FAILED"
            runner.persist_state(layout, state)
            result = runner.cleanup_run(
                repo,
                layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(result["status"], "CLEANED")
        self.assertEqual(
            result["removed_refs"],
            [f"refs/heads/pf/{run_id}/writer-a"],
        )
        self.assertFalse(layout.root.exists())

    def test_cleanup_refuses_nonterminal_or_lock_identity_mismatch_before_delete(
        self,
    ) -> None:
        for reason in ("nonterminal", "lock"):
            with self.subTest(reason=reason):
                fixture = self.prepare_cleanup(
                    f"task-6-cleanup-refuse-{reason}"
                )
                if reason == "nonterminal":
                    fixture.state["state"] = "PRECHECK"
                else:
                    fixture.state["lock_inode"] += 1
                runner.persist_state(fixture.layout, fixture.state)

                with repo_lock(
                    fixture.baseline,
                    f"task-6-cleanup-refuse-{reason}",
                ) as lock, self.assertRaises(runner.PilotfishError) as raised:
                    runner.cleanup_run(
                        fixture.repo,
                        fixture.layout.state_path,
                        require_finished=True,
                        lock=lock,
                    )

                self.assertEqual(raised.exception.state, "QUARANTINED")
                self.assert_objects_intact(fixture)
                self.assertTrue(fixture.layout.state_path.is_file())

    def test_cleanup_cas_race_preserves_drifted_ref_and_records_partial_progress(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-cas")
        drift_sha = run_git(
            fixture.repo,
            "-c",
            "user.name=Pilotfish Test",
            "-c",
            "user.email=pilotfish@example.invalid",
            "commit-tree",
            fixture.baseline.base_tree,
            "-p",
            fixture.baseline.base_sha,
            input_bytes=b"cleanup CAS drift\n",
        ).stdout.decode("ascii").strip()
        unrelated_ref = "refs/heads/unrelated-cas-preserved"
        run_git(
            fixture.repo,
            "update-ref",
            unrelated_ref,
            fixture.baseline.base_sha,
        )
        real_git = runner.git
        raced_ref: list[str] = []

        def race_before_delete(
            repo: Path, *args: str, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if args[:2] == ("update-ref", "-d") and not raced_ref:
                raced_ref.append(args[2])
                real_git(repo, "update-ref", args[2], drift_sha)
            return real_git(repo, *args, **kwargs)

        with repo_lock(
            fixture.baseline, "task-6-cleanup-cas"
        ) as lock, patch.object(
            runner, "git", side_effect=race_before_delete
        ), self.assertRaisesRegex(
            runner.PilotfishError, "CAS"
        ) as raised:
            runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(raised.exception.state, "QUARANTINED")
        persisted = runner.load_and_validate_run_state(
            fixture.layout.state_path, fixture.repo
        )
        self.assertEqual(
            set(persisted["cleanup_progress"]["removed_worktrees"]),
            {record["path"] for record in fixture.state["worktrees"]},
        )
        self.assertEqual(
            persisted["cleanup_progress"]["removed_refs"], []
        )
        self.assertEqual(
            runner.git_text(
                fixture.repo, "rev-parse", "--verify", raced_ref[0]
            ),
            drift_sha,
        )
        self.assertEqual(
            runner.git_text(
                fixture.repo, "rev-parse", "--verify", unrelated_ref
            ),
            fixture.baseline.base_sha,
        )
        self.assertTrue(fixture.layout.state_path.is_file())

    def test_cleanup_from_run_id_reacquires_three_argument_lock(self) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-command")

        result = runner.cleanup_from_run_id(
            fixture.repo, "task-6-cleanup-command"
        )

        self.assertEqual(result["status"], "CLEANED")
        self.assertFalse(fixture.layout.root.exists())

    def test_cleanup_removes_preowned_verification_clone_after_interruption(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-clone")
        clone = runner.create_isolated_verification_clone(
            fixture.baseline,
            fixture.layout,
            "integration",
            fixture.baseline.base_sha,
        )
        self.assertTrue((clone / ".git").exists())

        with repo_lock(
            fixture.baseline, "task-6-cleanup-clone"
        ) as lock:
            result = runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(result["status"], "CLEANED")
        self.assertFalse(fixture.layout.root.exists())

    def test_cleanup_recovers_when_progress_persist_fails_after_delete(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-progress-crash")
        real_mark = runner.mark_cleanup_progress
        calls = 0

        def fail_first_progress(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic crash after delete")
            real_mark(*args, **kwargs)

        with repo_lock(
            fixture.baseline, "task-6-cleanup-progress-crash"
        ) as lock, patch.object(
            runner, "mark_cleanup_progress", side_effect=fail_first_progress
        ), self.assertRaisesRegex(OSError, "synthetic crash"):
            runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        with repo_lock(
            fixture.baseline, "task-6-cleanup-progress-crash"
        ) as lock:
            result = runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(result["status"], "CLEANED")
        self.assertFalse(fixture.layout.root.exists())

    def test_cleanup_recovers_owned_state_temp_left_by_hard_interruption(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-state-temp")
        first = sorted(
            fixture.state["worktrees"],
            key=lambda record: record["path"],
            reverse=True,
        )[0]

        with repo_lock(
            fixture.baseline, "task-6-cleanup-state-temp"
        ) as lock:
            runner.mark_cleanup_started(fixture.state, fixture.layout)
            runner.git(
                fixture.repo,
                "worktree",
                "remove",
                "--force",
                first["path"],
            )
            temporary = fixture.layout.root / (
                f".run-state.{fixture.state['owner_nonce']}.tmp"
            )
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b'{"partial":')
                handle.flush()
                os.fsync(handle.fileno())

            result = runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(result["status"], "CLEANED")
        self.assertFalse(fixture.layout.root.exists())

    def test_cleanup_rechecks_worktree_fingerprint_before_force_remove(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-fingerprint")
        real_assert = runner.assert_cleanup_worktree_fingerprint
        injected = False

        def inject_then_assert(
            repo: Path, record: dict[str, object], expected: str
        ) -> None:
            nonlocal injected
            if not injected:
                injected = True
                Path(record["path"]).joinpath("late-untracked.txt").write_text(
                    "late drift\n", encoding="utf-8"
                )
            real_assert(repo, record, expected)

        with repo_lock(
            fixture.baseline, "task-6-cleanup-fingerprint"
        ) as lock, patch.object(
            runner,
            "assert_cleanup_worktree_fingerprint",
            side_effect=inject_then_assert,
        ), self.assertRaisesRegex(
            runner.PilotfishError, "fingerprint drift"
        ) as raised:
            runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(raised.exception.state, "QUARANTINED")
        self.assertTrue(injected)
        for record in fixture.state["worktrees"]:
            self.assertTrue(Path(record["path"]).is_dir())
        self.assertTrue(
            fixture.workers[0].worktree.joinpath(
                "late-untracked.txt"
            ).is_file()
        )

    def test_cleanup_fingerprint_hashes_existing_untracked_file_bytes(
        self,
    ) -> None:
        fixture = self.prepare_cleanup("task-6-cleanup-content-fingerprint")
        first = sorted(
            fixture.state["worktrees"],
            key=lambda record: record["path"],
            reverse=True,
        )[0]
        untracked = Path(first["path"]) / "existing-untracked.txt"
        untracked.write_text("before\n", encoding="utf-8")
        real_assert = runner.assert_cleanup_worktree_fingerprint
        injected = False

        def mutate_then_assert(
            repo: Path, record: dict[str, object], expected: str
        ) -> None:
            nonlocal injected
            if not injected:
                injected = True
                untracked.write_text("after\n", encoding="utf-8")
            real_assert(repo, record, expected)

        with repo_lock(
            fixture.baseline, "task-6-cleanup-content-fingerprint"
        ) as lock, patch.object(
            runner,
            "assert_cleanup_worktree_fingerprint",
            side_effect=mutate_then_assert,
        ), self.assertRaisesRegex(
            runner.PilotfishError, "fingerprint drift"
        ) as raised:
            runner.cleanup_run(
                fixture.repo,
                fixture.layout.state_path,
                require_finished=True,
                lock=lock,
            )

        self.assertEqual(raised.exception.state, "QUARANTINED")
        self.assertTrue(injected)
        self.assertEqual(untracked.read_text(encoding="utf-8"), "after\n")
        for record in fixture.state["worktrees"]:
            self.assertTrue(Path(record["path"]).is_dir())


class SnapshotIntegrationTests(unittest.TestCase):
    def create_workers(
        self,
        repo: Path,
        manifest: runner.Manifest,
        *,
        state: dict[str, object] | None = None,
    ) -> tuple[runner.RepoBaseline, runner.RunLayout, list[runner.WorkerRun]]:
        baseline = runner.preflight_repo(manifest)
        layout = runner.create_layout(manifest)
        self.addCleanup(shutil.rmtree, layout.root, True)
        workers = runner.create_worktrees(
            manifest,
            baseline,
            runner.load_roles(),
            layout,
            state=state,
        )
        return baseline, layout, workers

    def snapshot_text(
        self,
        worker: runner.WorkerRun,
        baseline: runner.RepoBaseline,
        layout: runner.RunLayout,
        path: str,
        value: str,
    ) -> None:
        destination = worker.worktree / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value, encoding="utf-8")
        worker.validated_result = {"changed_paths": [path]}
        runner.snapshot_worker(worker, baseline, layout)

    def assert_quarantined(self, error: runner.PilotfishError) -> None:
        self.assertEqual(error.state, "QUARANTINED")

    def test_layout_and_worktrees_use_owned_sorted_branches(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job("writer-b", ("b.txt",)),
                    lifecycle_job("writer-a", ("a.txt",)),
                ),
            )

            baseline, layout, workers = self.create_workers(repo, manifest)

            self.assertEqual([worker.job.id for worker in workers], ["writer-a", "writer-b"])
            self.assertEqual(layout.root.parent.name, runner.repo_id(repo))
            self.assertEqual(layout.root.name, manifest.run_id)
            self.assertEqual(layout.integration_worktree.parent, layout.worktrees)
            self.assertEqual(
                git_text(layout.integration_worktree, "rev-parse", "HEAD"),
                baseline.base_sha,
            )
            for worker in workers:
                self.assertEqual(
                    git_text(worker.worktree, "rev-parse", "HEAD"),
                    baseline.base_sha,
                )
                self.assertEqual(
                    git_text(worker.worktree, "branch", "--show-current"),
                    worker.branch,
                )

    def test_layout_refuses_to_reuse_existing_run_directory(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-5-existing")
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)

            with self.assertRaisesRegex(runner.PilotfishError, "already exists"):
                runner.create_layout(manifest)

    def test_intended_worktree_is_persisted_before_git_mutation(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-5-state")
            baseline = runner.preflight_repo(manifest)
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)
            with repo_lock(baseline, manifest.run_id) as lock:
                state = runner.initialize_run_state(
                    manifest, baseline, layout, lock
                )
            real_git = runner.git

            def fail_worktree_add(
                target: Path, *args: str, **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                if "worktree" in args and "add" in args:
                    raise runner.PilotfishError("PRECHECK_FAILED", "synthetic add failure")
                return real_git(target, *args, **kwargs)

            with patch.object(runner, "git", side_effect=fail_worktree_add):
                with self.assertRaisesRegex(runner.PilotfishError, "synthetic"):
                    runner.create_worktrees(
                        manifest,
                        baseline,
                        runner.load_roles(),
                        layout,
                        state=state,
                    )

            persisted = json.loads(layout.state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(persisted["worktrees"]), 1)
            record = persisted["worktrees"][0]
            self.assertEqual(record["kind"], "worker-pending")
            self.assertEqual(record["expected_ref_sha"], baseline.base_sha)
            self.assertEqual(record["head_sha"], baseline.base_sha)
            self.assertFalse(any(layout.root.glob(".run-state.*.tmp")))

    def test_refresh_preserves_unmatched_pending_worktree_records(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-5-pending")
            baseline = runner.preflight_repo(manifest)
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)
            with repo_lock(baseline, manifest.run_id) as lock:
                state = runner.initialize_run_state(
                    manifest, baseline, layout, lock
                )
            pending = {
                "path": str(layout.worktrees / "writer-a"),
                "branch_ref": "refs/heads/pf/task-5-pending/writer-a",
                "expected_ref_sha": manifest.base_sha,
                "head_sha": manifest.base_sha,
                "kind": "worker-pending",
            }
            state["worktrees"] = [pending]

            runner.refresh_owned_state_records(state, layout, workers=[])

            self.assertEqual(state["worktrees"], [pending])

    def test_tracked_checkout_attribute_is_rejected_before_smudge_hook_runs(self) -> None:
        with make_repo() as repo:
            (repo / ".gitattributes").write_text(
                "tracked.txt filter=probe\n", encoding="utf-8"
            )
            run_git(repo, "add", ".gitattributes")
            run_git(repo, "commit", "-m", "add tracked filter attribute")
            manifest = manifest_for(repo, run_id="task-5-smudge")
            baseline = runner.preflight_repo(manifest)
            marker = repo / ".git" / "smudge-ran"
            probe = repo / ".git" / "smudge-probe.sh"
            probe.write_text(
                "#!/bin/sh\n"
                f": > {shlex.quote(str(marker))}\n"
                "cat\n",
                encoding="utf-8",
            )
            probe.chmod(0o755)
            run_git(repo, "config", "filter.probe.smudge", str(probe))
            layout = runner.create_layout(manifest)
            self.addCleanup(shutil.rmtree, layout.root, True)

            with self.assertRaisesRegex(runner.PilotfishError, "checkout attribute"):
                runner.create_worktrees(
                    manifest, baseline, runner.load_roles(), layout
                )

            self.assertFalse(marker.exists())
            self.assertEqual(tuple(layout.worktrees.iterdir()), ())

    def test_post_checkout_hook_is_disabled_for_all_owned_worktrees(self) -> None:
        with make_repo() as repo:
            marker = repo / ".git" / "post-checkout-ran"
            hook = repo / ".git" / "hooks" / "post-checkout"
            hook.write_text(
                "#!/bin/sh\n" f": > {shlex.quote(str(marker))}\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)
            manifest = manifest_for(repo, run_id="task-5-hooks")

            self.create_workers(repo, manifest)

            self.assertFalse(marker.exists())

    def test_two_disjoint_snapshots_merge_from_same_base(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for_two_writers(repo, ("a.txt",), ("b.txt",))
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(workers[0], baseline, layout, "a.txt", "A\n")
            self.snapshot_text(workers[1], baseline, layout, "b.txt", "B\n")

            integration, results = runner.integrate_snapshots(
                workers, baseline, layout, (), runner.load_policy()
            )

            self.assertEqual(results, [])
            self.assertEqual((integration / "a.txt").read_text(encoding="utf-8"), "A\n")
            self.assertEqual((integration / "b.txt").read_text(encoding="utf-8"), "B\n")

    def test_conflict_aborts_and_leaves_integration_clean(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for_two_writers(
                repo, ("tracked.txt",), ("tracked.txt",)
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "writer A\n"
            )
            self.snapshot_text(
                workers[1], baseline, layout, "tracked.txt", "writer B\n"
            )

            with self.assertRaisesRegex(runner.PilotfishError, "conflict"):
                runner.integrate_snapshots(
                    workers, baseline, layout, (), runner.load_policy()
                )

            self.assertEqual(
                runner.git(
                    layout.integration_worktree,
                    "status",
                    "--porcelain=v1",
                    "-z",
                ).stdout,
                b"",
            )
            self.assertFalse(
                (layout.integration_worktree / ".git" / "MERGE_HEAD").exists()
            )

    def test_worker_commit_or_head_movement_is_quarantined(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-5-head")
            baseline, layout, workers = self.create_workers(repo, manifest)
            worker = workers[0]
            (worker.worktree / "tracked.txt").write_text(
                "worker commit\n", encoding="utf-8"
            )
            run_git(worker.worktree, "add", "tracked.txt")
            run_git(worker.worktree, "commit", "-m", "worker must not commit")
            worker.validated_result = {"changed_paths": ["tracked.txt"]}

            with self.assertRaisesRegex(runner.PilotfishError, "moved HEAD") as raised:
                runner.snapshot_worker(worker, baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_snapshot_rejects_worker_added_hidden_index_flags(self) -> None:
        cases = (
            ("assume-unchanged", "--assume-unchanged"),
            ("skip-worktree", "--skip-worktree"),
        )
        for label, flag in cases:
            with self.subTest(flag=label), make_repo() as repo:
                manifest = manifest_for(
                    repo, run_id=f"task-5-worker-index-{label}"
                )
                baseline, layout, workers = self.create_workers(repo, manifest)
                worker = workers[0]
                run_git(worker.worktree, "update-index", flag, "tracked.txt")
                (worker.worktree / "tracked.txt").write_text(
                    f"hidden by worker {label}\n", encoding="utf-8"
                )
                worker.validated_result = {"changed_paths": ["tracked.txt"]}

                with self.assertRaisesRegex(
                    runner.PilotfishError, "index flags"
                ) as raised:
                    runner.snapshot_worker(worker, baseline, layout)

                self.assert_quarantined(raised.exception)
                self.assertEqual(
                    runner.git(
                        worker.worktree,
                        "diff",
                        "--cached",
                        "--name-only",
                    ).stdout,
                    b"",
                )

    def test_empty_executor_patch_is_quarantined(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-empty")
            )
            workers[0].validated_result = {"changed_paths": []}

            with self.assertRaisesRegex(runner.PilotfishError, "empty patch") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_read_only_worker_mutation_is_quarantined(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("scout-a", ("tracked.txt",), role="scout"),),
                run_id="task-5-read-only",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            (workers[0].worktree / "tracked.txt").write_text(
                "not read-only\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(runner.PilotfishError, "read-only") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_clean_read_only_worker_finishes_without_snapshot(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("scout-a", ("tracked.txt",), role="scout"),),
                run_id="task-5-read-only-clean",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)

            runner.snapshot_worker(workers[0], baseline, layout)

            self.assertEqual(workers[0].status, "DONE")
            self.assertIsNone(workers[0].snapshot_sha)

    def test_dot_git_casefold_path_is_rejected(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", (".GIT",)),),
                run_id="task-5-dot-git",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            (workers[0].worktree / ".GIT").write_text("forbidden\n", encoding="utf-8")
            workers[0].validated_result = {"changed_paths": [".GIT"]}

            with self.assertRaisesRegex(runner.PilotfishError, "forbidden path"):
                runner.snapshot_worker(workers[0], baseline, layout)

    def test_new_symlink_mode_is_quarantined(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("link.txt",)),),
                run_id="task-5-symlink-add",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            (workers[0].worktree / "link.txt").symlink_to("tracked.txt")
            workers[0].validated_result = {"changed_paths": ["link.txt"]}

            with self.assertRaisesRegex(runner.PilotfishError, "non-regular") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_deleted_symlink_mode_is_quarantined(self) -> None:
        with make_repo() as repo:
            (repo / "link.txt").symlink_to("tracked.txt")
            run_git(repo, "add", "link.txt")
            run_git(repo, "commit", "-m", "add symlink fixture")
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("link.txt",)),),
                run_id="task-5-symlink-delete",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            (workers[0].worktree / "link.txt").unlink()
            workers[0].validated_result = {"changed_paths": ["link.txt"]}

            with self.assertRaisesRegex(runner.PilotfishError, "non-regular") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_gitlink_mode_is_quarantined(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("vendor",)),),
                run_id="task-5-gitlink",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            worker = workers[0]
            embedded = worker.worktree / "vendor"
            embedded.mkdir()
            run_git(embedded, "init", "-b", "main")
            run_git(embedded, "config", "user.name", "Embedded Test")
            run_git(embedded, "config", "user.email", "embedded@example.invalid")
            (embedded / "file.txt").write_text("embedded\n", encoding="utf-8")
            run_git(embedded, "add", "file.txt")
            run_git(embedded, "commit", "-m", "embedded fixture")
            run_git(worker.worktree, "add", "vendor")
            worker.validated_result = {"changed_paths": ["vendor"]}

            with self.assertRaisesRegex(runner.PilotfishError, "non-regular") as raised:
                runner.snapshot_worker(worker, baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_path_outside_allowlist_is_quarantined(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-outside")
            )
            (workers[0].worktree / "outside.txt").write_text(
                "outside\n", encoding="utf-8"
            )
            workers[0].validated_result = {"changed_paths": ["outside.txt"]}

            with self.assertRaisesRegex(runner.PilotfishError, "outside allowlist") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_denied_path_wins_over_allowlist(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job(
                        "writer-a",
                        ("allowed",),
                        denied_paths=("allowed/blocked.txt",),
                    ),
                ),
                run_id="task-5-denied",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            blocked = workers[0].worktree / "allowed" / "blocked.txt"
            blocked.parent.mkdir()
            blocked.write_text("blocked\n", encoding="utf-8")
            workers[0].validated_result = {
                "changed_paths": ["allowed/blocked.txt"]
            }

            with self.assertRaisesRegex(runner.PilotfishError, "denied path") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_ignored_file_creation_is_quarantined(self) -> None:
        with make_repo() as repo:
            (repo / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
            run_git(repo, "add", ".gitignore")
            run_git(repo, "commit", "-m", "add ignore fixture")
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("ignored.tmp",)),),
                run_id="task-5-ignored",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            (workers[0].worktree / "ignored.tmp").write_text(
                "hidden\n", encoding="utf-8"
            )
            workers[0].validated_result = {"changed_paths": ["ignored.tmp"]}

            with self.assertRaisesRegex(runner.PilotfishError, "ignored files") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_changed_gitattributes_is_rejected_before_stage(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", (".gitattributes",)),),
                run_id="task-5-attributes-change",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            attributes = workers[0].worktree / ".gitattributes"
            attributes.write_text("*.txt filter=unsafe\n", encoding="utf-8")
            workers[0].validated_result = {"changed_paths": [".gitattributes"]}

            with self.assertRaisesRegex(runner.PilotfishError, "gitattributes") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)
            self.assertEqual(
                runner.git(workers[0].worktree, "diff", "--cached", "--name-only").stdout,
                b"",
            )

    def test_custom_clean_or_process_filter_is_rejected_before_git_add(self) -> None:
        for filter_kind in ("clean", "process"):
            with self.subTest(filter=filter_kind), make_repo() as repo:
                (repo / ".gitattributes").write_text(
                    "*.flt filter=probe\n", encoding="utf-8"
                )
                run_git(repo, "add", ".gitattributes")
                run_git(repo, "commit", "-m", "add untracked filter rule")
                manifest = lifecycle_manifest(
                    repo,
                    (lifecycle_job("writer-a", ("payload.flt",)),),
                    run_id=f"task-5-filter-{filter_kind}",
                )
                baseline, layout, workers = self.create_workers(repo, manifest)
                marker = repo / ".git" / f"{filter_kind}-ran"
                probe = repo / ".git" / f"{filter_kind}-probe.sh"
                probe.write_text(
                    "#!/bin/sh\n"
                    f": > {shlex.quote(str(marker))}\n"
                    "cat\n",
                    encoding="utf-8",
                )
                probe.chmod(0o755)
                run_git(
                    repo,
                    "config",
                    f"filter.probe.{filter_kind}",
                    str(probe),
                )
                payload = workers[0].worktree / "payload.flt"
                payload.write_text("filtered?\n", encoding="utf-8")
                workers[0].validated_result = {"changed_paths": ["payload.flt"]}

                with self.assertRaisesRegex(runner.PilotfishError, "custom Git filter") as raised:
                    runner.snapshot_worker(workers[0], baseline, layout)

                self.assert_quarantined(raised.exception)
                self.assertFalse(marker.exists())
                self.assertEqual(
                    runner.git(
                        workers[0].worktree, "diff", "--cached", "--name-only"
                    ).stdout,
                    b"",
                )

    def test_binary_file_snapshot_is_supported(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("binary.bin",)),),
                run_id="task-5-binary",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            payload = b"\x00\xffpilotfish\x00\x80"
            (workers[0].worktree / "binary.bin").write_bytes(payload)
            workers[0].validated_result = {"changed_paths": ["binary.bin"]}

            runner.snapshot_worker(workers[0], baseline, layout)

            self.assertEqual(workers[0].status, "SNAPSHOT_READY")
            self.assertEqual(
                runner.git(
                    workers[0].worktree,
                    "show",
                    f"{workers[0].snapshot_sha}:binary.bin",
                ).stdout,
                payload,
            )

    def test_regular_file_deletion_snapshot_is_supported(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-delete")
            )
            (workers[0].worktree / "tracked.txt").unlink()
            workers[0].validated_result = {"changed_paths": ["tracked.txt"]}

            runner.snapshot_worker(workers[0], baseline, layout)

            missing = runner.git(
                workers[0].worktree,
                "cat-file",
                "-e",
                f"{workers[0].snapshot_sha}:tracked.txt",
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_executable_regular_file_mode_is_supported(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tool.py",)),),
                run_id="task-5-executable",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            tool = workers[0].worktree / "tool.py"
            tool.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            tool.chmod(0o755)
            workers[0].validated_result = {"changed_paths": ["tool.py"]}

            runner.snapshot_worker(workers[0], baseline, layout)

            mode = runner.git_text(
                workers[0].worktree,
                "ls-tree",
                workers[0].snapshot_sha or "",
                "tool.py",
            ).split()[0]
            self.assertEqual(mode, "100755")

    def test_patch_byte_limit_is_enforced(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-patch-limit")
            )
            (workers[0].worktree / "tracked.txt").write_text(
                "a patch larger than one byte\n", encoding="utf-8"
            )
            workers[0].validated_result = {"changed_paths": ["tracked.txt"]}
            tiny_policy = dataclasses.replace(
                runner.load_policy(), max_patch_bytes=1
            )

            with patch.object(runner, "load_policy", return_value=tiny_policy):
                with self.assertRaisesRegex(runner.PilotfishError, "patch byte limit") as raised:
                    runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_changed_file_count_limit_is_enforced(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("files",)),),
                run_id="task-5-file-limit",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            directory = workers[0].worktree / "files"
            directory.mkdir()
            (directory / "a.txt").write_text("A\n", encoding="utf-8")
            (directory / "b.txt").write_text("B\n", encoding="utf-8")
            workers[0].validated_result = {
                "changed_paths": ["files/a.txt", "files/b.txt"]
            }
            tiny_policy = dataclasses.replace(
                runner.load_policy(), max_changed_files=1
            )

            with patch.object(runner, "load_policy", return_value=tiny_policy):
                with self.assertRaisesRegex(runner.PilotfishError, "changed-file limit") as raised:
                    runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_actual_and_reported_changed_paths_must_match(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-reported")
            )
            (workers[0].worktree / "tracked.txt").write_text(
                "actual\n", encoding="utf-8"
            )
            workers[0].validated_result = {"changed_paths": []}

            with self.assertRaisesRegex(runner.PilotfishError, "reported/actual") as raised:
                runner.snapshot_worker(workers[0], baseline, layout)

            self.assert_quarantined(raised.exception)

    def test_snapshot_and_merge_commits_use_fixed_supervisor_identity(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-identity")
            )
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "snapshot\n"
            )
            snapshot_identity = runner.git_text(
                workers[0].worktree,
                "show",
                "-s",
                "--format=%an%x00%ae%x00%cn%x00%ce",
                workers[0].snapshot_sha or "",
            )

            integration, _ = runner.integrate_snapshots(
                workers, baseline, layout, (), runner.load_policy()
            )
            merge_identity = runner.git_text(
                integration,
                "show",
                "-s",
                "--format=%an%x00%ae%x00%cn%x00%ce",
                "HEAD",
            )

            expected = "Pilotfish Supervisor\x00pilotfish@localhost"
            self.assertEqual(snapshot_identity, f"{expected}\x00{expected}")
            self.assertEqual(merge_identity, f"{expected}\x00{expected}")

    def test_integration_uses_fixed_snapshot_not_later_worktree_dirt(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-fixed-snapshot")
            )
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "snapshotted\n"
            )
            fixed_sha = workers[0].snapshot_sha
            (workers[0].worktree / "tracked.txt").write_text(
                "late dirt\n", encoding="utf-8"
            )

            integration, _ = runner.integrate_snapshots(
                workers, baseline, layout, (), runner.load_policy()
            )

            self.assertEqual(workers[0].snapshot_sha, fixed_sha)
            self.assertEqual(
                (integration / "tracked.txt").read_text(encoding="utf-8"),
                "snapshotted\n",
            )

    def test_merge_order_is_deterministic_by_job_id(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job("writer-b", ("b.txt",)),
                    lifecycle_job("writer-a", ("a.txt",)),
                ),
                run_id="task-5-order",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            by_id = {worker.job.id: worker for worker in workers}
            self.snapshot_text(
                by_id["writer-a"], baseline, layout, "a.txt", "A\n"
            )
            self.snapshot_text(
                by_id["writer-b"], baseline, layout, "b.txt", "B\n"
            )

            integration, _ = runner.integrate_snapshots(
                list(reversed(workers)), baseline, layout, (), runner.load_policy()
            )

            merges = runner.git_text(
                integration,
                "rev-list",
                "--first-parent",
                "--reverse",
                f"{baseline.base_sha}..HEAD",
            ).splitlines()
            second_parents = [
                runner.git_text(integration, "rev-parse", f"{merge}^2")
                for merge in merges
            ]
            self.assertEqual(
                second_parents,
                [by_id["writer-a"].snapshot_sha, by_id["writer-b"].snapshot_sha],
            )

    def test_integration_rejects_worker_without_fixed_snapshot(self) -> None:
        with make_repo() as repo:
            baseline, layout, workers = self.create_workers(
                repo, manifest_for(repo, run_id="task-5-missing-snapshot")
            )

            with self.assertRaisesRegex(runner.PilotfishError, "valid snapshot") as raised:
                runner.integrate_snapshots(
                    workers, baseline, layout, (), runner.load_policy()
                )

            self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")

    def test_minimal_environment_drops_secrets_and_unlisted_names(self) -> None:
        environment = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "LANG": "C.UTF-8",
            "API_TOKEN": "secret",
            "PILOTFISH_CANARY": "canary",
            "PYTHONPATH": "/tmp/injected",
        }
        with patch.dict(os.environ, environment, clear=True):
            observed = runner.minimal_environment()

        self.assertEqual(
            observed,
            {
                "PATH": "/usr/bin",
                "HOME": "/tmp/home",
                "LANG": "C.UTF-8",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )

    def test_argv_policy_rejects_external_scope_executable_and_token(self) -> None:
        policy = runner.load_policy()
        with tempfile.TemporaryDirectory(prefix="pilotfish-argv-") as directory:
            root = Path(directory)
            cases = (
                lifecycle_command(
                    "external", sys.executable, "-V", effect_scope="external"
                ),
                lifecycle_command("shell", "sh", "-c", "true"),
                lifecycle_command(
                    "network", sys.executable, "-c", "print('https://example.invalid')"
                ),
            )
            for command in cases:
                with self.subTest(command=command.id):
                    with self.assertRaises(runner.PilotfishError) as raised:
                        runner.run_argv(command, root, root / "output", policy)
                    self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")

    def test_argv_output_is_captured_without_shell_parsing(self) -> None:
        policy = runner.load_policy()
        with tempfile.TemporaryDirectory(prefix="pilotfish-argv-") as directory:
            root = Path(directory)
            command = lifecycle_command(
                "literal",
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1]); print('err', file=sys.stderr)",
                "$(touch should-not-exist)",
            )

            result = runner.run_argv(command, root, root / "output", policy)

            self.assertEqual(result["argv"], list(command.argv))
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(
                (root / "output" / "literal.stdout").read_text(encoding="utf-8"),
                "$(touch should-not-exist)\n",
            )
            self.assertEqual(
                (root / "output" / "literal.stderr").read_text(encoding="utf-8"),
                "err\n",
            )
            self.assertFalse((root / "should-not-exist").exists())

    def test_argv_output_limit_is_enforced(self) -> None:
        policy = dataclasses.replace(
            runner.load_policy(), max_command_output_bytes=8
        )
        with tempfile.TemporaryDirectory(prefix="pilotfish-output-") as directory:
            root = Path(directory)
            command = lifecycle_command(
                "too-loud", sys.executable, "-c", "print('x' * 1000)"
            )

            with self.assertRaisesRegex(runner.PilotfishError, "output limit") as raised:
                runner.run_argv(command, root, root / "output", policy)

            self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")

    def test_command_timeout_terminates_grandchild_process_group(self) -> None:
        policy = runner.load_policy()
        with tempfile.TemporaryDirectory(prefix="pilotfish-timeout-") as directory:
            root = Path(directory)
            script = (
                "import pathlib, subprocess, sys, time; "
                "child=subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(60)']); "
                "pathlib.Path('grandchild.pid').write_text(str(child.pid)); "
                "time.sleep(60)"
            )
            command = lifecycle_command(
                "timeout", sys.executable, "-c", script, timeout_seconds=1
            )

            with warnings.catch_warnings(record=True) as observed_warnings:
                warnings.simplefilter("always", ResourceWarning)
                with self.assertRaisesRegex(
                    runner.PilotfishError, "timed out"
                ) as raised:
                    runner.run_argv(command, root, root / "output", policy)
                self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
                grandchild_pid = int(
                    (root / "grandchild.pid").read_text(encoding="utf-8")
                )
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    stat_path = Path(f"/proc/{grandchild_pid}/stat")
                    if not stat_path.exists():
                        break
                    fields = stat_path.read_text(encoding="utf-8").split()
                    if len(fields) > 2 and fields[2] == "Z":
                        break
                    time.sleep(0.05)
                else:
                    self.fail(
                        f"grandchild process survived timeout: {grandchild_pid}"
                    )
                del raised
                gc.collect()
            resource_warnings = [
                warning
                for warning in observed_warnings
                if issubclass(warning.category, ResourceWarning)
            ]
            self.assertEqual(resource_warnings, [])

    def test_verification_clone_has_independent_objects_config_and_refs(self) -> None:
        with make_repo() as repo:
            manifest = manifest_for(repo, run_id="task-5-independent")
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "snapshot\n"
            )

            clone = runner.create_isolated_verification_clone(
                baseline,
                layout,
                "independent",
                workers[0].snapshot_sha or "",
            )
            try:
                clone_git_dir = Path(
                    runner.git_text(clone, "rev-parse", "--absolute-git-dir")
                ).resolve()
                source_git_dir = Path(
                    runner.git_text(repo, "rev-parse", "--absolute-git-dir")
                ).resolve()
                self.assertNotEqual(clone_git_dir, source_git_dir)
                self.assertFalse(
                    (clone_git_dir / "objects" / "info" / "alternates").exists()
                )
                self.assertEqual(runner.git_text(clone, "remote"), "")
                self.assertEqual(
                    runner.git_text(clone, "rev-parse", "HEAD"),
                    workers[0].snapshot_sha,
                )
            finally:
                runner.remove_verification_clone(clone, layout)
            self.assertFalse(clone.exists())

    def test_worker_verification_mutation_is_confined_to_disposable_clone(self) -> None:
        command = lifecycle_command(
            "mutate",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('verification-only').write_text('x')",
        )
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",), commands=(command,)),),
                run_id="task-5-worker-verify",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "verified\n"
            )

            results = runner.verify_worker_snapshot(
                workers[0], baseline, layout, runner.load_policy()
            )

            self.assertEqual(results[0]["id"], "mutate")
            self.assertFalse((repo / "verification-only").exists())
            self.assertFalse((workers[0].worktree / "verification-only").exists())
            self.assertFalse((layout.verification_repos / "job-writer-a").exists())

    def test_worker_verification_detects_source_tree_drift(self) -> None:
        with make_repo() as repo:
            command = lifecycle_command(
                "source-drift",
                sys.executable,
                "-c",
                "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('drift\\n')",
                str(repo / "tracked.txt"),
            )
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",), commands=(command,)),),
                run_id="task-5-source-tree-drift",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "snapshot\n"
            )

            with self.assertRaisesRegex(runner.PilotfishError, "source tree") as raised:
                runner.verify_worker_snapshot(
                    workers[0], baseline, layout, runner.load_policy()
                )

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")
            self.assertFalse((layout.verification_repos / "job-writer-a").exists())

    def test_integration_command_mutation_is_disposable_and_argv_is_reported(self) -> None:
        command = lifecycle_command(
            "integration-mutate",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('clone-dirt').write_text('discard me')",
        )
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",)),),
                commands=(command,),
                run_id="task-5-integration-command",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "integrated\n"
            )

            integration, results = runner.integrate_snapshots(
                workers,
                baseline,
                layout,
                manifest.integration_verification_commands,
                runner.load_policy(),
            )

            self.assertEqual(results, [{"id": command.id, "argv": list(command.argv), "exit_code": 0}])
            self.assertFalse((repo / "clone-dirt").exists())
            self.assertFalse((integration / "clone-dirt").exists())
            self.assertFalse((layout.verification_repos / "integration").exists())

    def test_integration_verification_detects_source_ref_drift(self) -> None:
        with make_repo() as repo:
            command = lifecycle_command(
                "ref-drift",
                sys.executable,
                "-c",
                (
                    "import subprocess, sys; "
                    "subprocess.run(['git', '-C', sys.argv[1], 'update-ref', "
                    "'refs/heads/verification-drift', sys.argv[2]], check=True)"
                ),
                str(repo),
                git_text(repo, "rev-parse", "HEAD"),
            )
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",)),),
                commands=(command,),
                run_id="task-5-ref-drift",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "integrated\n"
            )

            with self.assertRaisesRegex(runner.PilotfishError, "changed Git refs") as raised:
                runner.integrate_snapshots(
                    workers,
                    baseline,
                    layout,
                    manifest.integration_verification_commands,
                    runner.load_policy(),
                )

            self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
            self.assertFalse((layout.verification_repos / "integration").exists())

    def test_integration_verification_detects_source_worktree_drift(self) -> None:
        with make_repo() as repo:
            command = lifecycle_command(
                "source-worktree-drift",
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_text('drift\\n')"
                ),
                str(repo / "tracked.txt"),
            )
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",)),),
                commands=(command,),
                run_id="task-5-integration-source-worktree-drift",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "integrated\n"
            )

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.integrate_snapshots(
                    workers,
                    baseline,
                    layout,
                    manifest.integration_verification_commands,
                    runner.load_policy(),
                )

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")
            self.assertFalse((layout.verification_repos / "integration").exists())

    def test_integration_verification_detects_source_index_drift(self) -> None:
        with make_repo() as repo:
            command = lifecycle_command(
                "source-index-drift",
                sys.executable,
                "-c",
                (
                    "import subprocess, sys; "
                    "subprocess.run(['git', '-C', sys.argv[1], "
                    "'update-index', '--chmod=+x', 'tracked.txt'], check=True)"
                ),
                str(repo),
            )
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",)),),
                commands=(command,),
                run_id="task-5-integration-source-index-drift",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "integrated\n"
            )

            with self.assertRaises(runner.PilotfishError) as raised:
                runner.integrate_snapshots(
                    workers,
                    baseline,
                    layout,
                    manifest.integration_verification_commands,
                    runner.load_policy(),
                )

            self.assertEqual(raised.exception.state, "SOURCE_DRIFTED")
            self.assertFalse((layout.verification_repos / "integration").exists())

    def test_integration_verification_detects_frozen_worktree_drift(self) -> None:
        with make_repo() as repo:
            manifest = lifecycle_manifest(
                repo,
                (lifecycle_job("writer-a", ("tracked.txt",)),),
                run_id="task-5-integration-drift",
            )
            baseline, layout, workers = self.create_workers(repo, manifest)
            self.snapshot_text(
                workers[0], baseline, layout, "tracked.txt", "integrated\n"
            )
            command = lifecycle_command(
                "integration-drift",
                sys.executable,
                "-c",
                "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('drift')",
                str(layout.integration_worktree / "unexpected.txt"),
            )

            with self.assertRaisesRegex(runner.PilotfishError, "not frozen and clean") as raised:
                runner.integrate_snapshots(
                    workers,
                    baseline,
                    layout,
                    (command,),
                    runner.load_policy(),
                )

            self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
            self.assertFalse((layout.verification_repos / "integration").exists())


class Task7CliContractTests(unittest.TestCase):
    def test_validate_cli_prints_machine_readable_success(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-task7-cli-"
        ) as directory:
            payload = manifest_dict(
                jobs=(
                    make_job("scout-a", "scout", ("docs",)),
                    make_job("writer-a", "executor", ("tracked.txt",)),
                ),
                run_id="task-7-cli-validate",
                repo_root=str(repo),
                base_branch="main",
                base_sha=git_text(repo, "rev-parse", "HEAD"),
                max_parallel=2,
            )
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "runner.py"),
                    "validate",
                    "--manifest",
                    str(manifest_path),
                ],
                cwd=directory,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", errors="replace"),
        )
        self.assertTrue(completed.stdout, "validate emitted no JSON")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["repo_root"], str(repo.resolve()))
        self.assertEqual(result["base_sha"], payload["base_sha"])

    def test_unknown_cli_command_fails_without_cwd_mutation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(
            prefix="pilotfish-task7-cli-unknown-"
        ) as directory:
            cwd = Path(directory)
            sentinel = cwd / "sentinel.txt"
            sentinel.write_bytes(b"unchanged\n")
            before = {
                path.relative_to(cwd).as_posix(): path.read_bytes()
                for path in cwd.rglob("*")
                if path.is_file()
            }
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "runner.py"),
                    "unknown",
                ],
                cwd=cwd,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            after = {
                path.relative_to(cwd).as_posix(): path.read_bytes()
                for path in cwd.rglob("*")
                if path.is_file()
            }

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(after, before)


class Task7FinalVerifierContractTests(unittest.TestCase):
    def test_final_verifier_is_fresh_xhigh_read_only_and_pins_tree(
        self,
    ) -> None:
        head = "1" * 40
        tree = "2" * 40
        diff = b"synthetic binary diff\n"
        captured: dict[str, object] = {}

        def fake_git(
            _repo: Path, *args: str, **_kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if args and args[0] == "status":
                stdout = b""
            elif "--name-only" in args:
                stdout = b"tracked.txt\0"
            elif args and args[0] == "diff":
                stdout = diff
            else:
                raise AssertionError(f"unexpected git argv: {args!r}")
            return subprocess.CompletedProcess(args, 0, stdout, b"")

        def fake_git_text(
            _repo: Path, *args: str, **_kwargs: object
        ) -> str:
            if args == ("rev-parse", "HEAD"):
                return head
            if args == ("rev-parse", "HEAD^{tree}"):
                return tree
            raise AssertionError(f"unexpected git-text argv: {args!r}")

        def capture_schedule(
            workers: object,
            verifier_manifest: runner.Manifest,
            _prefix: object,
            _policy: object,
            **kwargs: object,
        ) -> dict[str, int]:
            captured["workers"] = tuple(workers)  # type: ignore[arg-type]
            captured["manifest"] = verifier_manifest
            captured["scheduler_kwargs"] = kwargs
            return {"peak_active": 1, "max_parallel": 1}

        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-task7-verifier-"
        ) as directory:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job("writer-a", ("tracked.txt",)),
                    lifecycle_job("scout-a", ("docs",), role="scout"),
                ),
                run_id="task-7-final-verifier",
            )
            root = Path(directory)
            layout = runner.RunLayout(
                root=root,
                worktrees=root / "worktrees",
                verification_repos=root / "verification-repos",
                artifacts=root / "artifacts",
                rollback=root / "rollback",
                state_path=root / "run-state.json",
                integration_worktree=root / "worktrees" / "integration",
                integration_branch="pf/task-7-final-verifier/integration",
            )
            role = runner.load_roles()["verifier"]
            policy = runner.load_policy()
            run_state = {"processes": []}
            with patch.object(
                runner, "git", side_effect=fake_git
            ), patch.object(
                runner, "git_text", side_effect=fake_git_text
            ), patch.object(
                runner,
                "run_bounded_workers",
                side_effect=capture_schedule,
            ), patch.object(
                runner,
                "validate_worker_result",
                return_value={"status": "CONFIRMED"},
            ) as validate_result, patch.object(
                runner,
                "capture_worker_evidence",
                return_value=[{"job_id": "final-verifier"}],
                create=True,
            ):
                result = runner.run_final_verifier(
                    manifest,
                    role,
                    layout.integration_worktree,
                    layout,
                    ({"scope": "integration", "results": []},),
                    ("codex",),
                    policy,
                    state=run_state,
                )

        workers = captured["workers"]
        self.assertEqual(len(workers), 1)  # type: ignore[arg-type]
        worker = workers[0]  # type: ignore[index]
        self.assertIsNone(worker.process)
        self.assertEqual(worker.status, "CREATED")
        self.assertEqual(worker.job.id, "final-verifier")
        self.assertEqual(worker.role.name, "verifier")
        self.assertEqual(worker.role.model, "gpt-5.6-terra")
        self.assertEqual(worker.role.effort, "xhigh")
        self.assertEqual(worker.role.sandbox, "read-only")
        command = runner.build_worker_command(
            ("codex",), worker.role, worker.worktree, worker.final_path, policy
        )
        self.assertEqual(
            command[command.index("--sandbox") + 1], "read-only"
        )
        self.assertIn('model_reasoning_effort="xhigh"', command)
        verifier_manifest = captured["manifest"]
        self.assertEqual(verifier_manifest.max_parallel, 1)
        self.assertEqual(verifier_manifest.jobs, (worker.job,))
        self.assertIs(
            captured["scheduler_kwargs"]["state"], run_state
        )
        self.assertIs(
            captured["scheduler_kwargs"]["layout"], layout
        )
        evidence = json.loads(worker.job.goal)
        self.assertEqual(evidence["integration_head"], head)
        self.assertEqual(evidence["integration_tree"], tree)
        self.assertEqual(evidence["changed_paths"], ["tracked.txt"])
        self.assertEqual(
            evidence["diff_sha256"], hashlib.sha256(diff).hexdigest()
        )
        validate_result.assert_called_once_with(
            worker, verifier_manifest, head, policy
        )
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertEqual(
            result["runtime_evidence"], {"job_id": "final-verifier"}
        )

    def test_final_verifier_rejects_head_tree_or_cleanliness_drift(
        self,
    ) -> None:
        stable_head = "3" * 40
        stable_tree = "4" * 40
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-task7-verifier-gates-"
        ) as directory:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job("writer-a", ("tracked.txt",)),
                    lifecycle_job("scout-a", ("docs",), role="scout"),
                ),
                run_id="task-7-verifier-gates",
            )
            root = Path(directory)
            layout = runner.RunLayout(
                root=root,
                worktrees=root / "worktrees",
                verification_repos=root / "verification-repos",
                artifacts=root / "artifacts",
                rollback=root / "rollback",
                state_path=root / "run-state.json",
                integration_worktree=root / "worktrees" / "integration",
                integration_branch="pf/task-7-verifier-gates/integration",
            )
            role = runner.load_roles()["verifier"]
            policy = runner.load_policy()

            for drift in ("head", "tree", "dirty"):
                with self.subTest(drift=drift):
                    calls = {"head": 0, "tree": 0}

                    def fake_git_text(
                        _repo: Path, *args: str, **_kwargs: object
                    ) -> str:
                        if args == ("rev-parse", "HEAD"):
                            calls["head"] += 1
                            if drift == "head" and calls["head"] > 1:
                                return "5" * 40
                            return stable_head
                        if args == ("rev-parse", "HEAD^{tree}"):
                            calls["tree"] += 1
                            if drift == "tree" and calls["tree"] > 1:
                                return "6" * 40
                            return stable_tree
                        raise AssertionError(
                            f"unexpected git-text argv: {args!r}"
                        )

                    def fake_git(
                        _repo: Path, *args: str, **_kwargs: object
                    ) -> subprocess.CompletedProcess[bytes]:
                        if args and args[0] == "status":
                            stdout = b"?? verifier-dirt\0" if drift == "dirty" else b""
                        elif "--name-only" in args:
                            stdout = b"tracked.txt\0"
                        elif args and args[0] == "diff":
                            stdout = b"diff\n"
                        else:
                            raise AssertionError(
                                f"unexpected git argv: {args!r}"
                            )
                        return subprocess.CompletedProcess(
                            args, 0, stdout, b""
                        )

                    with patch.object(
                        runner, "git", side_effect=fake_git
                    ), patch.object(
                        runner, "git_text", side_effect=fake_git_text
                    ), patch.object(
                        runner,
                        "run_bounded_workers",
                        return_value={
                            "peak_active": 1,
                            "max_parallel": 1,
                        },
                    ), patch.object(
                        runner,
                        "validate_worker_result",
                        return_value={"status": "CONFIRMED"},
                    ), patch.object(
                        runner,
                        "capture_worker_evidence",
                        return_value=[{"job_id": "final-verifier"}],
                        create=True,
                    ), self.assertRaises(
                        runner.PilotfishError
                    ) as raised:
                        runner.run_final_verifier(
                            manifest,
                            role,
                            layout.integration_worktree,
                            layout,
                            (),
                            ("codex",),
                            policy,
                        )

                    self.assertEqual(
                        raised.exception.state, "VERIFICATION_FAILED"
                    )


class _Task7FakeContext:
    def __enter__(self) -> _Task7FakeContext:
        return self

    def __exit__(
        self, _exc_type: object, _exc: object, _traceback: object
    ) -> None:
        return None


class _Task7FakeLock(_Task7FakeContext):
    def __init__(self) -> None:
        self.device_inode = (701, 702)

    def assert_owned(self) -> None:
        return None


class _Task7FakeDeferred(_Task7FakeContext):
    def __init__(
        self,
        events: list[tuple[object, ...]],
        active: list[bool],
    ) -> None:
        self.events = events
        self.active = active
        self.received: set[signal.Signals] = set()

    def __enter__(self) -> _Task7FakeDeferred:
        self.active[0] = True
        self.events.append(("deferred-enter",))
        return self

    def __exit__(
        self, _exc_type: object, _exc: object, _traceback: object
    ) -> None:
        self.events.append(("deferred-exit",))
        self.active[0] = False
        return None


class Task7RunManifestContractTests(unittest.TestCase):
    @contextmanager
    def patched_pipeline(
        self,
        run_id: str,
        *,
        fail_applied_persist: bool = False,
        scheduler_failure: BaseException | None = None,
        cleanup_failure: BaseException | None = None,
        inject_fault_d: bool = False,
    ) -> object:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-task7-run-"
        ) as directory, ExitStack() as stack:
            manifest = lifecycle_manifest(
                repo,
                (
                    lifecycle_job("writer-a", ("tracked.txt",)),
                    lifecycle_job("scout-a", ("docs",), role="scout"),
                ),
                run_id=run_id,
            )
            baseline = runner.preflight_repo(manifest)
            root = Path(directory) / run_id
            layout = runner.RunLayout(
                root=root,
                worktrees=root / "worktrees",
                verification_repos=root / "verification-repos",
                artifacts=root / "artifacts",
                rollback=root / "rollback",
                state_path=root / "run-state.json",
                integration_worktree=root / "worktrees" / "integration",
                integration_branch=f"pf/{run_id}/integration",
            )
            layout.integration_worktree.mkdir(parents=True)
            layout.artifacts.mkdir(parents=True)
            layout.verification_repos.mkdir(parents=True)
            layout.rollback.mkdir(parents=True)
            roles = runner.load_roles()
            policy = runner.load_policy()
            state: dict[str, object] = {
                "state": "PRECHECK",
                "commit_point": False,
                "cleanup_required": True,
                "failure": None,
                "result": None,
            }
            workers: list[runner.WorkerRun] = []
            events: list[tuple[object, ...]] = []
            deferred_active = [False]
            deferred_holder: list[_Task7FakeDeferred] = []
            final_verifier_calls: list[
                tuple[tuple[object, ...], dict[str, object]]
            ] = []
            lock = _Task7FakeLock()
            lock_args: list[tuple[object, ...]] = []
            integration_values = {
                "head": "7" * 40,
                "tree": "8" * 40,
            }
            original_integration = dict(integration_values)
            bundle = runner.RollbackBundle(
                manifest_path=layout.rollback / "rollback.json",
                manifest_sha256="9" * 64,
                records=(),
                missing_parents=(),
            )

            def patch_runner(
                name: str, **kwargs: object
            ) -> object:
                return stack.enter_context(
                    patch.object(runner, name, create=True, **kwargs)
                )

            def lock_factory(*args: object) -> _Task7FakeLock:
                lock_args.append(args)
                return lock

            def initialize(*_args: object, **_kwargs: object) -> dict[str, object]:
                events.append(("initialize",))
                return state

            persist_calls: list[dict[str, object]] = []

            def persist(
                _layout: runner.RunLayout, candidate: dict[str, object]
            ) -> None:
                snapshot = copy.deepcopy(candidate)
                json.dumps(snapshot, allow_nan=False)
                persist_calls.append(snapshot)
                events.append(
                    (
                        "persist",
                        snapshot.get("state"),
                        snapshot.get("commit_point"),
                        deferred_active[0],
                    )
                )
                if (
                    fail_applied_persist
                    and snapshot.get("state") == "APPLIED"
                ):
                    raise OSError("synthetic APPLIED persist failure")

            def create_workers(
                _manifest: runner.Manifest,
                _baseline: runner.RepoBaseline,
                supplied_roles: dict[str, runner.RoleConfig],
                _layout: runner.RunLayout,
                *,
                state: object = None,
                workers_out: list[runner.WorkerRun] | None = None,
            ) -> list[runner.WorkerRun]:
                del state
                events.append(("create-worktrees",))
                self.assertFalse(
                    persist_calls,
                    "run_manifest redundantly persisted initializer state",
                )
                assert workers_out is not None
                for job in manifest.jobs:
                    worker = runner.WorkerRun(
                        job=job,
                        role=supplied_roles[job.role],
                        worktree=layout.worktrees / job.id,
                        branch=f"pf/{run_id}/{job.id}",
                        process=None,
                        started_monotonic=1.0,
                        finished_monotonic=2.0,
                        events_path=layout.artifacts / job.id / "events.jsonl",
                        stderr_path=layout.artifacts / job.id / "stderr.log",
                        final_path=layout.artifacts / job.id / "final.json",
                        status="DONE",
                        snapshot_sha=None,
                        snapshot_tree=None,
                    )
                    workers_out.append(worker)
                    workers.append(worker)
                return workers_out

            def transition(
                candidate: dict[str, object],
                _layout: runner.RunLayout,
                new_state: str,
                **_evidence: object,
            ) -> None:
                candidate["state"] = new_state
                events.append(("transition", new_state))

            def validate_result(
                worker: runner.WorkerRun,
                _manifest: runner.Manifest,
                _expected_head: str,
                _policy: runner.InvocationPolicy,
            ) -> dict[str, object]:
                status = worker.role.success_statuses[0]
                worker.validated_result = {
                    "status": status,
                    "usage": {},
                    "changed_paths": [],
                }
                return worker.validated_result

            def snapshot_worker(
                worker: runner.WorkerRun,
                _baseline: runner.RepoBaseline,
                _layout: runner.RunLayout,
            ) -> None:
                worker.status = "SNAPSHOT_READY"
                worker.snapshot_sha = baseline.base_sha
                worker.snapshot_tree = baseline.base_tree

            def final_verifier(
                *args: object, **kwargs: object
            ) -> dict[str, object]:
                final_verifier_calls.append((args, kwargs))
                events.append(("final-verifier",))
                integration_values.update(
                    head="a" * 40,
                    tree="b" * 40,
                )
                return {"status": "CONFIRMED"}

            def git_text_for_pin(
                _repo: Path, *args: str, **_kwargs: object
            ) -> str:
                if args == ("rev-parse", "HEAD"):
                    return integration_values["head"]
                if args == ("rev-parse", "HEAD^{tree}"):
                    return integration_values["tree"]
                raise AssertionError(f"unexpected git-text argv: {args!r}")

            apply_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def apply(
                *args: object, **kwargs: object
            ) -> dict[str, object]:
                apply_calls.append((args, kwargs))
                events.append(("apply", deferred_active[0]))
                if inject_fault_d:
                    self.assertTrue(deferred_holder)
                    deferred_holder[0].received.add(signal.SIGTERM)
                return {
                    "patch_sha256": "c" * 64,
                    "applied_tree": original_integration["tree"],
                    "rollback_manifest": str(bundle.manifest_path),
                    "rollback_manifest_sha256": bundle.manifest_sha256,
                    "_rollback_bundle_internal": bundle,
                }

            def deferred_factory(
                _cancellation: object,
            ) -> _Task7FakeDeferred:
                deferred = _Task7FakeDeferred(events, deferred_active)
                deferred_holder.append(deferred)
                return deferred

            patch_runner("load_manifest", return_value=manifest)
            patch_runner("load_roles", return_value=roles)
            patch_runner("load_policy", return_value=policy)
            patch_runner("preflight_repo", return_value=baseline)
            patch_runner("create_layout", return_value=layout)
            patch_runner("RepoLock", side_effect=lock_factory)
            patch_runner(
                "CancellationController", return_value=_Task7FakeContext()
            )
            patch_runner("DeferredSignals", side_effect=deferred_factory)
            patch_runner("initialize_run_state", side_effect=initialize)
            persist_mock = patch_runner("persist_state", side_effect=persist)
            patch_runner("create_worktrees", side_effect=create_workers)
            patch_runner("transition_state", side_effect=transition)
            if scheduler_failure is None:
                scheduler_mock = patch_runner(
                    "run_bounded_workers",
                    return_value={"peak_active": 2, "max_parallel": 2},
                )
            else:
                scheduler_mock = patch_runner(
                    "run_bounded_workers", side_effect=scheduler_failure
                )
            patch_runner(
                "validate_worker_result", side_effect=validate_result
            )
            patch_runner("snapshot_worker", side_effect=snapshot_worker)
            patch_runner("verify_worker_snapshot", return_value=[])
            patch_runner(
                "integrate_snapshots",
                return_value=(layout.integration_worktree, []),
            )
            patch_runner("run_final_verifier", side_effect=final_verifier)
            patch_runner("git_text", side_effect=git_text_for_pin)
            patch_runner("cancellation_checkpoint", return_value=None)
            apply_mock = patch_runner(
                "apply_integration_transactionally", side_effect=apply
            )
            patch_runner("refresh_owned_state_records", return_value=None)
            patch_runner(
                "capture_worker_evidence",
                return_value=[{"job_id": "writer-a"}, {"job_id": "scout-a"}],
            )
            restore_mock = patch_runner(
                "restore_rollback_bundle", return_value=None
            )
            patch_runner("assert_source_unchanged", return_value=None)
            failure_persist_mock = patch_runner(
                "persist_failure_state_best_effort", return_value=None
            )
            patch_runner(
                "terminate_and_wait_live_workers", return_value=None
            )
            if cleanup_failure is None:
                cleanup_mock = patch_runner(
                    "cleanup_run", return_value={"status": "CLEANED"}
                )
            else:
                cleanup_mock = patch_runner(
                    "cleanup_run", side_effect=cleanup_failure
                )

            yield SimpleNamespace(
                repo=repo,
                manifest=manifest,
                baseline=baseline,
                layout=layout,
                state=state,
                bundle=bundle,
                events=events,
                lock_args=lock_args,
                persist_calls=persist_calls,
                persist_mock=persist_mock,
                scheduler_mock=scheduler_mock,
                final_verifier_calls=final_verifier_calls,
                apply_calls=apply_calls,
                apply_mock=apply_mock,
                restore_mock=restore_mock,
                failure_persist_mock=failure_persist_mock,
                cleanup_mock=cleanup_mock,
                original_integration=original_integration,
            )

    def test_run_manifest_pins_tree_and_keeps_fault_d_blocked_until_applied(
        self,
    ) -> None:
        with self.patched_pipeline(
            "task-7-run-fault-d", inject_fault_d=True
        ) as fixture:
            result = runner.run_manifest(Path("/tmp/task-7-manifest.json"))

        self.assertEqual(
            fixture.lock_args,
            [
                (
                    fixture.baseline.common_dir,
                    fixture.manifest.run_id,
                    fixture.baseline.common_dir_device_inode,
                )
            ],
        )
        self.assertEqual(len(fixture.apply_calls), 1)
        self.assertEqual(len(fixture.final_verifier_calls), 1)
        verifier_args, _verifier_kwargs = fixture.final_verifier_calls[0]
        verifier_checks = verifier_args[4]
        runtime_checks = [
            item
            for item in verifier_checks
            if item.get("scope") == "runtime"
        ]
        self.assertEqual(len(runtime_checks), 1)
        self.assertEqual(runtime_checks[0]["scheduler"]["peak_active"], 2)
        self.assertEqual(
            {item["job_id"] for item in runtime_checks[0]["workers"]},
            {"writer-a", "scout-a"},
        )
        _args, kwargs = fixture.apply_calls[0]
        self.assertEqual(
            kwargs["expected_integration_head"],
            fixture.original_integration["head"],
        )
        self.assertEqual(
            kwargs["expected_integration_tree"],
            fixture.original_integration["tree"],
        )
        self.assertEqual(len(fixture.persist_calls), 1)
        durable = fixture.persist_calls[0]
        self.assertEqual(durable["state"], "APPLIED")
        self.assertTrue(durable["commit_point"])
        self.assertNotIn(
            "_rollback_bundle_internal", durable["result"]
        )
        self.assertNotIn("_rollback_bundle_internal", result)
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["deferred_signals"], ["SIGTERM"])
        persist_event = next(
            index
            for index, item in enumerate(fixture.events)
            if item[:2] == ("persist", "APPLIED")
        )
        exit_event = fixture.events.index(("deferred-exit",))
        self.assertLess(persist_event, exit_event)
        self.assertTrue(fixture.events[persist_event][3])

    def test_applied_persist_failure_restores_exact_internal_bundle(
        self,
    ) -> None:
        with self.patched_pipeline(
            "task-7-run-persist-failure",
            fail_applied_persist=True,
        ) as fixture, self.assertRaises(
            runner.PilotfishError
        ) as raised:
            runner.run_manifest(Path("/tmp/task-7-manifest.json"))

        self.assertEqual(raised.exception.state, "INTEGRATION_FAILED")
        fixture.restore_mock.assert_called_once_with(
            fixture.baseline.root, fixture.bundle
        )
        self.assertEqual(len(fixture.apply_calls), 1)
        self.assertFalse(fixture.cleanup_mock.called)

    def test_precommit_failure_has_structured_cleanup_details_and_never_applies(
        self,
    ) -> None:
        failure = runner.PilotfishError(
            "WORKER_FAILED", "synthetic scheduler failure"
        )
        with self.patched_pipeline(
            "task-7-run-precommit-failure",
            scheduler_failure=failure,
        ) as fixture, self.assertRaises(
            runner.PilotfishError
        ) as raised:
            runner.run_manifest(Path("/tmp/task-7-manifest.json"))

        self.assertEqual(raised.exception.state, "WORKER_FAILED")
        self.assertEqual(
            raised.exception.details["run_id"], fixture.manifest.run_id
        )
        self.assertEqual(
            raised.exception.details["state_path"],
            str(fixture.layout.state_path),
        )
        self.assertEqual(
            raised.exception.details["artifacts"],
            str(fixture.layout.artifacts),
        )
        cleanup_argv = shlex.split(
            raised.exception.details["cleanup_command"]
        )
        self.assertEqual(cleanup_argv[0], sys.executable)
        self.assertEqual(cleanup_argv[2], "cleanup")
        self.assertEqual(
            cleanup_argv[-4:],
            [
                "--repo-root",
                str(fixture.baseline.root),
                "--run-id",
                fixture.manifest.run_id,
            ],
        )
        self.assertFalse(fixture.apply_mock.called)
        self.assertTrue(fixture.failure_persist_mock.called)

    def test_postcommit_cleanup_failure_remains_applied_with_warning(
        self,
    ) -> None:
        cleanup_failure = runner.PilotfishError(
            "QUARANTINED", "synthetic cleanup refusal"
        )
        with self.patched_pipeline(
            "task-7-run-cleanup-warning",
            cleanup_failure=cleanup_failure,
        ) as fixture:
            result = runner.run_manifest(Path("/tmp/task-7-manifest.json"))

        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(result["cleanup_required"])
        self.assertIn("synthetic cleanup refusal", result["cleanup_warning"])
        cleanup_argv = shlex.split(result["cleanup_command"])
        self.assertEqual(cleanup_argv[0], sys.executable)
        self.assertEqual(cleanup_argv[2], "cleanup")
        self.assertEqual(cleanup_argv[-1], fixture.manifest.run_id)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
worktree = Path(args[args.index("-C") + 1])
final_path = Path(args[args.index("-o") + 1])
prompt = sys.stdin.read()
payload_text = prompt.split("<pilotfish_job>\n", 1)[1].split(
    "\n</pilotfish_job>", 1
)[0]
payload = json.loads(payload_text)
job = payload["job"]
goal = job["goal"]
if goal.startswith("fake:sleep="):
    time.sleep(float(goal.split("=", 1)[1]))
if job["role"] == "executor":
    target = worktree / job["allowed_paths"][0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(job["id"] + "\n", encoding="utf-8")
head = subprocess.check_output(
    ["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True
).strip()
status = "CONFIRMED" if job["role"] == "verifier" else "DONE"
changed = list(job["allowed_paths"][:1]) if job["role"] == "executor" else []
result = {
    "schema_version": 1,
    "run_id": payload["run_id"],
    "job_id": job["id"],
    "role": job["role"],
    "base_sha": payload["base_sha"],
    "worktree_head_sha": head,
    "status": status,
    "summary": "fake worker complete",
    "changed_paths": changed,
    "commands": [],
    "evidence": ["fake-evidence"],
    "blocking_reason": None,
}
final_path.parent.mkdir(parents=True, exist_ok=True)
final_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
print('{"type":"thread.started","thread_id":"fake-' + job["id"] + '"}')
print('{"type":"turn.started"}')
print('{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}')
'''


def write_fake_codex(directory: Path) -> tuple[str, ...]:
    path = directory / "fake_codex.py"
    path.write_text(FAKE_CODEX, encoding="utf-8")
    path.chmod(0o755)
    return (sys.executable, str(path))


class FakeEndToEndTests(unittest.TestCase):
    def test_two_disjoint_writers_overlap_and_apply_once(self) -> None:
        with make_repo() as repo, tempfile.TemporaryDirectory(
            prefix="pilotfish-fake-runtime-"
        ) as directory:
            base_sha = git_text(repo, "rev-parse", "HEAD")
            payload = manifest_dict(
                jobs=(
                    make_job(
                        "writer-a",
                        "executor",
                        ("a.txt",),
                        goal="fake:sleep=0.25",
                    ),
                    make_job(
                        "writer-b",
                        "executor",
                        ("b.txt",),
                        goal="fake:sleep=0.25",
                    ),
                ),
                run_id="fake-two-writers",
                repo_root=str(repo),
                base_branch="main",
                base_sha=base_sha,
                max_parallel=2,
            )
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with patch.dict(
                os.environ, {"PILOTFISH_TESTING": "1"}, clear=False
            ):
                result = runner.run_manifest(
                    manifest_path,
                    codex_prefix=write_fake_codex(Path(directory)),
                )

            self.assertEqual(result["status"], "APPLIED")
            self.assertFalse(result["cleanup_required"])
            self.assertEqual(
                (repo / "a.txt").read_text(encoding="utf-8"),
                "writer-a\n",
            )
            self.assertEqual(
                (repo / "b.txt").read_text(encoding="utf-8"),
                "writer-b\n",
            )
            self.assertEqual(git_text(repo, "rev-parse", "HEAD"), base_sha)
            workers = result["workers"]
            self.assertEqual(
                {item["job_id"] for item in workers},
                {"writer-a", "writer-b"},
            )
            self.assertLess(
                max(item["started"] for item in workers),
                min(item["finished"] for item in workers),
            )
            self.assertEqual(result["scheduler"]["peak_active"], 2)

if __name__ == "__main__":
    unittest.main()
