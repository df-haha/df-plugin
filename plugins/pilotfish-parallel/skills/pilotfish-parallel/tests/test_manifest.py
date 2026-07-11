import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError

import runner
from tests.helpers import (
    command_dict,
    make_job,
    manifest_dict,
    valid_verifier_result,
    valid_worker_result,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_BEGIN = "<!-- pilotfish-manifest-json:begin -->"
MANIFEST_END = "<!-- pilotfish-manifest-json:end -->"


def load_candidate_validator():
    path = ROOT / "validate.py"
    spec = importlib.util.spec_from_file_location(
        "pilotfish_candidate_validator_under_test", path
    )
    if spec is None or spec.loader is None:
        raise AssertionError("validator import spec is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaticPackageTests(unittest.TestCase):
    def test_skill_has_exact_frontmatter_and_only_live_file_links(self) -> None:
        skill_path = ROOT / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        _opening, frontmatter, body = text.split("---\n", 2)
        lines = frontmatter.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "name: pilotfish-parallel")
        self.assertTrue(lines[1].startswith("description: Use when "))
        self.assertIn("並行處理", lines[1])
        self.assertIn("multiple disjoint write jobs", lines[1])
        self.assertIn("Never touch Claude", body)
        self.assertIn("NEEDS_WRITABLE_VERIFICATION", body)
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
            if "://" not in target and not target.startswith("#"):
                self.assertTrue(
                    (ROOT / target).is_file(),
                    f"Skill link does not exist: {target}",
                )

    def test_agents_draft_has_one_exact_model_free_marker_block(self) -> None:
        text = (ROOT / "AGENTS.append.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- pilotfish-parallel:begin -->"), 1)
        self.assertEqual(text.count("<!-- pilotfish-parallel:end -->"), 1)
        self.assertNotIn("gpt-", text.casefold())
        self.assertIn("Codex-only", text)
        self.assertIn("~/.codex/config.toml", text)
        self.assertIn("~/.claude/", text)

    def test_agents_draft_freezes_delivery_scope_and_forbids_superpower(
        self,
    ) -> None:
        text = (ROOT / "AGENTS.append.md").read_text(encoding="utf-8")
        self.assertEqual(
            text.count("<!-- pilotfish-delivery-discipline:begin -->"), 1
        )
        self.assertEqual(
            text.count("<!-- pilotfish-delivery-discipline:end -->"), 1
        )
        self.assertIn("禁止任何 `superpowers:*` Skill", text)
        self.assertIn("手動 TDD", text)
        self.assertIn("freeze scope", text)
        self.assertIn("一輪 review＋一輪 targeted re-review", text)
        self.assertIn("不得增加新檔案、新架構或新驗收關卡", text)
        self.assertIn("全程 Codex-only", text)

    def test_manifest_has_valid_lifecycle_status_and_exact_embedded_keys(self) -> None:
        text = (ROOT / "MANIFEST.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(MANIFEST_BEGIN), 1)
        self.assertEqual(text.count(MANIFEST_END), 1)
        payload = text.split(MANIFEST_BEGIN, 1)[1].split(
            MANIFEST_END, 1
        )[0].strip()
        manifest = json.loads(payload)
        self.assertEqual(
            set(manifest),
            {
                "schema_version",
                "status",
                "design",
                "plan",
                "candidate_files",
                "formal_files",
                "baselines",
                "formal_scope",
                "runtime_report",
            },
        )
        self.assertIn(manifest["status"], {"DRAFT", "READY"})
        expected_label = (
            "DRAFT — NOT INSTALLABLE"
            if manifest["status"] == "DRAFT"
            else "READY — INSTALL ONLY IF BASELINES MATCH"
        )
        self.assertIn(expected_label, text)

    def test_validator_module_exposes_fail_closed_modes(self) -> None:
        path = ROOT / "validate.py"
        spec = importlib.util.spec_from_file_location(
            "pilotfish_candidate_validator", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.FORMAL_FILES,
            module.CANDIDATE_FILES
            - {"AGENTS.append.md", "MANIFEST.md", "validate.py"},
        )
        for name in (
            "validate_candidate",
            "validate_payload",
            "validate_formal",
            "atomic_publish_directory",
            "atomic_unpublish_directory",
            "materialize_exact_file",
            "main",
        ):
            self.assertTrue(callable(getattr(module, name)))


class StaticContractTests(unittest.TestCase):
    def load_schema(self, name: str) -> dict[str, object]:
        return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))

    def test_roles_are_exact(self) -> None:
        with (ROOT / "config" / "roles.toml").open("rb") as handle:
            roles = tomllib.load(handle)["roles"]
        self.assertEqual(
            {
                key: (value["model"], value["effort"], value["sandbox"])
                for key, value in roles.items()
            },
            {
                "scout": ("gpt-5.6-luna", "low", "read-only"),
                "executor": ("gpt-5.6-terra", "low", "workspace-write"),
                "verifier": ("gpt-5.6-terra", "xhigh", "read-only"),
            },
        )

    def test_schemas_are_draft_2020_12(self) -> None:
        for name in ("job.schema.json", "result.schema.json"):
            Draft202012Validator.check_schema(self.load_schema(name))

    def test_result_output_const_and_enums_have_explicit_types(self) -> None:
        schema = self.load_schema("result.schema.json")
        properties = schema["properties"]
        for name in ("schema_version", "role", "status"):
            with self.subTest(property=name):
                self.assertIn("type", properties[name])

    def test_prompts_forbid_delegation_and_scope_expansion(self) -> None:
        for name in ("scout.md", "executor.md", "verifier.md"):
            prompt = (ROOT / "prompts" / name).read_text(encoding="utf-8").lower()
            self.assertIn("do not delegate", prompt)
            self.assertIn("do not expand scope", prompt)

    def test_verifier_prompt_defines_read_only_result_fields(self) -> None:
        prompt = (ROOT / "prompts" / "verifier.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`changed_paths` must be `[]`", prompt)
        self.assertIn("supervisor-declared verification commands", prompt)

    def test_executor_prompt_separates_declared_commands_from_tool_evidence(self) -> None:
        prompt = (ROOT / "prompts" / "executor.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "`commands` must list exactly the supervisor-declared verification commands",
            prompt,
        )
        self.assertIn(
            "An empty `verification_commands` list does not by itself require `NEEDS_CONTEXT`",
            prompt,
        )
        self.assertIn("investigative tool calls in `evidence`", prompt)

    def test_job_schema_accepts_a_valid_manifest(self) -> None:
        Draft202012Validator(self.load_schema("job.schema.json")).validate(
            manifest_dict()
        )

    def test_job_schema_rejects_a_missing_required_field(self) -> None:
        manifest = copy.deepcopy(manifest_dict())
        del manifest["jobs"]

        with self.assertRaises(ValidationError):
            Draft202012Validator(self.load_schema("job.schema.json")).validate(manifest)

    def test_result_schema_accepts_a_valid_result(self) -> None:
        Draft202012Validator(self.load_schema("result.schema.json")).validate(
            valid_worker_result()
        )

    def test_result_schema_rejects_a_missing_required_field(self) -> None:
        result = copy.deepcopy(valid_worker_result())
        del result["status"]

        with self.assertRaises(ValidationError):
            Draft202012Validator(self.load_schema("result.schema.json")).validate(result)

    def test_bootstrap_helpers_construct_schema_valid_payloads(self) -> None:
        command = command_dict()
        job = make_job(verification_commands=(command,))
        manifest = manifest_dict(jobs=(job, make_job("scout-a", "scout")))
        result_validator = Draft202012Validator(
            self.load_schema("result.schema.json")
        )

        Draft202012Validator(self.load_schema("job.schema.json")).validate(manifest)
        result_validator.validate(valid_worker_result())
        result_validator.validate(valid_verifier_result())
        self.assertEqual(command["effect_scope"], "repo-local")


class CandidateValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_candidate_validator()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="pilotfish-validator-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.temp_root = Path(self.temporary.name)

    def candidate_copy(self, name: str = "candidate") -> Path:
        destination = self.temp_root / name
        shutil.copytree(
            ROOT,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return destination

    def ready_manifest_for(self, root: Path) -> dict[str, object]:
        formal_files = {}
        for relative in self.validator.FORMAL_FILES:
            path = root / relative
            formal_files[relative] = {
                "sha256": self.validator.sha256(path),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        return {
            "status": "READY",
            "formal_files": formal_files,
            "baselines": {},
        }

    def make_home(self, name: str = "home") -> Path:
        home = self.temp_root / name
        (home / ".codex" / "skills").mkdir(parents=True)
        return home

    def make_payload(
        self, home: Path, basename: str, *, mode: int = 0o700
    ) -> tuple[Path, dict[str, object]]:
        root = home / ".codex" / "skills" / basename
        root.mkdir()
        root.chmod(mode)
        for relative in self.validator.FORMAL_FILES:
            source = ROOT / relative
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o755 if relative == "scripts/runner.py" else 0o644)
        return root, self.ready_manifest_for(root)

    def assert_validation_error(self, callable_, pattern: str) -> None:
        with self.assertRaisesRegex(
            self.validator.ValidationError, pattern
        ):
            callable_()

    def test_constants_embedded_manifest_and_candidate_draft_contract(self) -> None:
        expected = {
            "SKILL.md",
            "scripts/runner.py",
            "config/roles.toml",
            "prompts/scout.md",
            "prompts/executor.md",
            "prompts/verifier.md",
            "schemas/job.schema.json",
            "schemas/result.schema.json",
            "tests/__init__.py",
            "tests/helpers.py",
            "tests/test_manifest.py",
            "tests/test_paths.py",
            "tests/test_processes.py",
            "tests/test_git_lifecycle.py",
            "AGENTS.append.md",
            "MANIFEST.md",
            "validate.py",
        }
        self.assertEqual(self.validator.CANDIDATE_FILES, expected)
        self.assertEqual(
            self.validator.FORMAL_FILES,
            expected - {"AGENTS.append.md", "MANIFEST.md", "validate.py"},
        )
        manifest = self.validator.read_embedded_manifest(ROOT / "MANIFEST.md")
        self.assertIn(manifest["status"], {"DRAFT", "READY"})
        with patch.object(self.validator, "run") as run:
            self.validator.validate_candidate(ROOT, manifest)
        run.assert_called_once()

        copied = self.candidate_copy()
        cases = {
            "role": lambda: (copied / "config" / "roles.toml").write_text(
                (copied / "config" / "roles.toml")
                .read_text(encoding="utf-8")
                .replace('effort = "xhigh"', 'effort = "low"'),
                encoding="utf-8",
            ),
            "prompt": lambda: (copied / "prompts" / "scout.md").write_text(
                "do not delegate\n", encoding="utf-8"
            ),
            "schema": lambda: (copied / "schemas" / "job.schema.json").write_text(
                json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": 7}),
                encoding="utf-8",
            ),
            "mode": lambda: (copied / "scripts" / "runner.py").chmod(0o644),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                shutil.rmtree(copied)
                copied = self.candidate_copy()
                mutate()
                with patch.object(self.validator, "run"):
                    self.assert_validation_error(
                        lambda: self.validator.validate_candidate(
                            copied, manifest
                        ),
                        "role|prompt|schema|mode",
                    )

    def test_candidate_rejects_disguised_cache_and_forbidden_formal_scope(
        self,
    ) -> None:
        manifest = self.validator.read_embedded_manifest(ROOT / "MANIFEST.md")
        copied = self.candidate_copy("cache-candidate")
        with patch.object(self.validator, "run"):
            root_cache = copied / "hidden.pyc"
            root_cache.write_bytes(b"not a Python cache directory entry")
            self.assert_validation_error(
                lambda: self.validator.validate_candidate(copied, manifest),
                "file set|candidate object",
            )
            root_cache.unlink()

            cache = copied / "tests" / "__pycache__"
            cache.mkdir()
            (cache / "escape.pyc").symlink_to(copied / "SKILL.md")
            self.assert_validation_error(
                lambda: self.validator.validate_candidate(copied, manifest),
                "cache|candidate object|unsupported",
            )
            shutil.rmtree(cache)

            forbidden = copy.deepcopy(manifest)
            forbidden["formal_scope"] = [
                *manifest["formal_scope"],
                str(Path.home() / ".claude" / "rules"),
            ]
            self.assert_validation_error(
                lambda: self.validator.validate_candidate(copied, forbidden),
                "formal scope",
            )

    def test_payload_ready_root_grammar_exact_files_hash_modes_and_cache(self) -> None:
        home = self.make_home()
        with patch.dict(os.environ, {"HOME": str(home)}):
            root, manifest = self.make_payload(
                home, ".pilotfish-parallel.staging-install-001"
            )
            self.validator.validate_payload(root, manifest)

            extra = root / "extra.txt"
            extra.write_text("extra\n", encoding="utf-8")
            self.assert_validation_error(
                lambda: self.validator.validate_payload(root, manifest),
                "file set",
            )
            extra.unlink()

            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "bad.pyc").write_bytes(b"cache")
            self.assert_validation_error(
                lambda: self.validator.validate_payload(root, manifest),
                "cache",
            )
            shutil.rmtree(cache)

            runner_path = root / "scripts" / "runner.py"
            runner_path.chmod(0o644)
            self.assert_validation_error(
                lambda: self.validator.validate_payload(root, manifest),
                "hash/mode",
            )
            runner_path.chmod(0o755)

            invalid, invalid_manifest = self.make_payload(
                home, "unexpected-staging"
            )
            self.assert_validation_error(
                lambda: self.validator.validate_payload(
                    invalid, invalid_manifest
                ),
                "payload root",
            )

    def test_formal_requires_target_baselines_marker_no_agents_and_claude_digest(self) -> None:
        home = self.make_home()
        with patch.dict(os.environ, {"HOME": str(home)}):
            root, manifest = self.make_payload(home, "pilotfish-parallel")
            codex = home / ".codex"
            config = codex / "config.toml"
            config.write_text("model = 'unchanged'\n", encoding="utf-8")
            agents = codex / "AGENTS.md"
            agents.write_text(
                "before\n<!-- pilotfish-parallel:begin -->\n"
                "installed\n<!-- pilotfish-parallel:end -->\nafter\n",
                encoding="utf-8",
            )
            claude = home / ".claude"
            claude.mkdir()
            (claude / "settings.json").write_text("{}\n", encoding="utf-8")
            manifest["baselines"] = {
                "config_sha256": self.validator.sha256(config),
                "installed_agents_sha256": self.validator.sha256(agents),
                "claude_metadata_digest": self.validator.metadata_digest(claude),
            }
            self.validator.validate_formal(root, manifest)

            before = config.read_text(encoding="utf-8")
            config.write_text("drift\n", encoding="utf-8")
            self.assert_validation_error(
                lambda: self.validator.validate_formal(root, manifest),
                "hash mismatch",
            )
            config.write_text(before, encoding="utf-8")

            original_agents = agents.read_text(encoding="utf-8")
            agents.write_text(original_agents + "<!-- pilotfish-parallel:begin -->\n", encoding="utf-8")
            self.assert_validation_error(
                lambda: self.validator.validate_formal(root, manifest),
                "marker",
            )
            agents.write_text(original_agents, encoding="utf-8")

            (codex / "agents").mkdir()
            self.assert_validation_error(
                lambda: self.validator.validate_formal(root, manifest),
                "agents",
            )
            (codex / "agents").rmdir()

            (claude / "settings.json").write_text('{"drift": true}\n', encoding="utf-8")
            self.assert_validation_error(
                lambda: self.validator.validate_formal(root, manifest),
                "Claude metadata",
            )

    def test_run_uses_sanitized_argv_environment_and_no_shell(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.dict(
            os.environ,
            {
                "PATH": "/safe/bin",
                "HOME": "/safe/home",
                "LANG": "C.UTF-8",
                "SECRET_TOKEN": "must-not-leak",
                "PYTHONPATH": "/unsafe",
            },
            clear=True,
        ), patch.object(
            self.validator.subprocess,
            "run",
            return_value=completed,
        ) as invoke:
            self.validator.run(
                [sys.executable, "-B", "-m", "unittest"], ROOT
            )
        args, kwargs = invoke.call_args
        self.assertEqual(args[0], [sys.executable, "-B", "-m", "unittest"])
        self.assertIs(kwargs["shell"], False)
        self.assertNotIn("SECRET_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["PYTHONPATH"], str(ROOT / "scripts"))
        self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_atomic_publish_and_unpublish_are_noreplace_and_fail_on_competitor(self) -> None:
        home = self.make_home()
        skills = home / ".codex" / "skills"
        with patch.dict(os.environ, {"HOME": str(home)}):
            staging = skills / ".pilotfish-parallel.staging-install-001"
            target = skills / "pilotfish-parallel"
            staging.mkdir(mode=0o700)
            self.validator.atomic_publish_directory(staging, target)
            self.assertFalse(staging.exists())
            self.assertTrue(target.is_dir())

            quarantine = skills / ".pilotfish-parallel.rollback-install-001"
            self.validator.atomic_unpublish_directory(target, quarantine)
            self.assertFalse(target.exists())
            self.assertTrue(quarantine.is_dir())

            staging = skills / ".pilotfish-parallel.staging-install-002"
            staging.mkdir(mode=0o700)
            target.mkdir(mode=0o700)
            self.assert_validation_error(
                lambda: self.validator.atomic_publish_directory(staging, target),
                "exists|renameat2",
            )
            self.assertTrue(staging.is_dir())
            self.assertTrue(target.is_dir())

            quarantine = skills / ".pilotfish-parallel.rollback-install-002"
            quarantine.mkdir(mode=0o700)
            self.assert_validation_error(
                lambda: self.validator.atomic_unpublish_directory(
                    target, quarantine
                ),
                "exists|renameat2",
            )
            self.assertTrue(target.is_dir())
            self.assertTrue(quarantine.is_dir())

    def test_materialize_recovers_all_fault_windows_on_same_inode(self) -> None:
        source = self.temp_root / "source.bin"
        source.write_bytes((b"pilotfish" * 131072) + b"end")
        expected = hashlib.sha256(source.read_bytes()).hexdigest()

        for point in (
            "after_empty_creation",
            "halfway_write",
            "after_write_fsync",
            "after_chmod",
        ):
            with self.subTest(point=point):
                directory = self.temp_root / point
                directory.mkdir()
                destination = directory / "destination.bin"
                journal = directory / "journal.jsonl"
                sibling = directory / "sibling.txt"
                sibling.write_text("untouched\n", encoding="utf-8")
                sibling_identity = sibling.stat().st_ino

                def crash(observed: str) -> None:
                    if observed == point:
                        raise RuntimeError(f"crash:{point}")

                with patch.object(
                    self.validator, "_fault_point", side_effect=crash
                ), self.assertRaisesRegex(RuntimeError, "crash"):
                    self.validator.materialize_exact_file(
                        source, destination, expected, 0o640, journal
                    )
                crashed_identity = destination.stat().st_ino

                self.validator.materialize_exact_file(
                    source, destination, expected, 0o640, journal
                )

                self.assertEqual(destination.stat().st_ino, crashed_identity)
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o640)
                self.assertEqual(sibling.stat().st_ino, sibling_identity)
                self.assertEqual(sibling.read_text(encoding="utf-8"), "untouched\n")

    def test_materialize_fails_closed_on_inode_owner_source_hash_and_mode_drift(self) -> None:
        source = self.temp_root / "trusted.bin"
        source.write_bytes(b"trusted payload" * 10000)
        expected = self.validator.sha256(source)

        for drift in ("inode", "owner", "source", "mode"):
            with self.subTest(drift=drift):
                directory = self.temp_root / f"drift-{drift}"
                directory.mkdir()
                destination = directory / "destination.bin"
                journal = directory / "journal.jsonl"

                def crash(point: str) -> None:
                    if point == "halfway_write":
                        raise RuntimeError("crash")

                with patch.object(
                    self.validator, "_fault_point", side_effect=crash
                ), self.assertRaises(RuntimeError):
                    self.validator.materialize_exact_file(
                        source, destination, expected, 0o640, journal
                    )

                if drift == "inode":
                    replacement = directory / "replacement.bin"
                    replacement.write_bytes(b"replacement")
                    replacement.chmod(0o600)
                    self.assertNotEqual(
                        replacement.stat().st_ino, destination.stat().st_ino
                    )
                    os.replace(replacement, destination)
                elif drift == "owner":
                    getuid = patch.object(
                        self.validator.os,
                        "getuid",
                        return_value=os.getuid() + 1,
                    )
                    getuid.start()
                    self.addCleanup(getuid.stop)
                elif drift == "source":
                    source.write_bytes(source.read_bytes() + b"drift")
                else:
                    destination.chmod(0o644)

                self.assert_validation_error(
                    lambda: self.validator.materialize_exact_file(
                        source, destination, expected, 0o640, journal
                    ),
                    "inode|owner|source|hash|mode",
                )
                if drift == "owner":
                    getuid.stop()
                if drift == "source":
                    source.write_bytes(b"trusted payload" * 10000)

    def test_cli_requires_explicit_absolute_payload_paths_and_json_exit_two(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.validator.main(["payload"])
        self.assertEqual(result, 2)
        error = json.loads(output.getvalue())
        self.assertEqual(error["status"], "FAIL")
        self.assertEqual(error["mode"], "payload")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.validator.main(
                ["formal", "--root", "relative", "--manifest", "relative"]
            )
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "FAIL")

        manifest = {"status": "DRAFT"}
        with patch.object(
            self.validator,
            "read_embedded_manifest",
            return_value=manifest,
        ), patch.object(self.validator, "validate_candidate") as validate:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = self.validator.main(["candidate"])
        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "PASS", "mode": "candidate"},
        )
        validate.assert_called_once()


class RuntimeManifestValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def load_manifest(self, manifest: dict[str, object]) -> runner.Manifest:
        path = Path(self.temp_dir.name) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return runner.load_manifest(path)

    def test_loads_a_valid_manifest_into_typed_specs(self) -> None:
        job_command = command_dict("job-check")
        integration_command = command_dict("integration-check")
        manifest = self.load_manifest(
            manifest_dict(
                jobs=(
                    make_job("scout-a", "scout", ("docs",)),
                    make_job(
                        "writer-a",
                        "executor",
                        ("src",),
                        verification_commands=(job_command,),
                    ),
                ),
                integration_verification_commands=(integration_command,),
            )
        )

        self.assertIsInstance(manifest, runner.Manifest)
        self.assertIsInstance(manifest.jobs[1], runner.JobSpec)
        self.assertIsInstance(
            manifest.jobs[1].verification_commands[0], runner.CommandSpec
        )
        self.assertEqual(manifest.repo_root, Path("/tmp/repo").resolve())

    def test_loads_typed_roles_and_policy(self) -> None:
        roles = runner.load_roles()
        policy = runner.load_policy()

        self.assertEqual(set(roles), {"scout", "executor", "verifier"})
        self.assertIsInstance(roles["executor"], runner.RoleConfig)
        self.assertEqual(roles["verifier"].success_statuses, ("CONFIRMED",))
        self.assertIsInstance(policy, runner.InvocationPolicy)
        self.assertEqual(policy.max_parallel_hard_cap, 3)

    def test_rejects_more_than_three_jobs(self) -> None:
        manifest = manifest_dict(
            jobs=(
                make_job("writer-a", "executor", ("src/a",)),
                make_job("scout-a", "scout", ("src/b",)),
                make_job("scout-b", "scout", ("src/c",)),
                make_job("verifier-a", "verifier", ("src/d",)),
            )
        )

        with self.assertRaises(runner.PilotfishError):
            self.load_manifest(manifest)

    def test_rejects_max_parallel_above_three(self) -> None:
        manifest = manifest_dict(max_parallel=4)

        with self.assertRaises(runner.PilotfishError):
            self.load_manifest(manifest)

    def test_rejects_malformed_base_sha(self) -> None:
        manifest = manifest_dict(base_sha="not-a-sha")

        with self.assertRaises(runner.PilotfishError):
            self.load_manifest(manifest)

    def test_rejects_duplicate_job_ids(self) -> None:
        manifest = manifest_dict()
        manifest["jobs"][0]["id"] = manifest["jobs"][1]["id"]

        with self.assertRaisesRegex(runner.PilotfishError, "duplicate job ids"):
            self.load_manifest(manifest)

    def test_rejects_duplicate_command_ids_within_a_job(self) -> None:
        duplicate = command_dict("duplicate")
        manifest = manifest_dict(
            jobs=(
                make_job("scout-a", "scout", ("docs",)),
                make_job(
                    verification_commands=(duplicate, copy.deepcopy(duplicate))
                ),
            )
        )

        with self.assertRaisesRegex(runner.PilotfishError, "duplicate command ids"):
            self.load_manifest(manifest)

    def test_rejects_duplicate_integration_command_ids(self) -> None:
        duplicate = command_dict("duplicate")
        manifest = manifest_dict(
            integration_verification_commands=(
                duplicate,
                copy.deepcopy(duplicate),
            )
        )

        with self.assertRaisesRegex(runner.PilotfishError, "duplicate command ids"):
            self.load_manifest(manifest)

    def test_rejects_a_missing_job_timeout(self) -> None:
        manifest = manifest_dict()
        del manifest["jobs"][0]["timeout_seconds"]

        with self.assertRaises(runner.PilotfishError):
            self.load_manifest(manifest)

    def test_rejects_a_manifest_without_an_executor(self) -> None:
        manifest = manifest_dict(
            jobs=(
                make_job("scout-a", "scout", ("docs/a",)),
                make_job("scout-b", "scout", ("docs/b",)),
            )
        )

        with self.assertRaisesRegex(runner.PilotfishError, "executor"):
            self.load_manifest(manifest)

    def test_rejects_a_non_repo_local_effect_scope(self) -> None:
        command = command_dict()
        command["effect_scope"] = "network"
        manifest = manifest_dict(
            integration_verification_commands=(command,)
        )

        with self.assertRaises(runner.PilotfishError):
            self.load_manifest(manifest)

    def test_rejects_a_success_status_outside_its_role_terminal_statuses(self) -> None:
        source = (ROOT / "config" / "roles.toml").read_text(encoding="utf-8")
        invalid = source.replace(
            'success_statuses = ["DONE"]',
            'success_statuses = ["CONFIRMED"]',
            1,
        )
        config_path = Path(self.temp_dir.name) / "roles.toml"
        config_path.write_text(invalid, encoding="utf-8")

        with self.assertRaisesRegex(runner.PilotfishError, "status"):
            runner.load_roles(config_path)

    def test_rejects_drifted_role_execution_triples(self) -> None:
        source = (ROOT / "config" / "roles.toml").read_text(encoding="utf-8")
        for role in ("scout", "executor", "verifier"):
            for field in ("model", "effort", "sandbox"):
                with self.subTest(role=role, field=field):
                    lines = source.splitlines()
                    in_target_role = False
                    replaced = False
                    for index, line in enumerate(lines):
                        if line == f"[roles.{role}]":
                            in_target_role = True
                        elif line.startswith("["):
                            in_target_role = False
                        elif in_target_role and line.startswith(f"{field} = "):
                            lines[index] = f'{field} = "drifted"'
                            replaced = True
                            break
                    self.assertTrue(replaced)
                    config_path = (
                        Path(self.temp_dir.name) / f"roles-{role}-{field}.toml"
                    )
                    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

                    with self.assertRaisesRegex(
                        runner.PilotfishError, "execution contract drift"
                    ):
                        runner.load_roles(config_path)


if __name__ == "__main__":
    unittest.main()
