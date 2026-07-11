#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import copy
import dataclasses
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator


SKILL_ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
APPROVED_ROLE_EXECUTION_CONTRACTS = {
    "scout": ("gpt-5.6-luna", "low", "read-only"),
    "executor": ("gpt-5.6-terra", "low", "workspace-write"),
    "verifier": ("gpt-5.6-terra", "xhigh", "read-only"),
}
RESERVED_JOB_IDS = frozenset({"integration", "final-verifier"})
RUN_STATE_KEYS = frozenset(
    {
        "schema_version",
        "owner",
        "owner_nonce",
        "run_id",
        "repo_root",
        "layout_root",
        "base_branch",
        "base_sha",
        "base_tree",
        "index_tree",
        "lock_device",
        "lock_inode",
        "state",
        "commit_point",
        "cleanup_required",
        "worktrees",
        "processes",
        "owned_files",
        "owned_directories",
        "transitions",
        "failure",
        "result",
        "cleanup_progress",
        "failure_state_persist_error",
    }
)
RUN_STATES = frozenset(
    {
        "PRECHECK",
        "PARALLEL_RUNNING",
        "SNAPSHOTS_READY",
        "INTEGRATED",
        "VERIFIED",
        "PRECHECK_FAILED",
        "WORKER_FAILED",
        "QUARANTINED",
        "INTEGRATION_FAILED",
        "VERIFICATION_FAILED",
        "SOURCE_DRIFTED",
        "CANCELLED",
        "ROLLBACK_FAILED",
        "APPLIED",
    }
)
WORKTREE_RECORD_KEYS = frozenset(
    {"path", "branch_ref", "expected_ref_sha", "head_sha", "kind"}
)
PROCESS_RECORD_KEYS = frozenset(
    {"job_id", "pid", "pgid", "started", "finished", "exit_code", "status"}
)
MAX_RUN_STATE_BYTES = 16 * 1024 * 1024
MAX_OWNER_MARKER_BYTES = 64 * 1024


class PilotfishError(RuntimeError):
    def __init__(
        self,
        state: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.details = details or {}


class WorkerSpawnError(PilotfishError):
    def __init__(
        self,
        job_id: str,
        process: subprocess.Popen[bytes],
        original_error: BaseException,
        cleanup_error: BaseException,
    ) -> None:
        super().__init__(
            "WORKER_FAILED",
            f"worker spawn cleanup failed: {job_id}",
            {
                "original_error": (
                    f"{type(original_error).__name__}: {original_error}"
                ),
                "cleanup_error": (
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                ),
            },
        )
        self.process = process
        self.original_error = original_error
        self.cleanup_error = cleanup_error


@dataclass(frozen=True)
class CommandSpec:
    id: str
    argv: tuple[str, ...]
    timeout_seconds: int
    effect_scope: str


@dataclass(frozen=True)
class JobSpec:
    id: str
    role: str
    goal: str
    allowed_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    verification_commands: tuple[CommandSpec, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    run_id: str
    task_requirement: str
    completion_claim: str
    overall_acceptance_criteria: tuple[str, ...]
    repo_root: Path
    base_branch: str
    base_sha: str
    max_parallel: int
    integration_verification_commands: tuple[CommandSpec, ...]
    jobs: tuple[JobSpec, ...]


@dataclass(frozen=True)
class RepoBaseline:
    root: Path
    branch: str
    base_sha: str
    base_tree: str
    git_dir: Path
    common_dir: Path
    common_dir_device_inode: tuple[int, int]
    index_tree: str


@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    effort: str
    sandbox: str
    prompt_path: Path
    success_statuses: tuple[str, ...]
    terminal_statuses: tuple[str, ...]


@dataclass(frozen=True)
class InvocationPolicy:
    max_parallel_hard_cap: int
    approval_policy: str
    v2_session_thread_cap: int
    final_verifier_timeout_seconds: int
    max_patch_bytes: int
    max_changed_files: int
    max_event_log_bytes: int
    max_result_bytes: int
    max_manifest_bytes: int
    max_prompt_bytes: int
    max_command_output_bytes: int
    forbidden_executables: tuple[str, ...]
    forbidden_argv_tokens: tuple[str, ...]


@dataclass
class WorkerRun:
    job: JobSpec
    role: RoleConfig
    worktree: Path
    branch: str
    process: subprocess.Popen[bytes] | None
    started_monotonic: float
    finished_monotonic: float | None
    events_path: Path
    stderr_path: Path
    final_path: Path
    status: str
    snapshot_sha: str | None
    snapshot_tree: str | None
    validated_result: dict[str, Any] = field(default_factory=dict)
    runtime_metadata: dict[str, str] = field(default_factory=dict)
    thread_id: str | None = None
    exit_code: int | None = None
    invocation_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunLayout:
    root: Path
    worktrees: Path
    verification_repos: Path
    artifacts: Path
    rollback: Path
    state_path: Path
    integration_worktree: Path
    integration_branch: str


@dataclass(frozen=True)
class PatchArtifact:
    """Immutable patch bytes plus their on-disk evidence location."""

    path: Path
    sha256: str
    bytes: bytes


@dataclass(frozen=True)
class RollbackRecord:
    """Trusted rollback metadata captured before the source is mutated."""

    path: str
    existed: bool
    payload: str | None
    sha256: str | None
    mode: int | None


@dataclass(frozen=True)
class RollbackBundle:
    """Immutable rollback truth; files below manifest_path are evidence."""

    manifest_path: Path
    manifest_sha256: str
    records: tuple[RollbackRecord, ...]
    missing_parents: tuple[str, ...]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotfishError(
            "PRECHECK_FAILED", f"cannot read JSON {path}: {exc}"
        ) from exc


def validate_schema(instance: Any, schema_path: Path) -> None:
    schema = load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    if errors:
        details = "; ".join(
            f"{'/'.join(map(str, item.path)) or '<root>'}: {item.message}"
            for item in errors
        )
        raise PilotfishError(
            "PRECHECK_FAILED", f"schema validation failed: {details}"
        )


def normalize_prefix(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "\\" in value
        or value.startswith(("/", ":"))
        or any(
            unicodedata.category(character) in {"Cc", "Cs"}
            for character in value
        )
    ):
        raise PilotfishError("PRECHECK_FAILED", f"unsafe path prefix: {value!r}")
    if unicodedata.normalize("NFC", value) != value:
        raise PilotfishError(
            "PRECHECK_FAILED", f"path prefix must be NFC-normalized: {value!r}"
        )
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise PilotfishError("PRECHECK_FAILED", f"unsafe path prefix: {value!r}")
    if path.parts[0].casefold() == ".git" or "\x00" in value:
        raise PilotfishError("PRECHECK_FAILED", f"forbidden path prefix: {value!r}")
    return path.as_posix()


def path_is_within(path: str, prefix: str) -> bool:
    path_parts = PurePosixPath(path).parts
    prefix_parts = PurePosixPath(prefix).parts
    return path_parts[: len(prefix_parts)] == prefix_parts


def validate_disjoint_writer_paths(jobs: Sequence[JobSpec]) -> None:
    owners: list[tuple[str, str]] = []
    for job in jobs:
        if job.role != "executor":
            continue
        for prefix in job.allowed_paths:
            for owner_id, owner_prefix in owners:
                if path_is_within(prefix, owner_prefix) or path_is_within(
                    owner_prefix, prefix
                ):
                    raise PilotfishError(
                        "PRECHECK_FAILED",
                        "executor allowlist overlap: "
                        f"{owner_id}:{owner_prefix} vs {job.id}:{prefix}",
                    )
            owners.append((job.id, prefix))


def parse_command(raw: dict[str, Any]) -> CommandSpec:
    return CommandSpec(
        raw["id"],
        tuple(raw["argv"]),
        raw["timeout_seconds"],
        raw["effect_scope"],
    )


def parse_commands(
    raw: Sequence[dict[str, Any]], scope: str
) -> tuple[CommandSpec, ...]:
    commands = tuple(parse_command(value) for value in raw)
    if len({command.id for command in commands}) != len(commands):
        raise PilotfishError(
            "PRECHECK_FAILED", f"duplicate command ids in {scope}"
        )
    return commands


def parse_job(raw: dict[str, Any]) -> JobSpec:
    allowed = tuple(normalize_prefix(value) for value in raw["allowed_paths"])
    denied = tuple(normalize_prefix(value) for value in raw["denied_paths"])
    for denied_prefix in denied:
        if not any(
            path_is_within(denied_prefix, allowed_prefix)
            for allowed_prefix in allowed
        ):
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"job {raw['id']} denied path is outside its allowlist",
            )
        if any(
            path_is_within(allowed_prefix, denied_prefix)
            for allowed_prefix in allowed
        ):
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"job {raw['id']} denied path blocks an entire allowed prefix",
            )
    return JobSpec(
        id=raw["id"],
        role=raw["role"],
        goal=raw["goal"],
        allowed_paths=allowed,
        denied_paths=denied,
        acceptance_criteria=tuple(raw["acceptance_criteria"]),
        verification_commands=parse_commands(
            raw["verification_commands"], f"job {raw['id']}"
        ),
        timeout_seconds=raw["timeout_seconds"],
    )


def load_manifest(path: Path) -> Manifest:
    policy = load_policy()
    if path.stat().st_size > policy.max_manifest_bytes:
        raise PilotfishError("PRECHECK_FAILED", "manifest byte limit exceeded")
    raw = load_json(path)
    validate_schema(raw, SKILL_ROOT / "schemas" / "job.schema.json")
    jobs = tuple(parse_job(value) for value in raw["jobs"])
    if len({job.id for job in jobs}) != len(jobs):
        raise PilotfishError("PRECHECK_FAILED", "duplicate job ids")
    reserved_job_ids = sorted(
        job.id for job in jobs if job.id in RESERVED_JOB_IDS
    )
    if reserved_job_ids:
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"job ids are reserved for internal use: {reserved_job_ids}",
        )
    if not any(job.role == "executor" for job in jobs):
        raise PilotfishError("PRECHECK_FAILED", "at least one executor is required")
    manifest = Manifest(
        schema_version=raw["schema_version"],
        run_id=raw["run_id"],
        task_requirement=raw["task_requirement"],
        completion_claim=raw["completion_claim"],
        overall_acceptance_criteria=tuple(raw["overall_acceptance_criteria"]),
        repo_root=Path(raw["repo_root"]).expanduser().resolve(),
        base_branch=raw["base_branch"],
        base_sha=raw["base_sha"],
        max_parallel=raw["max_parallel"],
        integration_verification_commands=parse_commands(
            raw["integration_verification_commands"], "integration"
        ),
        jobs=jobs,
    )
    validate_disjoint_writer_paths(jobs)
    return manifest


def load_roles(path: Path | None = None) -> dict[str, RoleConfig]:
    config_path = path or SKILL_ROOT / "config" / "roles.toml"
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw["roles"]) != set(APPROVED_ROLE_EXECUTION_CONTRACTS):
        raise PilotfishError(
            "PRECHECK_FAILED",
            "roles.toml must define scout, executor, verifier exactly",
        )
    roles: dict[str, RoleConfig] = {}
    for name, value in raw["roles"].items():
        observed_contract = (
            value["model"],
            value["effort"],
            value["sandbox"],
        )
        if observed_contract != APPROVED_ROLE_EXECUTION_CONTRACTS[name]:
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"role {name} execution contract drift",
            )
        prompt_path = SKILL_ROOT / value["prompt"]
        if not prompt_path.is_file():
            raise PilotfishError(
                "PRECHECK_FAILED", f"missing role prompt: {prompt_path}"
            )
        success_statuses = tuple(value["success_statuses"])
        terminal_statuses = tuple(value["terminal_statuses"])
        if not set(success_statuses).issubset(terminal_statuses):
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"role {name} success status is not terminal for that role",
            )
        roles[name] = RoleConfig(
            name=name,
            model=value["model"],
            effort=value["effort"],
            sandbox=value["sandbox"],
            prompt_path=prompt_path,
            success_statuses=success_statuses,
            terminal_statuses=terminal_statuses,
        )
    if set(roles) != {"scout", "executor", "verifier"}:
        raise PilotfishError(
            "PRECHECK_FAILED",
            "roles.toml must define scout, executor, verifier exactly",
        )
    return roles


def load_policy(path: Path | None = None) -> InvocationPolicy:
    config_path = path or SKILL_ROOT / "config" / "roles.toml"
    with config_path.open("rb") as handle:
        value = tomllib.load(handle)["policy"]
    if (
        value["max_parallel_hard_cap"] != 3
        or value["approval_policy"] != "never"
        or value["v2_session_thread_cap"] != 1
    ):
        raise PilotfishError("PRECHECK_FAILED", "roles.toml invariant policy drift")
    return InvocationPolicy(
        max_parallel_hard_cap=value["max_parallel_hard_cap"],
        approval_policy=value["approval_policy"],
        v2_session_thread_cap=value["v2_session_thread_cap"],
        final_verifier_timeout_seconds=value["final_verifier_timeout_seconds"],
        max_patch_bytes=value["max_patch_bytes"],
        max_changed_files=value["max_changed_files"],
        max_event_log_bytes=value["max_event_log_bytes"],
        max_result_bytes=value["max_result_bytes"],
        max_manifest_bytes=value["max_manifest_bytes"],
        max_prompt_bytes=value["max_prompt_bytes"],
        max_command_output_bytes=value["max_command_output_bytes"],
        forbidden_executables=tuple(value["forbidden_executables"]),
        forbidden_argv_tokens=tuple(value["forbidden_argv_tokens"]),
    )


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def validate_codex_prefix(codex_prefix: Sequence[str]) -> tuple[str, ...]:
    if (
        not isinstance(codex_prefix, tuple)
        or not codex_prefix
        or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in codex_prefix
        )
    ):
        raise PilotfishError(
            "PRECHECK_FAILED",
            "Codex prefix must be a non-empty argv tuple",
        )
    return codex_prefix


def build_worker_command(
    codex_prefix: Sequence[str],
    role: RoleConfig,
    worktree: Path,
    final_path: Path,
    policy: InvocationPolicy,
) -> list[str]:
    prefix = validate_codex_prefix(codex_prefix)
    project_override = (
        "projects={"
        + toml_string(str(worktree))
        + '={trust_level="untrusted"}}'
    )
    developer_text = role.prompt_path.read_text(encoding="utf-8")
    overrides = (
        f"model={toml_string(role.model)}",
        f"model_reasoning_effort={toml_string(role.effort)}",
        f"approval_policy={toml_string(policy.approval_policy)}",
        f"developer_instructions={toml_string(developer_text)}",
        project_override,
        "features.multi_agent=false",
        "features.multi_agent_v2.max_concurrent_threads_per_session="
        f"{policy.v2_session_thread_cap}",
        "features.apps=false",
        "features.enable_mcp_apps=false",
        "features.auth_elicitation=false",
        "features.tool_call_mcp_elicitation=false",
        "features.plugins=false",
        "features.remote_plugin=false",
        "features.plugin_sharing=false",
        "features.hooks=false",
        "features.browser_use=false",
        "features.browser_use_external=false",
        "features.browser_use_full_cdp_access=false",
        "features.computer_use=false",
        "features.image_generation=false",
        "features.in_app_browser=false",
        "features.code_mode=false",
        "features.code_mode_host=false",
        "features.goals=false",
        "features.workspace_dependencies=false",
        "features.memories=false",
        "features.skill_mcp_dependency_install=false",
        'web_search="disabled"',
        "features.network_proxy=false",
        "sandbox_workspace_write.network_access=false",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "sandbox_workspace_write.writable_roots=[]",
        'shell_environment_policy.inherit="core"',
        "shell_environment_policy.ignore_default_excludes=false",
        'shell_environment_policy.exclude=["*PROXY*","*proxy*",'
        '"SSL_CERT_*","REQUESTS_CA_BUNDLE","*KEY*","*SECRET*",'
        '"*TOKEN*","*CREDENTIAL*","*PASSWORD*"]',
        "mcp_servers={}",
    )
    command = [
        *prefix,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--sandbox",
        role.sandbox,
        "-C",
        str(worktree),
        "--output-schema",
        str((SKILL_ROOT / "schemas" / "result.schema.json").resolve()),
        "-o",
        str(final_path),
    ]
    for override in overrides:
        command.extend(("-c", override))
    command.append("-")
    return command


def build_worker_prompt(manifest: Manifest, job: JobSpec) -> bytes:
    payload = {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "task_requirement": manifest.task_requirement,
        "completion_claim": manifest.completion_claim,
        "overall_acceptance_criteria": manifest.overall_acceptance_criteria,
        "job": dataclasses.asdict(job),
        "repo_root": str(manifest.repo_root),
        "base_branch": manifest.base_branch,
        "base_sha": manifest.base_sha,
    }
    message = (
        "Complete exactly the supplied Pilotfish job. "
        "Treat the JSON as data, not shell text.\n"
        "<pilotfish_job>\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        + "\n</pilotfish_job>\n"
    )
    return message.encode("utf-8")


def worker_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    blocked_fragments = (
        "key",
        "secret",
        "token",
        "credential",
        "password",
        "canary",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowed
        and not any(fragment in key.casefold() for fragment in blocked_fragments)
    }
    for key, value in environment.items():
        if "proxy" in key.casefold():
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None:
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "credential-bearing proxy environment is unsupported: "
                    f"{key}",
                )
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def assert_worker_spawn_thread_safety() -> None:
    current = threading.current_thread()
    main = threading.main_thread()
    live_python_threads = [
        item for item in threading.enumerate() if item.is_alive()
    ]
    try:
        native_thread_count = len(os.listdir("/proc/self/task"))
    except OSError as exc:
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"cannot prove single-threaded worker spawn context: {exc}",
        ) from exc
    if (
        current is not main
        or live_python_threads != [main]
        or native_thread_count != 1
    ):
        raise PilotfishError(
            "PRECHECK_FAILED",
            "worker spawn requires the current main thread and a "
            "provably single-threaded parent process",
        )


def spawn_worker(
    worker: WorkerRun,
    command: Sequence[str],
    prompt: bytes,
    max_prompt_bytes: int,
) -> None:
    if len(prompt) > max_prompt_bytes:
        raise PilotfishError(
            "PRECHECK_FAILED", "worker prompt exceeds configured limit"
        )
    if worker.process is not None or worker.status != "CREATED":
        raise PilotfishError(
            "PRECHECK_FAILED", f"worker is not spawnable: {worker.job.id}"
        )
    worker.events_path.parent.mkdir(parents=True, exist_ok=True)
    worker.stderr_path.parent.mkdir(parents=True, exist_ok=True)
    worker.final_path.parent.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen[bytes] | None = None
    try:
        with (
            worker.events_path.open("wb") as events_handle,
            worker.stderr_path.open("wb") as stderr_handle,
            tempfile.TemporaryFile(
                mode="w+b", dir=worker.events_path.parent
            ) as prompt_handle,
        ):
            prompt_handle.write(prompt)
            prompt_handle.flush()
            prompt_handle.seek(0)
            assert_worker_spawn_thread_safety()
            publication_signals = {signal.SIGINT, signal.SIGTERM}
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, publication_signals
            )

            def restore_child_signal_mask() -> None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

            try:
                process = subprocess.Popen(
                    list(command),
                    stdin=prompt_handle,
                    stdout=events_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                    close_fds=True,
                    env=worker_environment(),
                    preexec_fn=restore_child_signal_mask,
                )
                worker.process = process
                worker.invocation_argv = tuple(command)
                worker.started_monotonic = time.monotonic()
                worker.status = "PARALLEL_RUNNING"
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    except BaseException as original_error:
        if process is not None:
            cleanup_error: BaseException | None = None
            try:
                terminate_process_group(process, 0.5)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                worker.finished_monotonic = time.monotonic()
                worker.exit_code = process.returncode
                worker.status = "WORKER_FAILED"
            if cleanup_error is not None:
                raise WorkerSpawnError(
                    worker.job.id,
                    process,
                    original_error,
                    cleanup_error,
                ) from cleanup_error
        raise


def process_group_exists_os(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def signal_process_group(pgid: int, item: signal.Signals) -> None:
    try:
        os.killpg(pgid, item)
    except ProcessLookupError:
        pass


def terminate_process_group(
    process: subprocess.Popen[bytes], grace_seconds: float
) -> None:
    pgid = process.pid
    if process_group_exists_os(pgid):
        signal_process_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0.0)
    while process_group_exists_os(pgid) and time.monotonic() < deadline:
        process.poll()
        time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
    if process_group_exists_os(pgid):
        signal_process_group(pgid, signal.SIGKILL)
    try:
        process.wait(timeout=max(grace_seconds, 0.1))
    except subprocess.TimeoutExpired as exc:
        raise PilotfishError(
            "WORKER_FAILED", f"process group {pgid} could not be reaped"
        ) from exc
    deadline = time.monotonic() + max(grace_seconds, 0.1)
    while process_group_exists_os(pgid) and time.monotonic() < deadline:
        time.sleep(0.01)
    if process_group_exists_os(pgid):
        raise PilotfishError(
            "WORKER_FAILED", f"orphan process group remains: {pgid}"
        )


_ACTIVE_CANCELLATION: "CancellationController | None" = None


class CancellationController(AbstractContextManager["CancellationController"]):
    def __init__(self) -> None:
        self.received: set[signal.Signals] = set()
        self.previous: dict[signal.Signals, Any] = {}

    def __enter__(self) -> "CancellationController":
        global _ACTIVE_CANCELLATION
        if _ACTIVE_CANCELLATION is not None:
            raise PilotfishError(
                "PRECHECK_FAILED", "nested cancellation controller"
            )
        _ACTIVE_CANCELLATION = self
        try:
            for item in (signal.SIGINT, signal.SIGTERM):
                self.previous[item] = signal.getsignal(item)
                signal.signal(item, self._record)
        except BaseException:
            for item, previous in self.previous.items():
                signal.signal(item, previous)
            _ACTIVE_CANCELLATION = None
            raise
        return self

    def _record(self, signum: int, _frame: object) -> None:
        self.received.add(signal.Signals(signum))

    def checkpoint(self) -> None:
        if self.received:
            names = ",".join(
                item.name for item in sorted(self.received, key=int)
            )
            raise PilotfishError("CANCELLED", f"run received {names}")

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        global _ACTIVE_CANCELLATION
        for item, previous in self.previous.items():
            signal.signal(item, previous)
        _ACTIVE_CANCELLATION = None


class DeferredSignals(AbstractContextManager["DeferredSignals"]):
    SIGNALS = {signal.SIGINT, signal.SIGTERM}

    def __init__(self, cancellation: CancellationController) -> None:
        self.cancellation = cancellation
        self.previous_mask: set[signal.Signals] | None = None
        self.received: set[signal.Signals] = set()

    def __enter__(self) -> "DeferredSignals":
        if threading.current_thread() is not threading.main_thread():
            raise PilotfishError(
                "PRECHECK_FAILED",
                "deferred signal section must run on the main thread",
            )
        if _ACTIVE_CANCELLATION is not self.cancellation:
            raise PilotfishError(
                "PRECHECK_FAILED",
                "deferred signal section requires the active controller",
            )
        self.previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, self.SIGNALS
        )
        kernel_pending = self.pending()
        observed = set(self.cancellation.received) | kernel_pending
        if observed:
            self.received.update(observed)
            for pending_signal in sorted(kernel_pending, key=int):
                signal.sigwait({pending_signal})
            signal.pthread_sigmask(
                signal.SIG_SETMASK, self.previous_mask
            )
            self.previous_mask = None
            names = ",".join(
                item.name for item in sorted(observed, key=int)
            )
            raise PilotfishError(
                "CANCELLED",
                f"run received {names} before final apply",
            )
        return self

    def pending(self) -> set[signal.Signals]:
        return {
            signal.Signals(item)
            for item in signal.sigpending()
            if item in self.SIGNALS
        }

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        if self.previous_mask is None:
            return
        kernel_pending = self.pending()
        for pending_signal in sorted(kernel_pending, key=int):
            signal.sigwait({pending_signal})
            self.received.add(pending_signal)
        signal.pthread_sigmask(signal.SIG_SETMASK, self.previous_mask)
        self.previous_mask = None


def cancellation_checkpoint() -> None:
    if _ACTIVE_CANCELLATION is not None:
        _ACTIVE_CANCELLATION.checkpoint()


def monitor_workers(
    workers: Sequence[WorkerRun],
    poll_interval: float = 0.1,
    terminate_grace_seconds: float = 2.0,
) -> None:
    pending = {worker.job.id: worker for worker in workers}
    if len(pending) != len(workers):
        raise PilotfishError("PRECHECK_FAILED", "duplicate worker job ids")
    try:
        while pending:
            cancellation_checkpoint()
            now = time.monotonic()
            for job_id, worker in list(pending.items()):
                if worker.process is None:
                    raise PilotfishError(
                        "WORKER_FAILED", f"worker {job_id} has no process"
                    )
                returncode = worker.process.poll()
                if returncode is not None:
                    leaked_group = process_group_exists_os(worker.process.pid)
                    if leaked_group:
                        terminate_process_group(
                            worker.process, terminate_grace_seconds
                        )
                    worker.finished_monotonic = now
                    worker.exit_code = worker.process.returncode
                    worker.status = (
                        "DONE"
                        if returncode == 0 and not leaked_group
                        else "WORKER_FAILED"
                    )
                    pending.pop(job_id)
                    continue
                if now - worker.started_monotonic > worker.job.timeout_seconds:
                    terminate_process_group(
                        worker.process, terminate_grace_seconds
                    )
                    worker.finished_monotonic = time.monotonic()
                    worker.exit_code = worker.process.returncode
                    worker.status = "WORKER_FAILED"
                    pending.pop(job_id)
            if pending:
                time.sleep(poll_interval)
    except BaseException as exc:
        for worker in pending.values():
            if worker.process is not None:
                terminate_process_group(
                    worker.process, terminate_grace_seconds
                )
                worker.exit_code = worker.process.returncode
            worker.finished_monotonic = time.monotonic()
            worker.status = "CANCELLED"
        if isinstance(exc, PilotfishError):
            raise
        raise PilotfishError("CANCELLED", "run cancelled by user") from exc


def terminate_and_wait_live_workers(
    workers: Sequence[WorkerRun],
    terminate_grace_seconds: float = 2.0,
) -> None:
    errors: list[str] = []
    for worker in workers:
        if worker.process is None:
            continue
        if process_group_exists_os(worker.process.pid):
            try:
                terminate_process_group(
                    worker.process, terminate_grace_seconds
                )
            except PilotfishError as exc:
                errors.append(f"{worker.job.id}:{exc}")
        else:
            worker.process.wait()
        worker.exit_code = worker.process.returncode
        worker.finished_monotonic = time.monotonic()
        worker.status = "CANCELLED"
    if errors:
        raise PilotfishError(
            "WORKER_FAILED", "failed to reap workers: " + "; ".join(errors)
        )


def run_bounded_workers(
    workers: Sequence[WorkerRun],
    manifest: Manifest,
    codex_prefix: Sequence[str],
    policy: InvocationPolicy,
    *,
    state: dict[str, Any] | None = None,
    layout: Any = None,
    poll_interval: float = 0.1,
    terminate_grace_seconds: float = 2.0,
) -> dict[str, Any]:
    prefix = validate_codex_prefix(codex_prefix)
    if not 1 <= manifest.max_parallel <= policy.max_parallel_hard_cap:
        raise PilotfishError(
            "PRECHECK_FAILED", "manifest parallelism is outside policy"
        )
    queued = collections.deque(sorted(workers, key=lambda item: item.job.id))
    active: dict[str, WorkerRun] = {}
    failure: str | None = None
    peak_active = 0
    spawning: WorkerRun | None = None
    try:
        while queued or active:
            cancellation_checkpoint()
            while (
                queued
                and failure is None
                and len(active) < manifest.max_parallel
            ):
                spawning = queued.popleft()
                command = build_worker_command(
                    prefix,
                    spawning.role,
                    spawning.worktree,
                    spawning.final_path,
                    policy,
                )
                prompt = build_worker_prompt(manifest, spawning.job)
                if len(prompt) > policy.max_prompt_bytes:
                    raise PilotfishError(
                        "PRECHECK_FAILED",
                        "worker prompt byte limit exceeded: "
                        f"{spawning.job.id}",
                    )
                spawn_worker(
                    spawning, command, prompt, policy.max_prompt_bytes
                )
                active[spawning.job.id] = spawning
                peak_active = max(peak_active, len(active))
                spawning = None
                if state is not None and layout is not None:
                    refresh_owned_state_records(
                        state, layout, workers=workers
                    )
                    persist_state(layout, state)
            now = time.monotonic()
            for job_id, worker in list(active.items()):
                assert worker.process is not None
                forced_failure: str | None = None
                if (
                    worker.events_path.exists()
                    and worker.events_path.stat().st_size
                    > policy.max_event_log_bytes
                ):
                    forced_failure = f"event-log-limit:{job_id}"
                    failure = failure or forced_failure
                    terminate_process_group(
                        worker.process, terminate_grace_seconds
                    )
                returncode = worker.process.poll()
                if (
                    returncode is None
                    and now - worker.started_monotonic
                    > worker.job.timeout_seconds
                ):
                    forced_failure = f"timeout:{job_id}"
                    failure = failure or forced_failure
                    terminate_process_group(
                        worker.process, terminate_grace_seconds
                    )
                    returncode = worker.process.returncode
                if returncode is None:
                    continue
                leaked_group = process_group_exists_os(worker.process.pid)
                if leaked_group:
                    forced_failure = f"orphan-process-group:{job_id}"
                    failure = failure or forced_failure
                    terminate_process_group(
                        worker.process, terminate_grace_seconds
                    )
                    returncode = worker.process.returncode
                worker.finished_monotonic = time.monotonic()
                worker.exit_code = returncode
                worker.status = (
                    "DONE"
                    if returncode == 0 and forced_failure is None
                    else "WORKER_FAILED"
                )
                if worker.status == "WORKER_FAILED":
                    failure = failure or f"exit:{job_id}:{returncode}"
                active.pop(job_id)
            if failure is not None and queued:
                for worker in queued:
                    worker.status = "CANCELLED"
                queued.clear()
            if active or queued:
                time.sleep(poll_interval)
    except BaseException as run_error:
        if spawning is not None and spawning.process is None:
            spawning.finished_monotonic = time.monotonic()
            spawning.status = "WORKER_FAILED"
        cleanup_errors: list[str] = []
        recovered_process = (
            run_error.process
            if isinstance(run_error, WorkerSpawnError)
            else None
        )
        published_process = (
            spawning.process if spawning is not None else None
        )
        if (
            recovered_process is not None
            and recovered_process is not published_process
        ):
            try:
                if process_group_exists_os(recovered_process.pid):
                    terminate_process_group(
                        recovered_process, terminate_grace_seconds
                    )
                else:
                    try:
                        recovered_process.wait(
                            timeout=max(terminate_grace_seconds, 0.1)
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise PilotfishError(
                            "WORKER_FAILED",
                            "unpublished spawn process could not be reaped: "
                            f"{recovered_process.pid}",
                        ) from exc
            except BaseException as exc:
                cleanup_errors.append(f"unpublished-spawn:{exc}")
            finally:
                if spawning is not None:
                    spawning.finished_monotonic = time.monotonic()
                    spawning.exit_code = recovered_process.returncode
                    spawning.status = "WORKER_FAILED"
        cleanup_workers = list(active.values())
        if spawning is not None and spawning.process is not None:
            cleanup_workers.append(spawning)
        try:
            try:
                terminate_and_wait_live_workers(
                    tuple(cleanup_workers), terminate_grace_seconds
                )
            except BaseException as exc:
                cleanup_errors.append(f"published-workers:{exc}")
        finally:
            for worker in queued:
                worker.status = "CANCELLED"
        if cleanup_errors:
            raise PilotfishError(
                "WORKER_FAILED",
                "worker cleanup failed: " + "; ".join(cleanup_errors),
                {"run_error": f"{type(run_error).__name__}: {run_error}"},
            ) from run_error
        raise
    if failure is not None:
        raise PilotfishError("WORKER_FAILED", failure)
    return {
        "peak_active": peak_active,
        "max_parallel": manifest.max_parallel,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PilotfishError(
            "QUARANTINED", f"cannot read worker JSONL {path}: {exc}"
        ) from exc
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PilotfishError(
                "QUARANTINED", f"invalid JSONL line {number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise PilotfishError(
                "QUARANTINED", f"JSONL line {number} is not an object"
            )
        events.append(value)
    if not any(event.get("type") == "thread.started" for event in events):
        raise PilotfishError("QUARANTINED", "JSONL lacks thread.started")
    if not any(event.get("type") == "turn.completed" for event in events):
        raise PilotfishError("QUARANTINED", "JSONL lacks turn.completed")
    if any(event.get("type") in {"turn.failed", "error"} for event in events):
        raise PilotfishError(
            "WORKER_FAILED", "JSONL contains a failure event"
        )
    return events


def attest_invocation(
    worker: WorkerRun,
    policy: InvocationPolicy | None = None,
) -> dict[str, str]:
    argv = worker.invocation_argv
    try:
        exec_index = argv.index("exec")
    except ValueError as exc:
        raise PilotfishError(
            "QUARANTINED", "worker invocation lacks exec"
        ) from exc
    prefix = argv[:exec_index]
    try:
        expected = tuple(
            build_worker_command(
                prefix,
                worker.role,
                worker.worktree,
                worker.final_path,
                policy or load_policy(),
            )
        )
    except (OSError, PilotfishError) as exc:
        raise PilotfishError(
            "QUARANTINED", f"worker invocation cannot be attested: {exc}"
        ) from exc
    if argv != expected:
        raise PilotfishError(
            "QUARANTINED", "worker invocation exact argv attestation mismatch"
        )
    return {
        "model": worker.role.model,
        "effort": worker.role.effort,
        "sandbox": worker.role.sandbox,
        "evidence": (
            "configured/strict-attested: exact argv + --strict-config "
            "accepted + exit 0"
        ),
    }


def worker_artifact_size(path: Path, label: str, job_id: str) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED",
            f"worker {label} is unavailable for {job_id}: {exc}",
        ) from exc


def validate_worker_result(
    worker: WorkerRun,
    manifest: Manifest,
    expected_head_sha: str,
    policy: InvocationPolicy,
) -> dict[str, Any]:
    if (
        worker.process is None
        or worker.exit_code != 0
        or worker.status != "DONE"
    ):
        raise PilotfishError(
            "WORKER_FAILED",
            f"worker process did not exit cleanly: {worker.job.id}",
        )
    if (
        worker_artifact_size(
            worker.events_path, "event log", worker.job.id
        )
        > policy.max_event_log_bytes
    ):
        raise PilotfishError(
            "QUARANTINED",
            f"worker event log exceeds limit: {worker.job.id}",
        )
    if (
        worker_artifact_size(worker.final_path, "result", worker.job.id)
        > policy.max_result_bytes
    ):
        raise PilotfishError(
            "QUARANTINED",
            f"worker result exceeds limit: {worker.job.id}",
        )
    events = read_jsonl(worker.events_path)
    try:
        raw = load_json(worker.final_path)
        validate_schema(raw, SKILL_ROOT / "schemas" / "result.schema.json")
    except PilotfishError as exc:
        raise PilotfishError(
            "QUARANTINED", f"worker result failed validation: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PilotfishError("QUARANTINED", "worker result is not an object")
    expected = {
        "run_id": manifest.run_id,
        "job_id": worker.job.id,
        "role": worker.role.name,
        "base_sha": manifest.base_sha,
        "worktree_head_sha": expected_head_sha,
    }
    for key, value in expected.items():
        if raw[key] != value:
            raise PilotfishError(
                "QUARANTINED",
                f"result {key} mismatch: {raw[key]!r} != {value!r}",
            )
    observed = attest_invocation(worker, policy)
    if raw["status"] not in worker.role.terminal_statuses:
        raise PilotfishError(
            "QUARANTINED",
            f"worker returned invalid terminal status: {raw['status']}",
        )
    if worker.role.name != "executor" and raw["changed_paths"]:
        raise PilotfishError(
            "QUARANTINED", "read-only worker reported changed paths"
        )
    claimed = raw["commands"]
    expected_commands = worker.job.verification_commands
    if len(claimed) != len(expected_commands):
        raise PilotfishError(
            "QUARANTINED", "worker command count mismatch"
        )
    for claim, expected_command in zip(
        claimed, expected_commands, strict=True
    ):
        if (
            claim["id"] != expected_command.id
            or tuple(claim["argv"]) != expected_command.argv
            or claim["exit_code"] != 0
        ):
            raise PilotfishError(
                "QUARANTINED", "worker command evidence mismatch"
            )
    completed = [
        event for event in events if event.get("type") == "turn.completed"
    ][-1]
    usage = completed.get("usage", {})
    if not isinstance(usage, dict):
        raise PilotfishError(
            "QUARANTINED", "turn.completed usage is not an object"
        )
    raw["usage"] = usage
    thread_events = [
        event for event in events if event.get("type") == "thread.started"
    ]
    thread_id = thread_events[0].get("thread_id")
    worker.thread_id = thread_id if isinstance(thread_id, str) else None
    worker.runtime_metadata = observed
    worker.validated_result = raw
    return raw


def git_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR"}
    environment = {
        key: value for key, value in os.environ.items() if key in allowed
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS="/bin/false",
    )
    if extra:
        forbidden = set(extra) - {"GIT_INDEX_FILE"}
        if forbidden:
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"unsupported Git environment keys: {sorted(forbidden)}",
            )
        environment.update(extra)
    return environment


def git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
    error_state: str = "PRECHECK_FAILED",
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", str(repo), "-c", "core.fsmonitor=false", *args]
    completed = subprocess.run(
        command,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_environment(env),
        check=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PilotfishError(
            error_state,
            f"git command failed {command!r}: {message}",
        )
    return completed


def git_text(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    error_state: str = "PRECHECK_FAILED",
) -> str:
    return (
        git(repo, *args, env=env, error_state=error_state)
        .stdout.decode("utf-8", errors="strict")
        .strip()
    )


def assert_no_git_operation(
    git_dir: Path,
    state: str = "PRECHECK_FAILED",
) -> None:
    operation_markers = (
        git_dir / "MERGE_HEAD",
        git_dir / "CHERRY_PICK_HEAD",
        git_dir / "REVERT_HEAD",
        git_dir / "BISECT_LOG",
        git_dir / "rebase-apply",
        git_dir / "rebase-merge",
    )
    if any(os.path.lexists(path) for path in operation_markers):
        raise PilotfishError(state, "repository has an active Git operation")


def reject_hidden_index_flags(
    root: Path,
    state: str = "PRECHECK_FAILED",
) -> None:
    index_flags = git(root, "ls-files", "-v", "-z").stdout
    if any(
        record.startswith((b"h ", b"S ", b"s "))
        for record in index_flags.split(b"\0")
        if record
    ):
        raise PilotfishError(
            state,
            "assume-unchanged/skip-worktree index flags are unsupported",
        )


def preflight_repo(manifest: Manifest) -> RepoBaseline:
    root_text = git_text(manifest.repo_root, "rev-parse", "--show-toplevel")
    root = Path(root_text).resolve()
    if root != manifest.repo_root:
        raise PilotfishError(
            "PRECHECK_FAILED", f"repo_root must be top-level: {root}"
        )

    git_dir = Path(
        git_text(root, "rev-parse", "--absolute-git-dir")
    ).resolve()
    common_dir_raw = git_text(root, "rev-parse", "--git-common-dir")
    common_dir_path = Path(common_dir_raw)
    common_dir = (
        common_dir_path.resolve()
        if common_dir_path.is_absolute()
        else (root / common_dir_path).resolve()
    )
    try:
        common_dir_stat = os.stat(common_dir, follow_symlinks=False)
    except OSError as exc:
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"cannot inspect repository common directory: {exc}",
        ) from exc
    if not stat.S_ISDIR(common_dir_stat.st_mode):
        raise PilotfishError(
            "PRECHECK_FAILED", "repository common directory is not a directory"
        )
    common_dir_device_inode = (
        common_dir_stat.st_dev,
        common_dir_stat.st_ino,
    )
    assert_no_git_operation(git_dir)

    branch = git_text(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != manifest.base_branch:
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"branch mismatch: {branch} != {manifest.base_branch}",
        )
    base_sha = git_text(root, "rev-parse", "HEAD")
    if base_sha != manifest.base_sha:
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"base SHA mismatch: {base_sha} != {manifest.base_sha}",
        )
    sparse = git(
        root,
        "config",
        "--bool",
        "core.sparseCheckout",
        check=False,
    ).stdout.strip()
    if sparse == b"true":
        raise PilotfishError(
            "PRECHECK_FAILED", "sparse checkout is unsupported"
        )
    for key in ("core.fsmonitor", "diff.external", "core.attributesFile"):
        configured = git(
            root,
            "config",
            "--local",
            "--get",
            key,
            check=False,
        ).stdout.strip()
        if configured:
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"unsafe local Git config is unsupported: {key}",
            )
    for key in (
        "commit.gpgSign",
        "tag.gpgSign",
        "tag.forceSignAnnotated",
        "user.signingKey",
        "gpg.format",
        "gpg.program",
        "gpg.ssh.defaultKeyCommand",
    ):
        configured = git(
            root,
            "config",
            "--local",
            "--get",
            key,
            check=False,
        ).stdout.strip()
        if configured:
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"unsafe signing Git config is unsupported: {key}",
            )
    signing_programs = git(
        root,
        "config",
        "--local",
        "--get-regexp",
        r"^gpg\..*\.program$",
        check=False,
    )
    if signing_programs.returncode == 0 and signing_programs.stdout.strip():
        raise PilotfishError(
            "PRECHECK_FAILED",
            "unsafe signing Git config programs are unsupported",
        )
    dangerous_config = git(
        root,
        "config",
        "--local",
        "--get-regexp",
        r"^(filter\..*\.(clean|smudge|process)|merge\..*\.driver)$",
        check=False,
    )
    if dangerous_config.returncode == 0 and dangerous_config.stdout.strip():
        raise PilotfishError(
            "PRECHECK_FAILED",
            "custom Git filters/merge drivers are unsupported",
        )
    reject_hidden_index_flags(root)
    stage = git(root, "ls-files", "--stage", "-z").stdout
    if any(
        record.startswith(b"160000 ")
        for record in stage.split(b"\0")
        if record
    ):
        raise PilotfishError(
            "PRECHECK_FAILED", "submodule/gitlink entries are unsupported"
        )
    status_output = git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout
    if status_output:
        raise PilotfishError(
            "PRECHECK_FAILED", "working tree and index must be clean"
        )
    base_tree = git_text(root, "rev-parse", "HEAD^{tree}")
    index_tree = git_text(root, "write-tree")
    return RepoBaseline(
        root,
        branch,
        base_sha,
        base_tree,
        git_dir,
        common_dir,
        common_dir_device_inode,
        index_tree,
    )


def repo_id(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


def create_layout(manifest: Manifest) -> RunLayout:
    root = (
        Path("/tmp")
        / "pilotfish-parallel"
        / repo_id(manifest.repo_root)
        / manifest.run_id
    )
    if root.exists():
        raise PilotfishError(
            "PRECHECK_FAILED", f"run directory already exists: {root}"
        )
    worktrees = root / "worktrees"
    verification_repos = root / "verification-repos"
    artifacts = root / "artifacts"
    rollback = root / "rollback"
    for path in (worktrees, verification_repos, artifacts):
        path.mkdir(parents=True, exist_ok=False)
    return RunLayout(
        root=root,
        worktrees=worktrees,
        verification_repos=verification_repos,
        artifacts=artifacts,
        rollback=rollback,
        state_path=root / "run-state.json",
        integration_worktree=worktrees / "integration",
        integration_branch=f"pf/{manifest.run_id}/integration",
    )


def is_within_resolved(path: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return path == resolved_path and root == resolved_root


def reject_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while True:
        if os.path.lexists(current):
            try:
                mode = current.lstat().st_mode
            except OSError as exc:
                raise PilotfishError(
                    "QUARANTINED",
                    f"cannot inspect owned path component: {current}: {exc}",
                ) from exc
            if stat.S_ISLNK(mode):
                raise PilotfishError(
                    "QUARANTINED", f"symlink in owned path: {current}"
                )
        if current == stop:
            return
        parent = current.parent
        if parent == current:
            raise PilotfishError(
                "QUARANTINED", f"owned path escapes layout: {path}"
            )
        current = parent


def layout_from_state(state: dict[str, Any]) -> RunLayout:
    if not isinstance(state, dict):
        raise PilotfishError("QUARANTINED", "run-state must be an object")
    root_raw = state.get("layout_root")
    run_id = state.get("run_id")
    if not isinstance(root_raw, str) or not isinstance(run_id, str):
        raise PilotfishError(
            "QUARANTINED", "run-state layout identity is invalid"
        )
    root = Path(root_raw)
    worktrees = root / "worktrees"
    return RunLayout(
        root=root,
        worktrees=worktrees,
        verification_repos=root / "verification-repos",
        artifacts=root / "artifacts",
        rollback=root / "rollback",
        state_path=root / "run-state.json",
        integration_worktree=worktrees / "integration",
        integration_branch=f"pf/{run_id}/integration",
    )


def _is_plain_number(value: Any) -> bool:
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def _validate_json_value(value: Any, label: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PilotfishError(
            "QUARANTINED", f"run-state {label} is not strict JSON"
        ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_bounded_regular_json(
    path: Path, maximum: int, label: str
) -> Any:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED", f"{label} is unavailable: {exc}"
        ) from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise PilotfishError(
            "QUARANTINED", f"{label} is not a regular file"
        )
    if path_stat.st_size > maximum:
        raise PilotfishError(
            "QUARANTINED", f"{label} byte limit exceeded"
        )
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED", f"cannot safely open {label}: {exc}"
        ) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
            or opened_stat.st_size > maximum
        ):
            raise PilotfishError(
                "QUARANTINED", f"{label} inode identity or size changed"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > maximum:
            raise PilotfishError(
                "QUARANTINED", f"{label} byte limit exceeded"
            )
    finally:
        os.close(descriptor)
    try:
        return json.loads(
            raw.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except UnicodeDecodeError as exc:
        raise PilotfishError(
            "QUARANTINED", f"cannot decode {label}: {exc}"
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if "constant" in str(exc):
            raise PilotfishError(
                "QUARANTINED", f"{label} contains a non-finite JSON constant"
            ) from exc
        raise PilotfishError(
            "QUARANTINED", f"cannot decode {label}: {exc}"
        ) from exc


def _validate_owned_paths(
    values: Any, label: str, layout: RunLayout
) -> list[str]:
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise PilotfishError(
            "QUARANTINED", f"run-state {label} must be a string list"
        )
    if len(values) != len(set(values)):
        raise PilotfishError("QUARANTINED", f"duplicate {label}")
    for raw in values:
        path = Path(raw)
        if not path.is_absolute() or str(path) != raw:
            raise PilotfishError(
                "QUARANTINED", f"owned path escapes layout: {path}"
            )
        reject_symlink_components(path, layout.root)
        if not is_within_resolved(path, layout.root):
            raise PilotfishError(
                "QUARANTINED", f"owned path escapes layout: {path}"
            )
    return values


def _validate_worktree_records(
    records: Any, layout: RunLayout, run_id: str
) -> tuple[set[str], set[str]]:
    if not isinstance(records, list):
        raise PilotfishError(
            "QUARANTINED", "run-state worktrees must be a list"
        )
    paths: set[str] = set()
    refs: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != WORKTREE_RECORD_KEYS:
            raise PilotfishError(
                "QUARANTINED", "invalid run-state worktree record"
            )
        path_raw = record["path"]
        if not isinstance(path_raw, str):
            raise PilotfishError(
                "QUARANTINED", "invalid run-state worktree path"
            )
        path = Path(path_raw)
        if path_raw in paths or not path.is_absolute() or str(path) != path_raw:
            reason = "duplicate" if path_raw in paths else "invalid"
            raise PilotfishError(
                "QUARANTINED", f"{reason} run-state worktree path: {path_raw}"
            )
        reject_symlink_components(path, layout.root)
        if not is_within_resolved(path, layout.worktrees):
            raise PilotfishError(
                "QUARANTINED", f"invalid run-state worktree path: {path_raw}"
            )
        paths.add(path_raw)
        branch_ref = record["branch_ref"]
        if (
            not isinstance(branch_ref, str)
            or not branch_ref.startswith("refs/heads/")
            or branch_ref in refs
            or any(character in branch_ref for character in ("\x00", "\n", "\r"))
        ):
            reason = "duplicate" if branch_ref in refs else "invalid"
            raise PilotfishError(
                "QUARANTINED", f"{reason} run-state worktree ref"
            )
        refs.add(branch_ref)
        if record["kind"] not in {
            "worker",
            "worker-pending",
            "integration",
            "integration-pending",
        }:
            raise PilotfishError(
                "QUARANTINED", "invalid run-state worktree kind"
            )
        kind = record["kind"]
        if kind.startswith("integration"):
            if (
                path != layout.integration_worktree
                or branch_ref != f"refs/heads/{layout.integration_branch}"
            ):
                raise PilotfishError(
                    "QUARANTINED",
                    "integration worktree record violates owned namespace",
                )
        else:
            job_id = path.name
            if (
                path.parent != layout.worktrees
                or not ID_RE.fullmatch(job_id)
                or job_id in RESERVED_JOB_IDS
                or branch_ref != f"refs/heads/pf/{run_id}/{job_id}"
            ):
                raise PilotfishError(
                    "QUARANTINED",
                    "worker worktree record violates owned namespace",
                )
        for sha_key in ("expected_ref_sha", "head_sha"):
            sha = record[sha_key]
            if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
                raise PilotfishError(
                    "QUARANTINED",
                    f"invalid run-state worktree {sha_key}",
                )
    return paths, refs


def _validate_process_records(records: Any) -> None:
    if not isinstance(records, list):
        raise PilotfishError(
            "QUARANTINED", "run-state processes must be a list"
        )
    job_ids: set[str] = set()
    pids: set[int] = set()
    pgids: set[int] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != PROCESS_RECORD_KEYS:
            raise PilotfishError(
                "QUARANTINED", "invalid run-state process record"
            )
        job_id = record["job_id"]
        if (
            not isinstance(job_id, str)
            or not ID_RE.fullmatch(job_id)
            or job_id in job_ids
        ):
            reason = "duplicate" if job_id in job_ids else "invalid"
            raise PilotfishError(
                "QUARANTINED", f"{reason} run-state process job id"
            )
        job_ids.add(job_id)
        for key, seen in (("pid", pids), ("pgid", pgids)):
            value = record[key]
            if value is not None and (
                type(value) is not int or value <= 0 or value in seen
            ):
                reason = "duplicate" if value in seen else "invalid"
                raise PilotfishError(
                    "QUARANTINED", f"{reason} run-state process {key}"
                )
            if value is not None:
                seen.add(value)
        started = record["started"]
        finished = record["finished"]
        if not _is_plain_number(started) or started < 0:
            raise PilotfishError(
                "QUARANTINED", "run-state process start must be finite"
            )
        if finished is not None and (
            not _is_plain_number(finished) or finished < started
        ):
            raise PilotfishError(
                "QUARANTINED", "run-state process end must be finite"
            )
        exit_code = record["exit_code"]
        if exit_code is not None and type(exit_code) is not int:
            raise PilotfishError(
                "QUARANTINED", "invalid run-state process exit code"
            )
        if not isinstance(record["status"], str) or not record["status"]:
            raise PilotfishError(
                "QUARANTINED", "invalid run-state process status"
            )


def validate_run_state(state: dict[str, Any], layout: RunLayout) -> None:
    if (
        not isinstance(state, dict)
        or set(state) != RUN_STATE_KEYS
        or type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1
        or state.get("owner") != "pilotfish-parallel"
    ):
        raise PilotfishError(
            "QUARANTINED", "run-state schema/key mismatch"
        )
    nonce = state["owner_nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise PilotfishError("QUARANTINED", "invalid owner nonce")
    run_id = state["run_id"]
    if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
        raise PilotfishError("QUARANTINED", "invalid run id")
    repo_raw = state["repo_root"]
    layout_raw = state["layout_root"]
    if not isinstance(repo_raw, str) or not isinstance(layout_raw, str):
        raise PilotfishError("QUARANTINED", "run-state root mismatch")
    repo_root = Path(repo_raw)
    recorded_layout = Path(layout_raw)
    expected_root = (
        Path("/tmp")
        / "pilotfish-parallel"
        / repo_id(repo_root)
        / run_id
    )
    expected_layout = RunLayout(
        root=expected_root,
        worktrees=expected_root / "worktrees",
        verification_repos=expected_root / "verification-repos",
        artifacts=expected_root / "artifacts",
        rollback=expected_root / "rollback",
        state_path=expected_root / "run-state.json",
        integration_worktree=expected_root / "worktrees" / "integration",
        integration_branch=f"pf/{run_id}/integration",
    )
    if (
        not repo_root.is_absolute()
        or repo_root != repo_root.resolve(strict=False)
        or not repo_root.is_dir()
        or recorded_layout != expected_root
        or layout != expected_layout
        or not is_within_resolved(layout.root, layout.root)
        or repo_root == layout.root
    ):
        raise PilotfishError("QUARANTINED", "run-state root mismatch")
    try:
        root_mode = layout.root.lstat().st_mode
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED", f"run-state layout root is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_mode):
        raise PilotfishError(
            "QUARANTINED", "run-state layout root is not a directory"
        )
    base_branch = state["base_branch"]
    if (
        not isinstance(base_branch, str)
        or not base_branch
        or any(character in base_branch for character in ("\x00", "\n", "\r"))
    ):
        raise PilotfishError("QUARANTINED", "invalid run-state base branch")
    for key in ("base_sha", "base_tree", "index_tree"):
        value = state[key]
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise PilotfishError(
                "QUARANTINED", f"invalid run-state {key}"
            )
    for key in ("lock_device", "lock_inode"):
        value = state[key]
        if type(value) is not int or value < 0:
            raise PilotfishError(
                "QUARANTINED", f"invalid run-state {key}"
            )
    if state["state"] not in RUN_STATES:
        raise PilotfishError("QUARANTINED", "invalid run-state state")
    if type(state["commit_point"]) is not bool or type(
        state["cleanup_required"]
    ) is not bool:
        raise PilotfishError(
            "QUARANTINED", "invalid run-state boolean fields"
        )
    worktree_paths, worktree_refs = _validate_worktree_records(
        state["worktrees"], layout, run_id
    )
    _validate_process_records(state["processes"])
    owned_files = _validate_owned_paths(
        state["owned_files"], "owned_files", layout
    )
    owned_directories = _validate_owned_paths(
        state["owned_directories"], "owned_directories", layout
    )
    if set(owned_files) & set(owned_directories):
        raise PilotfishError(
            "QUARANTINED", "owned file/directory records overlap"
        )
    required_files = {
        str(layout.root / "owner.json"),
        str(layout.state_path),
        str(layout.root / f".run-state.{nonce}.tmp"),
    }
    required_directories = {
        str(layout.root),
        str(layout.worktrees),
        str(layout.verification_repos),
        str(layout.artifacts),
    }
    if not required_files.issubset(owned_files) or not required_directories.issubset(
        owned_directories
    ):
        raise PilotfishError("QUARANTINED", "missing run-state ownership")
    progress = state["cleanup_progress"]
    if (
        not isinstance(progress, dict)
        or set(progress) != {"removed_worktrees", "removed_refs"}
    ):
        raise PilotfishError("QUARANTINED", "invalid cleanup progress")
    for key, known in (
        ("removed_worktrees", worktree_paths),
        ("removed_refs", worktree_refs),
    ):
        values = progress[key]
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) for value in values)
            or len(values) != len(set(values))
            or not set(values).issubset(known)
        ):
            raise PilotfishError(
                "QUARANTINED", f"invalid cleanup progress {key}"
            )
    transitions = state["transitions"]
    if not isinstance(transitions, list):
        raise PilotfishError("QUARANTINED", "invalid run-state transitions")
    for transition in transitions:
        if (
            not isinstance(transition, dict)
            or set(transition) != {"state", "monotonic", "evidence"}
            or transition["state"] not in RUN_STATES
            or not _is_plain_number(transition["monotonic"])
            or transition["monotonic"] < 0
            or not isinstance(transition["evidence"], dict)
        ):
            raise PilotfishError(
                "QUARANTINED",
                "invalid or non-finite run-state transition record",
            )
        _validate_json_value(transition["evidence"], "transition evidence")
    if state["failure"] is not None and not isinstance(state["failure"], dict):
        raise PilotfishError("QUARANTINED", "invalid run-state failure")
    if state["result"] is not None and not isinstance(state["result"], dict):
        raise PilotfishError("QUARANTINED", "invalid run-state result")
    _validate_json_value(state["failure"], "failure")
    _validate_json_value(state["result"], "result")
    persist_error = state["failure_state_persist_error"]
    if persist_error is not None and not isinstance(persist_error, str):
        raise PilotfishError(
            "QUARANTINED", "invalid failure-state persist error"
        )
    owner_path = layout.root / "owner.json"
    owner = _load_bounded_regular_json(
        owner_path, MAX_OWNER_MARKER_BYTES, "owner marker"
    )
    if owner != {
        "schema_version": 1,
        "owner": "pilotfish-parallel",
        "owner_nonce": nonce,
    }:
        raise PilotfishError("QUARANTINED", "owner marker mismatch")


def deterministic_owned_inventory(
    manifest: Manifest,
    layout: RunLayout,
    nonce: str,
    policy: InvocationPolicy,
) -> tuple[list[str], list[str]]:
    files: set[Path] = {
        layout.root / "owner.json",
        layout.state_path,
        layout.root / f".run-state.{nonce}.tmp",
        layout.artifacts / "combined.patch",
        layout.root / "preflight.index",
        layout.root / "verify.index",
        layout.rollback / "rollback.json",
    }
    files.update(
        layout.rollback / f"{index:04d}.bin"
        for index in range(policy.max_changed_files)
    )
    directories: set[Path] = {
        layout.root,
        layout.worktrees,
        layout.verification_repos,
        layout.verification_repos / "integration",
        layout.artifacts,
        layout.rollback,
        layout.artifacts / "job-checks",
        layout.artifacts / "integration",
        layout.artifacts / "final-verifier",
    }
    files.update(
        {
            layout.artifacts / "final-verifier" / "events.jsonl",
            layout.artifacts / "final-verifier" / "stderr.log",
            layout.artifacts / "final-verifier" / "final.json",
        }
    )
    for job in manifest.jobs:
        worker_artifacts = layout.artifacts / job.id
        job_checks = layout.artifacts / "job-checks" / job.id
        verification_clone = layout.verification_repos / f"job-{job.id}"
        directories.update(
            {worker_artifacts, job_checks, verification_clone}
        )
        files.update(
            {
                worker_artifacts / "events.jsonl",
                worker_artifacts / "stderr.log",
                worker_artifacts / "final.json",
            }
        )
        for command in job.verification_commands:
            files.update(
                {
                    job_checks / f"{command.id}.stdout",
                    job_checks / f"{command.id}.stderr",
                }
            )
    for command in manifest.integration_verification_commands:
        files.update(
            {
                layout.artifacts / "integration" / f"{command.id}.stdout",
                layout.artifacts / "integration" / f"{command.id}.stderr",
            }
        )
    return (
        sorted(str(path) for path in files),
        sorted(str(path) for path in directories),
    )


def initialize_run_state(
    manifest: Manifest,
    baseline: RepoBaseline,
    layout: RunLayout,
    lock: "RepoLock",
) -> dict[str, Any]:
    lock.assert_owned()
    assert lock.device_inode is not None
    nonce = secrets.token_hex(32)
    owner_path = layout.root / "owner.json"
    owner = {
        "schema_version": 1,
        "owner": "pilotfish-parallel",
        "owner_nonce": nonce,
    }
    descriptor = os.open(
        owner_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC,
        0o600,
    )
    owner_stat = os.fstat(descriptor)
    if not stat.S_ISREG(owner_stat.st_mode) or owner_stat.st_nlink != 1:
        os.close(descriptor)
        raise PilotfishError(
            "PRECHECK_FAILED", "owner marker is not a private regular file"
        )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(owner, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    policy = load_policy()
    owned_files, owned_directories = deterministic_owned_inventory(
        manifest, layout, nonce, policy
    )
    state: dict[str, Any] = {
        "schema_version": 1,
        "owner": "pilotfish-parallel",
        "owner_nonce": nonce,
        "run_id": manifest.run_id,
        "repo_root": str(baseline.root),
        "layout_root": str(layout.root),
        "base_branch": baseline.branch,
        "base_sha": baseline.base_sha,
        "base_tree": baseline.base_tree,
        "index_tree": baseline.index_tree,
        "lock_device": lock.device_inode[0],
        "lock_inode": lock.device_inode[1],
        "state": "PRECHECK",
        "commit_point": False,
        "cleanup_required": True,
        "worktrees": [],
        "processes": [],
        "owned_files": owned_files,
        "owned_directories": owned_directories,
        "transitions": [],
        "failure": None,
        "result": None,
        "cleanup_progress": {
            "removed_worktrees": [],
            "removed_refs": [],
        },
        "failure_state_persist_error": None,
    }
    validate_run_state(state, layout)
    persist_state(layout, state)
    return state


def persist_state(layout: RunLayout, state: dict[str, Any]) -> None:
    validate_run_state(state, layout)
    try:
        serialized = (
            json.dumps(
                state,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PilotfishError(
            "QUARANTINED", "run-state cannot be serialized as strict JSON"
        ) from exc
    if len(serialized) > MAX_RUN_STATE_BYTES:
        raise PilotfishError(
            "QUARANTINED", "run-state byte limit exceeded"
        )
    temporary_path = (
        layout.root / f".run-state.{state['owner_nonce']}.tmp"
    )
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            0o600,
        )
        descriptor_stat = os.fstat(descriptor)
        created_identity = (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        path_stat = temporary_path.lstat()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino) != created_identity
        ):
            raise PilotfishError(
                "QUARANTINED", "run-state temporary inode changed"
            )
        os.replace(temporary_path, layout.state_path)
        created_identity = None
    except BaseException:
        if created_identity is not None:
            try:
                path_stat = temporary_path.lstat()
                if (
                    stat.S_ISREG(path_stat.st_mode)
                    and (path_stat.st_dev, path_stat.st_ino)
                    == created_identity
                ):
                    temporary_path.unlink()
            except OSError:
                pass
        raise
    try:
        directory_descriptor = os.open(
            layout.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        )
    except OSError:
        return
    try:
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
    finally:
        os.close(directory_descriptor)


def load_and_validate_run_state(
    state_path: Path, repo_root: Path
) -> dict[str, Any]:
    state = _load_bounded_regular_json(
        state_path, MAX_RUN_STATE_BYTES, "run-state"
    )
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("repo_root"), str)
        or Path(state["repo_root"]).resolve(strict=False)
        != repo_root.resolve(strict=False)
    ):
        raise PilotfishError("QUARANTINED", "run-state repo mismatch")
    layout = layout_from_state(state)
    if state_path != layout.state_path:
        raise PilotfishError("QUARANTINED", "run-state path mismatch")
    validate_run_state(state, layout)
    return state


def register_intended_worktree(
    state: dict[str, Any],
    layout: RunLayout,
    path: Path,
    branch_ref: str,
    base_sha: str,
    *,
    kind: str,
) -> None:
    records = state.setdefault("worktrees", [])
    if not isinstance(records, list):
        raise PilotfishError(
            "PRECHECK_FAILED", "run state worktrees must be a list"
        )
    normalized_path = str(path.resolve(strict=False))
    if any(
        isinstance(record, dict) and record.get("path") == normalized_path
        for record in records
    ):
        raise PilotfishError(
            "PRECHECK_FAILED",
            f"duplicate intended worktree record: {normalized_path}",
        )
    records.append(
        {
            "path": normalized_path,
            "branch_ref": branch_ref,
            "expected_ref_sha": base_sha,
            "head_sha": base_sha,
            "kind": kind,
        }
    )
    persist_state(layout, state)


def refresh_owned_state_records(
    state: dict[str, Any],
    layout: RunLayout,
    *,
    workers: Sequence[WorkerRun] | None = None,
) -> None:
    validate_run_state(state, layout)
    original_state = state
    state = copy.deepcopy(state)
    existing = state["worktrees"]
    records_by_path: dict[str, dict[str, Any]] = {}
    for record in existing:
        path = record["path"]
        if path in records_by_path:
            raise PilotfishError(
                "QUARANTINED", f"duplicate run-state worktree path: {path}"
            )
        records_by_path[path] = dict(record)

    def current_ref_sha(repo: Path, branch_ref: str) -> str:
        observed = git(
            repo, "rev-parse", "--verify", branch_ref, check=False
        )
        if observed.returncode != 0:
            raise PilotfishError(
                "QUARANTINED", f"owned worktree ref is missing: {branch_ref}"
            )
        value = observed.stdout.decode("ascii").strip()
        if not SHA_RE.fullmatch(value):
            raise PilotfishError(
                "QUARANTINED", f"invalid owned worktree ref SHA: {branch_ref}"
            )
        return value

    repo = Path(state.get("repo_root", ""))
    refreshed_by_path: dict[str, dict[str, Any]] = {
        path: dict(record) for path, record in records_by_path.items()
    }
    known_workers = tuple(workers or ())
    for worker in known_workers:
        path = str(worker.worktree.resolve(strict=False))
        branch_ref = f"refs/heads/{worker.branch}"
        if not worker.worktree.is_dir():
            raise PilotfishError(
                "QUARANTINED", f"owned worker worktree is missing: {path}"
            )
        refreshed_by_path[path] = {
            "path": path,
            "branch_ref": branch_ref,
            "expected_ref_sha": current_ref_sha(repo, branch_ref),
            "head_sha": git_text(worker.worktree, "rev-parse", "HEAD"),
            "kind": "worker",
        }

    integration_path = str(
        layout.integration_worktree.resolve(strict=False)
    )
    if layout.integration_worktree.is_dir():
        integration_ref = f"refs/heads/{layout.integration_branch}"
        refreshed_by_path[integration_path] = {
            "path": integration_path,
            "branch_ref": integration_ref,
            "expected_ref_sha": current_ref_sha(repo, integration_ref),
            "head_sha": git_text(
                layout.integration_worktree, "rev-parse", "HEAD"
            ),
            "kind": "integration",
        }

    known_paths = {
        str(worker.worktree.resolve(strict=False)) for worker in known_workers
    }
    known_paths.add(integration_path)
    for path, record in tuple(refreshed_by_path.items()):
        if path in known_paths or str(record.get("kind", "")).endswith(
            "-pending"
        ):
            continue
        worktree_path = Path(path)
        branch_ref = record.get("branch_ref")
        if worktree_path.is_dir() and isinstance(branch_ref, str):
            refreshed_by_path[path] = {
                **record,
                "expected_ref_sha": current_ref_sha(repo, branch_ref),
                "head_sha": git_text(worktree_path, "rev-parse", "HEAD"),
            }
    state["worktrees"] = [
        refreshed_by_path[path] for path in sorted(refreshed_by_path)
    ]

    if workers is not None:
        state["processes"] = [
            {
                "job_id": worker.job.id,
                "pid": worker.process.pid if worker.process else None,
                "pgid": worker.process.pid if worker.process else None,
                "started": worker.started_monotonic,
                "finished": worker.finished_monotonic,
                "exit_code": worker.exit_code,
                "status": worker.status,
            }
            for worker in sorted(
                known_workers, key=lambda item: item.job.id
            )
        ]

    files = set(state["owned_files"])
    directories = set(state["owned_directories"])
    for worker in known_workers:
        is_internal_final_verifier = (
            worker.job.id == "final-verifier"
            and worker.role.name == "verifier"
            and worker.worktree == layout.integration_worktree
            and worker.branch == layout.integration_branch
        )
        if (
            not ID_RE.fullmatch(worker.job.id)
            or (
                worker.job.id in RESERVED_JOB_IDS
                and not is_internal_final_verifier
            )
        ):
            raise PilotfishError(
                "QUARANTINED", "worker artifact id violates owned namespace"
            )
        expected_parent = layout.artifacts / worker.job.id
        expected_artifacts = (
            expected_parent / "events.jsonl",
            expected_parent / "stderr.log",
            expected_parent / "final.json",
        )
        actual_artifacts = (
            worker.events_path,
            worker.stderr_path,
            worker.final_path,
        )
        if actual_artifacts != expected_artifacts:
            raise PilotfishError(
                "QUARANTINED", "worker artifact path violates owned namespace"
            )
        directories.add(str(expected_parent))
        files.update(str(path) for path in expected_artifacts)

    for root, dirnames, filenames in os.walk(
        layout.root, followlinks=False
    ):
        root_path = Path(root)
        if root_path == layout.worktrees or layout.worktrees in root_path.parents:
            dirnames[:] = []
            continue
        for dirname in dirnames:
            path = root_path / dirname
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise PilotfishError(
                    "QUARANTINED",
                    f"cannot inspect owned run directory: {path}: {exc}",
                ) from exc
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise PilotfishError(
                    "QUARANTINED",
                    f"symlink or non-directory in owned run tree: {path}",
                )
            if str(path) not in directories:
                raise PilotfishError(
                    "QUARANTINED", f"unexpected run directory: {path}"
                )
        for filename in filenames:
            path = root_path / filename
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise PilotfishError(
                    "QUARANTINED",
                    f"cannot inspect owned artifact: {path}: {exc}",
                ) from exc
            if stat.S_ISLNK(mode):
                raise PilotfishError(
                    "QUARANTINED", f"symlink in owned run tree: {path}"
                )
            if not stat.S_ISREG(mode):
                raise PilotfishError(
                    "QUARANTINED", f"non-regular owned artifact: {path}"
                )
            if str(path) not in files:
                raise PilotfishError(
                    "QUARANTINED", f"unexpected run file: {path}"
                )
    state["owned_files"] = sorted(files)
    state["owned_directories"] = sorted(directories)
    validate_run_state(state, layout)
    original_state.clear()
    original_state.update(state)


def process_exists_os(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def verify_recorded_processes_dead(state: dict[str, Any]) -> None:
    records = state.get("processes")
    _validate_process_records(records)
    for record in records:
        pid = record["pid"]
        pgid = record["pgid"]
        if pid is not None and process_exists_os(pid):
            raise PilotfishError(
                "QUARANTINED", f"recorded process is still alive: {pid}"
            )
        if pgid is not None and process_group_exists_os(pgid):
            raise PilotfishError(
                "QUARANTINED",
                f"recorded process group is still alive: {pgid}",
            )


def parse_worktree_porcelain(raw: bytes) -> set[str]:
    paths: set[str] = set()
    for field in raw.split(b"\0"):
        if not field or not field.startswith(b"worktree "):
            continue
        encoded_path = field[len(b"worktree ") :]
        if not encoded_path:
            raise PilotfishError(
                "QUARANTINED", "malformed Git worktree porcelain"
            )
        try:
            path = str(Path(os.fsdecode(encoded_path)).resolve(strict=False))
        except (OSError, RuntimeError, ValueError) as exc:
            raise PilotfishError(
                "QUARANTINED", "invalid Git worktree porcelain path"
            ) from exc
        if path in paths:
            raise PilotfishError(
                "QUARANTINED", f"duplicate Git worktree path: {path}"
            )
        paths.add(path)
    return paths


def _run_namespace_refs(repo: Path, run_id: str) -> set[str]:
    completed = git(
        repo,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/pf/{run_id}/",
        error_state="QUARANTINED",
    )
    return {
        line.decode("utf-8", errors="strict")
        for line in completed.stdout.splitlines()
        if line
    }


def _verify_owned_run_tree(
    state: dict[str, Any], layout: RunLayout
) -> None:
    expected_files = set(state["owned_files"])
    expected_directories = set(state["owned_directories"])
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    recorded_worktree_paths = {
        record["path"] for record in state["worktrees"]
    }

    for raw in expected_files:
        path = Path(raw)
        if not os.path.lexists(path):
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise PilotfishError(
                "QUARANTINED", f"owned file is not regular: {path}"
            )
    for raw in expected_directories:
        path = Path(raw)
        if not os.path.lexists(path):
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise PilotfishError(
                "QUARANTINED", f"owned directory is not a directory: {path}"
            )

    for root, dirnames, filenames in os.walk(
        layout.root, followlinks=False
    ):
        root_path = Path(root)
        actual_directories.add(str(root_path))
        if root_path == layout.worktrees:
            for name in (*dirnames, *filenames):
                child = root_path / name
                resolved = str(child.resolve(strict=False))
                if child.is_symlink() or resolved not in recorded_worktree_paths:
                    raise PilotfishError(
                        "QUARANTINED",
                        f"unknown run worktree path: {child}",
                    )
            dirnames[:] = []
            continue
        if root_path == layout.verification_repos:
            for name in dirnames:
                child = root_path / name
                if child.is_symlink() or str(child) not in expected_directories:
                    raise PilotfishError(
                        "QUARANTINED",
                        f"unknown verification clone path: {child}",
                    )
            if filenames:
                raise PilotfishError(
                    "QUARANTINED",
                    "unexpected file in verification clone container",
                )
            dirnames[:] = []
            continue
        if layout.worktrees in root_path.parents:
            dirnames[:] = []
            continue
        for dirname in dirnames:
            path = root_path / dirname
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise PilotfishError(
                    "QUARANTINED", f"symlink in run tree: {path}"
                )
            actual_directories.add(str(path))
        for filename in filenames:
            path = root_path / filename
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise PilotfishError(
                    "QUARANTINED", f"symlink in run tree: {path}"
                )
            if not stat.S_ISREG(mode):
                raise PilotfishError(
                    "QUARANTINED", f"non-regular file in run tree: {path}"
                )
            actual_files.add(str(path))

    unexpected_files = actual_files - expected_files
    if unexpected_files:
        raise PilotfishError(
            "QUARANTINED",
            f"unexpected run files: {sorted(unexpected_files)}",
        )
    unexpected_directories = actual_directories - expected_directories
    if unexpected_directories:
        raise PilotfishError(
            "QUARANTINED",
            f"unexpected run directories: {sorted(unexpected_directories)}",
        )


def verify_all_owned_paths_and_objects(
    state: dict[str, Any],
    actual_worktrees: set[str],
    *,
    removed_worktrees: set[str],
    removed_refs: set[str],
) -> None:
    layout = layout_from_state(state)
    validate_run_state(state, layout)
    repo = Path(state["repo_root"])
    records = state["worktrees"]
    known_paths = {record["path"] for record in records}
    known_refs = {record["branch_ref"] for record in records}
    if not removed_worktrees.issubset(known_paths) or not removed_refs.issubset(
        known_refs
    ):
        raise PilotfishError(
            "QUARANTINED", "cleanup progress references an unknown object"
        )

    actual_run_worktrees = {
        path
        for path in actual_worktrees
        if is_within_resolved(Path(path), layout.worktrees)
    }
    unknown_worktrees = actual_run_worktrees - known_paths
    if unknown_worktrees:
        raise PilotfishError(
            "QUARANTINED",
            f"unknown run worktrees: {sorted(unknown_worktrees)}",
        )
    unknown_refs = _run_namespace_refs(repo, state["run_id"]) - known_refs
    if unknown_refs:
        raise PilotfishError(
            "QUARANTINED", f"unknown run refs: {sorted(unknown_refs)}"
        )

    for record in records:
        path = record["path"]
        worktree = Path(path)
        branch_ref = record["branch_ref"]
        pending = record["kind"].endswith("-pending")
        if path in removed_worktrees:
            if path in actual_worktrees or os.path.lexists(worktree):
                raise PilotfishError(
                    "QUARANTINED", f"removed worktree reappeared: {path}"
                )
        elif path not in actual_worktrees:
            if os.path.lexists(worktree):
                raise PilotfishError(
                    "QUARANTINED",
                    f"unregistered owned worktree path exists: {path}",
                )
            if not pending:
                raise PilotfishError(
                    "QUARANTINED", f"owned worktree is missing: {path}"
                )
        else:
            if not worktree.is_dir() or worktree.is_symlink():
                raise PilotfishError(
                    "QUARANTINED", f"owned worktree is not a directory: {path}"
                )
            observed_head = git_text(
                worktree,
                "rev-parse",
                "HEAD",
                error_state="QUARANTINED",
            )
            if observed_head != record["head_sha"]:
                raise PilotfishError(
                    "QUARANTINED", f"owned worktree HEAD drift: {path}"
                )
            observed_branch = git(
                worktree,
                "symbolic-ref",
                "--quiet",
                "HEAD",
                check=False,
            )
            if (
                observed_branch.returncode != 0
                or observed_branch.stdout.decode("utf-8").strip()
                != branch_ref
            ):
                raise PilotfishError(
                    "QUARANTINED", f"owned worktree branch drift: {path}"
                )

        current_ref = git(
            repo,
            "rev-parse",
            "--verify",
            branch_ref,
            check=False,
        )
        if branch_ref in removed_refs:
            if current_ref.returncode == 0:
                raise PilotfishError(
                    "QUARANTINED", f"removed ref reappeared: {branch_ref}"
                )
        elif current_ref.returncode == 0:
            observed_ref = current_ref.stdout.decode("ascii").strip()
            if observed_ref != record["expected_ref_sha"]:
                raise PilotfishError(
                    "QUARANTINED", f"owned ref drift: {branch_ref}"
                )
        elif not pending:
            raise PilotfishError(
                "QUARANTINED", f"owned ref is missing: {branch_ref}"
            )

    _verify_owned_run_tree(state, layout)


TERMINAL_RUN_STATES = frozenset(
    {
        "PRECHECK_FAILED",
        "WORKER_FAILED",
        "QUARANTINED",
        "INTEGRATION_FAILED",
        "VERIFICATION_FAILED",
        "SOURCE_DRIFTED",
        "CANCELLED",
        "ROLLBACK_FAILED",
        "APPLIED",
    }
)


def cleanup_has_started(state: dict[str, Any]) -> bool:
    return any(
        isinstance(transition, dict)
        and isinstance(transition.get("evidence"), dict)
        and transition["evidence"].get("cleanup_started") is True
        for transition in state.get("transitions", [])
    )


def mark_cleanup_started(state: dict[str, Any], layout: RunLayout) -> None:
    if cleanup_has_started(state):
        return
    state["transitions"].append(
        {
            "state": state["state"],
            "monotonic": time.monotonic(),
            "evidence": {"cleanup_started": True},
        }
    )
    persist_state(layout, state)


def mark_cleanup_progress(
    state_path: Path,
    state: dict[str, Any],
    removed_worktrees: Sequence[str],
    removed_refs: Sequence[str],
) -> None:
    expected_state_path = layout_from_state(state).state_path
    if state_path != expected_state_path:
        raise PilotfishError("QUARANTINED", "cleanup state path mismatch")
    state["cleanup_progress"] = {
        "removed_worktrees": list(removed_worktrees),
        "removed_refs": list(removed_refs),
    }
    persist_state(layout_from_state(state), state)


def recover_cleanup_progress_after_interruption(
    state: dict[str, Any],
    repo_root: Path,
    actual_worktrees: set[str],
) -> tuple[list[str], list[str]]:
    progress = state["cleanup_progress"]
    removed_worktrees = list(progress["removed_worktrees"])
    removed_refs = list(progress["removed_refs"])
    for record in state["worktrees"]:
        path = record["path"]
        if (
            path not in removed_worktrees
            and path not in actual_worktrees
            and not os.path.lexists(path)
        ):
            removed_worktrees.append(path)
        branch_ref = record["branch_ref"]
        if branch_ref not in removed_refs:
            current = git(
                repo_root,
                "rev-parse",
                "--verify",
                branch_ref,
                check=False,
            )
            if current.returncode != 0:
                removed_refs.append(branch_ref)
    return removed_worktrees, removed_refs


def remove_interrupted_state_temporary(
    state: dict[str, Any], layout: RunLayout
) -> None:
    temporary = layout.root / f".run-state.{state['owner_nonce']}.tmp"
    if not os.path.lexists(temporary):
        return
    if str(temporary) not in state["owned_files"]:
        raise PilotfishError(
            "QUARANTINED", "state temporary is not explicitly owned"
        )
    reject_symlink_components(temporary, layout.root)
    try:
        root_descriptor = os.open(
            layout.root,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
        )
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED", f"cannot open cleanup layout root: {exc}"
        ) from exc
    try:
        try:
            path_stat = os.stat(
                temporary.name,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            temporary_descriptor = os.open(
                temporary.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_descriptor,
            )
        except OSError as exc:
            raise PilotfishError(
                "QUARANTINED",
                f"cannot safely inspect interrupted state temporary: {exc}",
            ) from exc
        try:
            opened_stat = os.fstat(temporary_descriptor)
            identity = (opened_stat.st_dev, opened_stat.st_ino)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or identity != (path_stat.st_dev, path_stat.st_ino)
                or opened_stat.st_uid != os.geteuid()
                or opened_stat.st_nlink != 1
                or stat.S_IMODE(opened_stat.st_mode) != 0o600
                or opened_stat.st_size > MAX_RUN_STATE_BYTES
            ):
                raise PilotfishError(
                    "QUARANTINED",
                    "interrupted state temporary ownership is invalid",
                )
        finally:
            os.close(temporary_descriptor)
        current_stat = os.stat(
            temporary.name,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (current_stat.st_dev, current_stat.st_ino) != identity:
            raise PilotfishError(
                "QUARANTINED", "interrupted state temporary inode changed"
            )
        os.unlink(temporary.name, dir_fd=root_descriptor)
        try:
            os.fsync(root_descriptor)
        except OSError:
            pass
    finally:
        os.close(root_descriptor)


def cleanup_untracked_content_digest(worktree: Path) -> bytes:
    paths: set[bytes] = set()
    for arguments in (
        ("ls-files", "--others", "--exclude-standard", "-z"),
        (
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ),
    ):
        raw = git(
            worktree, *arguments, error_state="QUARANTINED"
        ).stdout
        paths.update(value for value in raw.split(b"\0") if value)

    digest = hashlib.sha256()
    for encoded in sorted(paths):
        try:
            relative = normalize_prefix(os.fsdecode(encoded))
        except PilotfishError as exc:
            raise PilotfishError(
                "QUARANTINED", f"unsafe cleanup file path: {encoded!r}"
            ) from exc
        path = worktree.joinpath(*PurePosixPath(relative).parts)
        reject_symlink_components(path.parent, worktree)
        try:
            path_stat = path.lstat()
            descriptor = os.open(
                path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except OSError as exc:
            raise PilotfishError(
                "QUARANTINED",
                f"cannot safely read cleanup file {relative}: {exc}",
            ) from exc
        try:
            opened_stat = os.fstat(descriptor)
            identity = (opened_stat.st_dev, opened_stat.st_ino)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or identity != (path_stat.st_dev, path_stat.st_ino)
            ):
                raise PilotfishError(
                    "QUARANTINED",
                    f"cleanup file is not a stable regular file: {relative}",
                )
            content_digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                content_digest.update(chunk)
            after_read = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            current_stat = path.lstat()
        except OSError as exc:
            raise PilotfishError(
                "QUARANTINED",
                f"cleanup file disappeared while hashing: {relative}",
            ) from exc
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(opened_stat, field) != getattr(after_read, field)
            or getattr(opened_stat, field) != getattr(current_stat, field)
            for field in stable_fields
        ):
            raise PilotfishError(
                "QUARANTINED",
                f"cleanup file changed while hashing: {relative}",
            )
        for value in (
            encoded,
            stat.S_IMODE(opened_stat.st_mode).to_bytes(4, "big"),
            opened_stat.st_size.to_bytes(8, "big"),
            content_digest.digest(),
        ):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.digest()


def cleanup_worktree_fingerprint(
    repo_root: Path, record: dict[str, Any]
) -> str:
    worktree = Path(record["path"])
    head = git(
        worktree,
        "rev-parse",
        "HEAD",
        error_state="QUARANTINED",
    ).stdout
    branch = git(
        worktree,
        "symbolic-ref",
        "--quiet",
        "HEAD",
        error_state="QUARANTINED",
    ).stdout
    ref = git(
        repo_root,
        "rev-parse",
        "--verify",
        record["branch_ref"],
        error_state="QUARANTINED",
    ).stdout
    if head.decode("ascii").strip() != record["head_sha"]:
        raise PilotfishError(
            "QUARANTINED", f"owned worktree HEAD drift: {worktree}"
        )
    if branch.decode("utf-8").strip() != record["branch_ref"]:
        raise PilotfishError(
            "QUARANTINED", f"owned worktree branch drift: {worktree}"
        )
    if ref.decode("ascii").strip() != record["expected_ref_sha"]:
        raise PilotfishError(
            "QUARANTINED", f"owned ref drift: {record['branch_ref']}"
        )
    status = git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        error_state="QUARANTINED",
    ).stdout
    staged = git(
        worktree,
        "ls-files",
        "--stage",
        "-z",
        error_state="QUARANTINED",
    ).stdout
    flags = git(
        worktree,
        "ls-files",
        "-v",
        "-z",
        error_state="QUARANTINED",
    ).stdout
    working_diff = git(
        worktree,
        "diff",
        "--binary",
        "--full-index",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        error_state="QUARANTINED",
    ).stdout
    untracked_content = cleanup_untracked_content_digest(worktree)
    digest = hashlib.sha256()
    for value in (
        head,
        branch,
        ref,
        status,
        staged,
        flags,
        working_diff,
        untracked_content,
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def capture_cleanup_worktree_fingerprints(
    state: dict[str, Any], repo_root: Path, actual_worktrees: set[str]
) -> dict[str, str]:
    return {
        record["path"]: cleanup_worktree_fingerprint(repo_root, record)
        for record in state["worktrees"]
        if record["path"] in actual_worktrees
        and record["path"]
        not in state["cleanup_progress"]["removed_worktrees"]
    }


def assert_cleanup_worktree_fingerprint(
    repo_root: Path, record: dict[str, Any], expected: str
) -> None:
    observed = cleanup_worktree_fingerprint(repo_root, record)
    if observed != expected:
        raise PilotfishError(
            "QUARANTINED",
            f"owned worktree fingerprint drift: {record['path']}",
        )


def _delete_owned_regular(path: Path, *, required: bool = False) -> None:
    if not os.path.lexists(path):
        if required:
            raise PilotfishError(
                "QUARANTINED", f"required owned file is missing: {path}"
            )
        return
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise PilotfishError(
            "QUARANTINED", f"refusing non-regular owned delete: {path}"
        )
    path.unlink()


def delete_exact_owned_files_and_empty_directories(
    state: dict[str, Any], state_path_last: bool
) -> None:
    if not state_path_last:
        raise PilotfishError(
            "QUARANTINED", "cleanup state file must be deleted last"
        )
    layout = layout_from_state(state)
    validate_run_state(state, layout)
    state_path = layout.state_path
    owner_path = layout.root / "owner.json"
    ordinary_files = sorted(
        Path(value)
        for value in state["owned_files"]
        if Path(value) not in {owner_path, state_path}
    )
    for path in ordinary_files:
        reject_symlink_components(path, layout.root)
        _delete_owned_regular(path)

    directories = sorted(
        (
            Path(value)
            for value in state["owned_directories"]
            if Path(value) != layout.root
        ),
        key=lambda item: (len(item.parts), os.fsencode(item)),
        reverse=True,
    )
    for path in directories:
        if not os.path.lexists(path):
            continue
        reject_symlink_components(path, layout.root)
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise PilotfishError(
                "QUARANTINED", f"refusing non-directory owned rmdir: {path}"
            )
        try:
            path.rmdir()
        except OSError as exc:
            raise PilotfishError(
                "QUARANTINED", f"owned directory is not empty: {path}"
            ) from exc

    _delete_owned_regular(owner_path, required=True)
    _delete_owned_regular(state_path, required=True)
    try:
        layout.root.rmdir()
    except OSError as exc:
        raise PilotfishError(
            "QUARANTINED", "run layout root is not empty after cleanup"
        ) from exc


def remove_empty_layout_containers(state: dict[str, Any]) -> None:
    layout = layout_from_state(state)
    expected_repo_container = (
        Path("/tmp")
        / "pilotfish-parallel"
        / repo_id(Path(state["repo_root"]))
    )
    shared_container = expected_repo_container.parent
    if layout.root.parent != expected_repo_container:
        raise PilotfishError(
            "QUARANTINED", "cleanup container namespace mismatch"
        )
    for container in (expected_repo_container, shared_container):
        if not os.path.lexists(container):
            continue
        try:
            result = container.lstat()
        except OSError as exc:
            raise PilotfishError(
                "QUARANTINED",
                f"cannot inspect cleanup container: {container}: {exc}",
            ) from exc
        if (
            not stat.S_ISDIR(result.st_mode)
            or stat.S_ISLNK(result.st_mode)
            or result.st_uid != os.getuid()
        ):
            raise PilotfishError(
                "QUARANTINED",
                f"cleanup container ownership is invalid: {container}",
            )
        try:
            container.rmdir()
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                break
            if exc.errno == errno.ENOENT:
                continue
            raise PilotfishError(
                "QUARANTINED",
                f"cannot remove empty cleanup container: {container}",
            ) from exc


def cleanup_run(
    repo_root: Path,
    state_path: Path,
    require_finished: bool,
    lock: "RepoLock",
) -> dict[str, Any]:
    try:
        lock.assert_owned()
    except PilotfishError as exc:
        raise PilotfishError(
            "QUARANTINED", f"cleanup lock is not owned: {exc}"
        ) from exc
    state = load_and_validate_run_state(state_path, repo_root)
    if lock.device_inode != (state["lock_device"], state["lock_inode"]):
        raise PilotfishError("QUARANTINED", "cleanup lock identity mismatch")
    if require_finished and state["state"] not in TERMINAL_RUN_STATES:
        raise PilotfishError("QUARANTINED", "run is not terminal")
    verify_recorded_processes_dead(state)
    actual_worktrees = parse_worktree_porcelain(
        git(
            repo_root,
            "worktree",
            "list",
            "--porcelain",
            "-z",
            error_state="QUARANTINED",
        ).stdout
    )
    persisted_removed_worktrees = list(
        state["cleanup_progress"]["removed_worktrees"]
    )
    persisted_removed_refs = list(
        state["cleanup_progress"]["removed_refs"]
    )
    if cleanup_has_started(state):
        removed_worktrees, removed_refs = (
            recover_cleanup_progress_after_interruption(
                state, repo_root, actual_worktrees
            )
        )
    else:
        removed_worktrees = list(persisted_removed_worktrees)
        removed_refs = list(persisted_removed_refs)
    verify_all_owned_paths_and_objects(
        state,
        actual_worktrees,
        removed_worktrees=set(removed_worktrees),
        removed_refs=set(removed_refs),
    )
    layout = layout_from_state(state)
    remove_interrupted_state_temporary(state, layout)
    if (
        removed_worktrees != persisted_removed_worktrees
        or removed_refs != persisted_removed_refs
    ):
        mark_cleanup_progress(
            state_path, state, removed_worktrees, removed_refs
        )
    if not cleanup_has_started(state):
        mark_cleanup_started(state, layout)
    worktree_fingerprints = capture_cleanup_worktree_fingerprints(
        state, repo_root, actual_worktrees
    )

    verification_clones = sorted(
        (
            Path(value)
            for value in state["owned_directories"]
            if Path(value).parent == layout.verification_repos
        ),
        key=lambda item: os.fsencode(item),
    )
    for clone in verification_clones:
        if not os.path.lexists(clone):
            continue
        try:
            lock.assert_owned()
        except PilotfishError as exc:
            raise PilotfishError(
                "QUARANTINED", f"cleanup lock is not owned: {exc}"
            ) from exc
        remove_verification_clone(clone, layout)

    remaining_worktrees = [
        record
        for record in state["worktrees"]
        if record["path"] not in removed_worktrees
    ]
    for record in sorted(
        remaining_worktrees, key=lambda item: item["path"], reverse=True
    ):
        path = record["path"]
        if path in actual_worktrees:
            try:
                lock.assert_owned()
            except PilotfishError as exc:
                raise PilotfishError(
                    "QUARANTINED", f"cleanup lock is not owned: {exc}"
                ) from exc
            current_worktrees = parse_worktree_porcelain(
                git(
                    repo_root,
                    "worktree",
                    "list",
                    "--porcelain",
                    "-z",
                    error_state="QUARANTINED",
                ).stdout
            )
            if path not in current_worktrees:
                raise PilotfishError(
                    "QUARANTINED",
                    f"owned worktree disappeared before removal: {path}",
                )
            assert_cleanup_worktree_fingerprint(
                repo_root, record, worktree_fingerprints[path]
            )
            git(
                repo_root,
                "worktree",
                "remove",
                "--force",
                path,
                error_state="QUARANTINED",
            )
        if os.path.lexists(path):
            raise PilotfishError(
                "QUARANTINED", f"worktree path survived removal: {path}"
            )
        removed_worktrees.append(path)
        mark_cleanup_progress(
            state_path, state, removed_worktrees, removed_refs
        )

    remaining_refs = [
        record
        for record in state["worktrees"]
        if record["branch_ref"] not in removed_refs
    ]
    for record in sorted(
        remaining_refs, key=lambda item: item["branch_ref"]
    ):
        branch_ref = record["branch_ref"]
        try:
            lock.assert_owned()
        except PilotfishError as exc:
            raise PilotfishError(
                "QUARANTINED", f"cleanup lock is not owned: {exc}"
            ) from exc
        current = git(
            repo_root,
            "rev-parse",
            "--verify",
            branch_ref,
            check=False,
        )
        if current.returncode != 0 and record["kind"].endswith("-pending"):
            removed_refs.append(branch_ref)
            mark_cleanup_progress(
                state_path, state, removed_worktrees, removed_refs
            )
            continue
        deleted = git(
            repo_root,
            "update-ref",
            "-d",
            branch_ref,
            record["expected_ref_sha"],
            check=False,
        )
        still_present = git(
            repo_root,
            "rev-parse",
            "--verify",
            branch_ref,
            check=False,
        )
        if deleted.returncode != 0 or still_present.returncode == 0:
            mark_cleanup_progress(
                state_path, state, removed_worktrees, removed_refs
            )
            raise PilotfishError(
                "QUARANTINED", f"CAS ref deletion refused: {branch_ref}"
            )
        removed_refs.append(branch_ref)
        mark_cleanup_progress(
            state_path, state, removed_worktrees, removed_refs
        )

    delete_exact_owned_files_and_empty_directories(
        state, state_path_last=True
    )
    remove_empty_layout_containers(state)
    return {
        "status": "CLEANED",
        "run_id": state["run_id"],
        "removed_worktrees": removed_worktrees,
        "removed_refs": removed_refs,
    }


def cleanup_from_run_id(repo_root: Path, run_id: str) -> dict[str, Any]:
    if not ID_RE.fullmatch(run_id):
        raise PilotfishError("PRECHECK_FAILED", "invalid cleanup run id")
    repo_root = repo_root.resolve()
    top_level = Path(
        git_text(
            repo_root,
            "rev-parse",
            "--show-toplevel",
            error_state="PRECHECK_FAILED",
        )
    ).resolve()
    if top_level != repo_root:
        raise PilotfishError(
            "PRECHECK_FAILED", "cleanup repo root must be repository top-level"
        )
    state_path = (
        Path("/tmp")
        / "pilotfish-parallel"
        / repo_id(repo_root)
        / run_id
        / "run-state.json"
    )
    expected_state = load_and_validate_run_state(state_path, repo_root)
    common_raw = Path(git_text(repo_root, "rev-parse", "--git-common-dir"))
    common_dir = (
        common_raw.resolve()
        if common_raw.is_absolute()
        else (repo_root / common_raw).resolve()
    )
    try:
        common_stat = os.stat(common_dir, follow_symlinks=False)
    except OSError as exc:
        raise PilotfishError(
            "PRECHECK_FAILED", f"cannot inspect cleanup common directory: {exc}"
        ) from exc
    if not stat.S_ISDIR(common_stat.st_mode):
        raise PilotfishError(
            "PRECHECK_FAILED", "cleanup common directory is not a directory"
        )
    common_identity = (common_stat.st_dev, common_stat.st_ino)
    with RepoLock(
        common_dir, f"cleanup:{run_id}", common_identity
    ) as cleanup_lock:
        if cleanup_lock.device_inode != (
            expected_state["lock_device"], expected_state["lock_inode"]
        ):
            raise PilotfishError(
                "QUARANTINED", "run lock inode differs from original run"
            )
        return cleanup_run(
            repo_root,
            state_path,
            require_finished=True,
            lock=cleanup_lock,
        )


def nul_paths(raw: bytes) -> tuple[str, ...]:
    values: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        values.append(normalize_prefix(os.fsdecode(item)))
    return tuple(values)


def git_env_with_index(index_path: Path) -> dict[str, str]:
    return {"GIT_INDEX_FILE": str(index_path)}


def write_binary_patch(
    baseline: RepoBaseline,
    integration: Path,
    path: Path,
    policy: InvocationPolicy,
    *,
    end_ref: str = "HEAD",
) -> PatchArtifact:
    patch = git(
        integration,
        "diff",
        "--binary",
        "--full-index",
        "--no-renames",
        baseline.base_sha,
        end_ref,
        error_state="INTEGRATION_FAILED",
    ).stdout
    if not patch:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "integration produced an empty combined patch",
        )
    if len(patch) > policy.max_patch_bytes:
        raise PilotfishError(
            "INTEGRATION_FAILED", "combined patch byte limit exceeded"
        )
    path.write_bytes(patch)
    return PatchArtifact(
        path=path,
        sha256=hashlib.sha256(patch).hexdigest(),
        bytes=patch,
    )


def verify_patch_evidence(artifact: PatchArtifact) -> None:
    try:
        evidence_stat = artifact.path.lstat()
        if not stat.S_ISREG(evidence_stat.st_mode):
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "combined patch evidence is not regular",
            )
        evidence = artifact.path.read_bytes()
    except PilotfishError:
        raise
    except OSError as exc:
        raise PilotfishError(
            "INTEGRATION_FAILED", "cannot read combined patch evidence"
        ) from exc
    if (
        hashlib.sha256(evidence).hexdigest() != artifact.sha256
        or evidence != artifact.bytes
    ):
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "combined patch evidence hash mismatch",
        )


def prove_patch_tree(
    repo: Path,
    baseline: RepoBaseline,
    patch: PatchArtifact,
    index_path: Path,
    expected_tree: str,
) -> None:
    try:
        index_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"cannot reset temporary index: {index_path}",
        ) from exc
    index_path.parent.mkdir(parents=True, exist_ok=True)
    environment = git_env_with_index(index_path)
    git(
        repo,
        "read-tree",
        baseline.base_sha,
        env=environment,
        error_state="INTEGRATION_FAILED",
    )
    git(
        repo,
        "apply",
        "--cached",
        "--binary",
        "-",
        env=environment,
        input_bytes=patch.bytes,
        error_state="INTEGRATION_FAILED",
    )
    observed = git_text(
        repo,
        "write-tree",
        env=environment,
        error_state="INTEGRATION_FAILED",
    )
    if observed != expected_tree:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"temporary-index tree mismatch: {observed} != {expected_tree}",
        )


def _rollback_path(value: object, state: str) -> str:
    if not isinstance(value, str):
        raise PilotfishError(state, "rollback manifest path is not text")
    try:
        return normalize_prefix(value)
    except PilotfishError as exc:
        raise PilotfishError(state, f"unsafe rollback path: {value!r}") from exc


def _load_rollback_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest_stat = manifest_path.lstat()
        if not stat.S_ISREG(manifest_stat.st_mode):
            raise PilotfishError(
                "ROLLBACK_FAILED", "rollback manifest is not regular"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except PilotfishError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PilotfishError(
            "ROLLBACK_FAILED", "cannot read rollback manifest"
        ) from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "records",
        "missing_parents",
    }:
        raise PilotfishError(
            "ROLLBACK_FAILED", "rollback manifest shape mismatch"
        )
    records = manifest["records"]
    missing_parents = manifest["missing_parents"]
    if not isinstance(records, list) or not isinstance(missing_parents, list):
        raise PilotfishError(
            "ROLLBACK_FAILED", "rollback manifest collections are invalid"
        )
    seen_paths: set[str] = set()
    seen_payloads: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("existed"), bool
        ):
            raise PilotfishError(
                "ROLLBACK_FAILED", "rollback record shape mismatch"
            )
        relative = _rollback_path(record.get("path"), "ROLLBACK_FAILED")
        if relative in seen_paths:
            raise PilotfishError(
                "ROLLBACK_FAILED", f"duplicate rollback path: {relative}"
            )
        seen_paths.add(relative)
        record["path"] = relative
        if record["existed"]:
            if set(record) != {
                "path",
                "existed",
                "payload",
                "sha256",
                "mode",
            }:
                raise PilotfishError(
                    "ROLLBACK_FAILED", "rollback file record shape mismatch"
                )
            payload = record["payload"]
            digest = record["sha256"]
            mode = record["mode"]
            if (
                not isinstance(payload, str)
                or PurePosixPath(payload).name != payload
                or payload in {"", ".", ".."}
                or payload in seen_payloads
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or not isinstance(mode, int)
                or isinstance(mode, bool)
                or not 0 <= mode <= 0o7777
            ):
                raise PilotfishError(
                    "ROLLBACK_FAILED", "rollback payload metadata is invalid"
                )
            seen_payloads.add(payload)
        elif set(record) != {"path", "existed"}:
            raise PilotfishError(
                "ROLLBACK_FAILED", "rollback absence record shape mismatch"
            )
    normalized_parents = [
        _rollback_path(value, "ROLLBACK_FAILED")
        for value in missing_parents
    ]
    if len(normalized_parents) != len(set(normalized_parents)):
        raise PilotfishError(
            "ROLLBACK_FAILED", "duplicate rollback missing parent"
        )
    manifest["missing_parents"] = normalized_parents
    return manifest


def create_rollback_bundle(
    repo: Path,
    changed_paths: Sequence[str],
    bundle_dir: Path,
) -> RollbackBundle:
    normalized = tuple(normalize_prefix(value) for value in changed_paths)
    if len(normalized) != len(set(normalized)):
        raise PilotfishError(
            "INTEGRATION_FAILED", "duplicate rollback changed path"
        )
    try:
        bundle_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise PilotfishError(
            "INTEGRATION_FAILED", "cannot create rollback bundle"
        ) from exc
    records: list[dict[str, Any]] = []
    frozen_records: list[RollbackRecord] = []
    missing_parents: set[str] = set()
    for index, relative in enumerate(normalized):
        source = repo / relative
        existed = os.path.lexists(source)
        record: dict[str, Any] = {"path": relative, "existed": existed}
        parent = source.parent
        while parent != repo:
            if not os.path.lexists(parent):
                missing_parents.add(parent.relative_to(repo).as_posix())
            parent = parent.parent
        if existed:
            try:
                stat_result = source.lstat()
                if not stat.S_ISREG(stat_result.st_mode):
                    raise PilotfishError(
                        "INTEGRATION_FAILED",
                        f"unsupported rollback path: {relative}",
                    )
                content = source.read_bytes()
            except PilotfishError:
                raise
            except OSError as exc:
                raise PilotfishError(
                    "INTEGRATION_FAILED",
                    f"cannot capture rollback path: {relative}",
                ) from exc
            payload = bundle_dir / f"{index:04d}.bin"
            try:
                payload.write_bytes(content)
            except OSError as exc:
                raise PilotfishError(
                    "INTEGRATION_FAILED",
                    f"cannot write rollback payload: {relative}",
                ) from exc
            record["payload"] = payload.name
            record["sha256"] = hashlib.sha256(content).hexdigest()
            record["mode"] = stat_result.st_mode & 0o7777
        records.append(record)
        frozen_records.append(
            RollbackRecord(
                path=relative,
                existed=existed,
                payload=record.get("payload"),
                sha256=record.get("sha256"),
                mode=record.get("mode"),
            )
        )
    manifest_path = bundle_dir / "rollback.json"
    manifest = {
        "records": records,
        "missing_parents": sorted(missing_parents),
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        manifest_path.write_bytes(manifest_bytes)
    except OSError as exc:
        raise PilotfishError(
            "INTEGRATION_FAILED", "cannot write rollback manifest"
        ) from exc
    return RollbackBundle(
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        records=tuple(frozen_records),
        missing_parents=tuple(sorted(missing_parents)),
    )


def rollback_manifest_integrity_error(
    bundle: RollbackBundle,
) -> PilotfishError | None:
    try:
        manifest_stat = bundle.manifest_path.lstat()
        if not stat.S_ISREG(manifest_stat.st_mode):
            return PilotfishError(
                "ROLLBACK_FAILED", "rollback manifest is not regular"
            )
        manifest_bytes = bundle.manifest_path.read_bytes()
    except OSError:
        return PilotfishError(
            "ROLLBACK_FAILED", "cannot read rollback manifest evidence"
        )
    if hashlib.sha256(manifest_bytes).hexdigest() != bundle.manifest_sha256:
        return PilotfishError(
            "ROLLBACK_FAILED", "rollback manifest digest mismatch"
        )
    return None


def load_trusted_rollback_payloads(
    bundle: RollbackBundle,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for record in bundle.records:
        if not record.existed:
            continue
        if record.payload is None or record.sha256 is None:
            raise PilotfishError(
                "ROLLBACK_FAILED", "trusted rollback record is incomplete"
            )
        payload = bundle.manifest_path.parent / record.payload
        try:
            payload_stat = payload.lstat()
            if not stat.S_ISREG(payload_stat.st_mode):
                raise PilotfishError(
                    "ROLLBACK_FAILED",
                    f"rollback payload is not regular: {record.path}",
                )
            content = payload.read_bytes()
        except PilotfishError:
            raise
        except OSError as exc:
            raise PilotfishError(
                "ROLLBACK_FAILED",
                f"cannot read rollback payload: {record.path}",
            ) from exc
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise PilotfishError(
                "ROLLBACK_FAILED",
                f"rollback payload hash mismatch: {record.path}",
            )
        payloads[record.path] = content
    return payloads


def verify_rollback_source(repo: Path, bundle: RollbackBundle) -> None:
    for record in bundle.records:
        target = repo / record.path
        if record.existed:
            if not os.path.lexists(target) or not stat.S_ISREG(
                target.lstat().st_mode
            ):
                raise PilotfishError(
                    "ROLLBACK_FAILED",
                    f"restored path is not regular: {target}",
                )
            try:
                content = target.read_bytes()
            except OSError as exc:
                raise PilotfishError(
                    "ROLLBACK_FAILED",
                    f"cannot read restored path: {target}",
                ) from exc
            if record.sha256 is None or record.mode is None:
                raise PilotfishError(
                    "ROLLBACK_FAILED", "trusted rollback record is incomplete"
                )
            if hashlib.sha256(content).hexdigest() != record.sha256:
                raise PilotfishError(
                    "ROLLBACK_FAILED",
                    f"restored byte hash mismatch: {target}",
                )
            if (target.lstat().st_mode & 0o7777) != record.mode:
                raise PilotfishError(
                    "ROLLBACK_FAILED", f"restored mode mismatch: {target}"
                )
        elif os.path.lexists(target):
            raise PilotfishError(
                "ROLLBACK_FAILED",
                f"originally absent path still exists: {target}",
            )
    for relative in bundle.missing_parents:
        if os.path.lexists(repo / relative):
            raise PilotfishError(
                "ROLLBACK_FAILED",
                f"created parent still exists: {relative}",
            )


def verify_rollback_bundle(repo: Path, bundle: RollbackBundle) -> None:
    integrity_error = rollback_manifest_integrity_error(bundle)
    if integrity_error is not None:
        raise integrity_error
    load_trusted_rollback_payloads(bundle)
    verify_rollback_source(repo, bundle)


def restore_rollback_bundle(repo: Path, bundle: RollbackBundle) -> None:
    integrity_error = rollback_manifest_integrity_error(bundle)
    payloads = load_trusted_rollback_payloads(bundle)
    try:
        for record in bundle.records:
            target = repo / record.path
            if record.existed:
                if record.mode is None:
                    raise PilotfishError(
                        "ROLLBACK_FAILED",
                        "trusted rollback record is incomplete",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".pilotfish-restore-", dir=target.parent
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payloads[record.path])
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, record.mode)
                    os.replace(temporary, target)
                finally:
                    if os.path.lexists(temporary):
                        temporary.unlink()
            elif os.path.lexists(target):
                if not stat.S_ISREG(target.lstat().st_mode):
                    raise PilotfishError(
                        "ROLLBACK_FAILED",
                        f"refusing to remove unexpected path: {target}",
                    )
                target.unlink()
        for relative in sorted(
            bundle.missing_parents,
            key=lambda value: len(PurePosixPath(value).parts),
            reverse=True,
        ):
            directory = repo / relative
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    except PilotfishError:
        raise
    except OSError as exc:
        raise PilotfishError(
            "ROLLBACK_FAILED", "rollback filesystem restore failed"
        ) from exc
    verify_rollback_source(repo, bundle)
    if integrity_error is not None:
        raise PilotfishError(
            "ROLLBACK_FAILED",
            "source restored from trusted metadata, but rollback artifact "
            "integrity was compromised",
            {
                "source_restored": True,
                "artifact_integrity_compromised": True,
                "integrity_error": str(integrity_error),
            },
        ) from integrity_error


def assert_source_unchanged(
    baseline: RepoBaseline,
    lock: "RepoLock",
) -> None:
    lock.assert_owned()
    if (
        git_text(
            baseline.root,
            "symbolic-ref",
            "--short",
            "HEAD",
            error_state="SOURCE_DRIFTED",
        )
        != baseline.branch
    ):
        raise PilotfishError("SOURCE_DRIFTED", "source branch drifted")
    if (
        git_text(
            baseline.root,
            "rev-parse",
            "HEAD",
            error_state="SOURCE_DRIFTED",
        )
        != baseline.base_sha
    ):
        raise PilotfishError("SOURCE_DRIFTED", "source HEAD drifted")
    if (
        git_text(
            baseline.root,
            "write-tree",
            error_state="SOURCE_DRIFTED",
        )
        != baseline.index_tree
    ):
        raise PilotfishError("SOURCE_DRIFTED", "source index drifted")
    reject_hidden_index_flags(baseline.root, "SOURCE_DRIFTED")
    if git(
        baseline.root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        error_state="SOURCE_DRIFTED",
    ).stdout:
        raise PilotfishError(
            "SOURCE_DRIFTED", "source working tree drifted"
        )
    assert_no_git_operation(baseline.git_dir, "SOURCE_DRIFTED")


def working_tree_hash(
    repo: Path,
    baseline: RepoBaseline,
    index_path: Path,
) -> str:
    try:
        index_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"cannot reset working-tree proof index: {index_path}",
        ) from exc
    index_path.parent.mkdir(parents=True, exist_ok=True)
    environment = git_env_with_index(index_path)
    git(
        repo,
        "read-tree",
        baseline.base_sha,
        env=environment,
        error_state="INTEGRATION_FAILED",
    )
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "add",
        "-A",
        "--",
        ".",
        env=environment,
        error_state="INTEGRATION_FAILED",
    )
    return git_text(
        repo,
        "write-tree",
        env=environment,
        error_state="INTEGRATION_FAILED",
    )


def assert_checkout_safe(baseline: RepoBaseline) -> None:
    tracked = nul_paths(git(baseline.root, "ls-files", "-z").stdout)
    for offset in range(0, len(tracked), 256):
        batch = tracked[offset : offset + 256]
        values = git(
            baseline.root,
            "check-attr",
            "-z",
            "--cached",
            "filter",
            "merge",
            "diff",
            "--",
            *batch,
        ).stdout.split(b"\0")
        if values and values[-1] == b"":
            values.pop()
        if len(values) % 3 != 0:
            raise PilotfishError(
                "PRECHECK_FAILED", "malformed cached attribute output"
            )
        for index in range(0, len(values), 3):
            path, attribute, value = values[index : index + 3]
            if value not in {b"unspecified", b"unset"}:
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "checkout attribute is unsupported: "
                    f"{os.fsdecode(path)}:{os.fsdecode(attribute)}",
                )


def create_worktrees(
    manifest: Manifest,
    baseline: RepoBaseline,
    roles: dict[str, RoleConfig],
    layout: RunLayout,
    state: dict[str, Any] | None = None,
    workers_out: list[WorkerRun] | None = None,
) -> list[WorkerRun]:
    assert_checkout_safe(baseline)
    workers = workers_out if workers_out is not None else []
    for job in sorted(manifest.jobs, key=lambda item: item.id):
        branch = f"pf/{manifest.run_id}/{job.id}"
        worktree = layout.worktrees / job.id
        if state is not None:
            register_intended_worktree(
                state,
                layout,
                worktree,
                f"refs/heads/{branch}",
                baseline.base_sha,
                kind="worker-pending",
            )
        git(
            baseline.root,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree),
            baseline.base_sha,
        )
        workers.append(
            WorkerRun(
                job=job,
                role=roles[job.role],
                worktree=worktree,
                branch=branch,
                process=None,
                started_monotonic=0.0,
                finished_monotonic=None,
                events_path=layout.artifacts / job.id / "events.jsonl",
                stderr_path=layout.artifacts / job.id / "stderr.log",
                final_path=layout.artifacts / job.id / "final.json",
                status="CREATED",
                snapshot_sha=None,
                snapshot_tree=None,
            )
        )
        if state is not None:
            refresh_owned_state_records(state, layout, workers=workers)
            persist_state(layout, state)
    if state is not None:
        register_intended_worktree(
            state,
            layout,
            layout.integration_worktree,
            f"refs/heads/{layout.integration_branch}",
            baseline.base_sha,
            kind="integration-pending",
        )
    git(
        baseline.root,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        "worktree",
        "add",
        "-b",
        layout.integration_branch,
        str(layout.integration_worktree),
        baseline.base_sha,
    )
    if state is not None:
        refresh_owned_state_records(state, layout, workers=workers)
        persist_state(layout, state)
    return workers


def validate_changed_paths(
    job: JobSpec, changed_paths: Sequence[str]
) -> None:
    for path in changed_paths:
        if not any(
            path_is_within(path, prefix) for prefix in job.allowed_paths
        ):
            raise PilotfishError(
                "QUARANTINED",
                f"job {job.id} changed path outside allowlist: {path}",
            )
        if any(path_is_within(path, prefix) for prefix in job.denied_paths):
            raise PilotfishError(
                "QUARANTINED", f"job {job.id} changed denied path: {path}"
            )


def candidate_paths_before_stage(
    worktree: Path, base_sha: str
) -> tuple[str, ...]:
    tracked = nul_paths(
        git(
            worktree,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base_sha,
        ).stdout
    )
    untracked = nul_paths(
        git(
            worktree,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).stdout
    )
    return tuple(sorted(set(tracked) | set(untracked)))


def assert_verified_integration_unchanged(
    integration: Path,
    expected_head: str,
    expected_tree: str,
) -> None:
    current_head = git_text(
        integration,
        "rev-parse",
        "HEAD",
        error_state="INTEGRATION_FAILED",
    )
    if current_head != expected_head:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "verified integration HEAD drifted before final apply",
        )
    current_tree = git_text(
        integration,
        "rev-parse",
        "HEAD^{tree}",
        error_state="INTEGRATION_FAILED",
    )
    if current_tree != expected_tree:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "verified integration tree drifted before final apply",
        )
    if git(
        integration,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        error_state="INTEGRATION_FAILED",
    ).stdout:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "verified integration worktree is not clean before final apply",
        )


def apply_integration_transactionally(
    baseline: RepoBaseline,
    layout: RunLayout,
    integration: Path,
    lock: "RepoLock",
    signals: DeferredSignals,
    *,
    expected_integration_head: str,
    expected_integration_tree: str,
) -> dict[str, Any]:
    assert_source_unchanged(baseline, lock)
    assert_verified_integration_unchanged(
        integration,
        expected_integration_head,
        expected_integration_tree,
    )
    policy = load_policy()
    patch_path = layout.artifacts / "combined.patch"
    patch = write_binary_patch(
        baseline,
        integration,
        patch_path,
        policy,
        end_ref=expected_integration_head,
    )
    changed_paths = nul_paths(
        git(
            integration,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            baseline.base_sha,
            expected_integration_head,
            error_state="INTEGRATION_FAILED",
        ).stdout
    )
    if len(changed_paths) > policy.max_changed_files:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "combined changed-file limit exceeded",
        )
    prove_patch_tree(
        baseline.root,
        baseline,
        patch,
        layout.root / "preflight.index",
        expected_integration_tree,
    )
    git(
        baseline.root,
        "apply",
        "--check",
        "--binary",
        "-",
        input_bytes=patch.bytes,
        error_state="INTEGRATION_FAILED",
    )
    rollback_bundle = create_rollback_bundle(
        baseline.root, changed_paths, layout.rollback
    )
    # This is deliberately outside the rollback handler: no source write has
    # occurred yet, so newly observed external drift must be preserved rather
    # than overwritten with the earlier rollback snapshot.
    assert_source_unchanged(baseline, lock)
    try:
        git(
            baseline.root,
            "apply",
            "--binary",
            "-",
            input_bytes=patch.bytes,
            error_state="INTEGRATION_FAILED",
        )
        applied_tree = working_tree_hash(
            baseline.root,
            baseline,
            layout.root / "verify.index",
        )
        if applied_tree != expected_integration_tree:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "post-apply tree mismatch: "
                f"{applied_tree} != {expected_integration_tree}",
            )
        actual_paths = candidate_paths_before_stage(
            baseline.root, baseline.base_sha
        )
        if tuple(sorted(actual_paths)) != tuple(sorted(changed_paths)):
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "post-apply changed-path set mismatch",
            )
        if (
            git_text(
                baseline.root,
                "write-tree",
                error_state="INTEGRATION_FAILED",
            )
            != baseline.index_tree
        ):
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "real index changed during final apply",
            )
        # combined.patch is public evidence, but never execution input after
        # write_binary_patch.  Recheck it at the last success gate so a path
        # swap cannot be silently reported as the artifact that was applied.
        verify_patch_evidence(patch)
        pending_signals = signals.pending()
        if pending_signals:
            names = ",".join(
                item.name for item in sorted(pending_signals, key=int)
            )
            raise PilotfishError(
                "CANCELLED",
                f"run received {names} during final apply",
            )
    except BaseException as exc:
        try:
            restore_rollback_bundle(baseline.root, rollback_bundle)
            assert_source_unchanged(baseline, lock)
        except BaseException as rollback_exc:
            if (
                isinstance(rollback_exc, PilotfishError)
                and rollback_exc.details.get("source_restored") is True
                and rollback_exc.details.get(
                    "artifact_integrity_compromised"
                )
                is True
            ):
                raise PilotfishError(
                    "ROLLBACK_FAILED",
                    "exact rollback restored the source tree, but rollback "
                    "artifact integrity was compromised",
                    rollback_exc.details,
                ) from rollback_exc
            raise PilotfishError(
                "ROLLBACK_FAILED",
                "exact rollback could not prove the original source tree",
            ) from rollback_exc
        if isinstance(exc, PilotfishError) and exc.state == "CANCELLED":
            raise
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "final apply failed; exact rollback restored the source tree",
        ) from exc
    return {
        "patch_sha256": patch.sha256,
        "applied_tree": applied_tree,
        "rollback_manifest": str(rollback_bundle.manifest_path),
        "rollback_manifest_sha256": rollback_bundle.manifest_sha256,
        # Non-JSON internal capability. Task 7 must remove this key before
        # durable/public state serialization and retain it for persist-failure
        # rollback until APPLIED is durably replaced.
        "_rollback_bundle_internal": rollback_bundle,
    }


def reject_unsafe_attributes_and_modes_before_stage(
    worktree: Path,
    base_sha: str,
    changed_paths: Sequence[str],
) -> None:
    if ".gitattributes" in changed_paths or any(
        path.endswith("/.gitattributes") for path in changed_paths
    ):
        raise PilotfishError(
            "QUARANTINED", "changing .gitattributes is unsupported"
        )
    attributes = git(
        worktree,
        "check-attr",
        "-z",
        "filter",
        "merge",
        "diff",
        "--",
        *changed_paths,
    ).stdout.split(b"\0")
    if attributes and attributes[-1] == b"":
        attributes.pop()
    if len(attributes) % 3 != 0:
        raise PilotfishError(
            "QUARANTINED", "malformed git check-attr output"
        )
    for index in range(0, len(attributes), 3):
        path, _attribute, value = attributes[index : index + 3]
        if value not in {b"unspecified", b"unset"}:
            raise PilotfishError(
                "QUARANTINED",
                "custom Git filter is unsupported for "
                f"{os.fsdecode(path)}: {os.fsdecode(value)}",
            )


def reject_unsupported_raw_modes(worktree: Path, base_sha: str) -> None:
    raw = git(
        worktree,
        "diff",
        "--cached",
        "--raw",
        "--no-renames",
        "-z",
        base_sha,
    ).stdout
    for record in raw.split(b"\0"):
        if not record.startswith(b":"):
            continue
        fields = record[1:].split()
        if len(fields) < 5:
            raise PilotfishError(
                "QUARANTINED", "malformed raw diff record"
            )
        old_mode, new_mode = fields[0], fields[1]
        supported = {b"000000", b"100644", b"100755"}
        if old_mode not in supported or new_mode not in supported:
            raise PilotfishError(
                "QUARANTINED", "non-regular Git mode is unsupported"
            )


def enforce_patch_limits(
    worktree: Path,
    base_sha: str,
    changed_paths: Sequence[str],
    policy: InvocationPolicy,
) -> None:
    if len(changed_paths) > policy.max_changed_files:
        raise PilotfishError(
            "QUARANTINED", "changed-file limit exceeded"
        )
    patch = git(
        worktree,
        "diff",
        "--cached",
        "--binary",
        "--full-index",
        "--no-renames",
        base_sha,
    ).stdout
    if len(patch) > policy.max_patch_bytes:
        raise PilotfishError("QUARANTINED", "patch byte limit exceeded")


def snapshot_worker(
    worker: WorkerRun,
    baseline: RepoBaseline,
    layout: RunLayout,
) -> None:
    del layout
    head = git_text(worker.worktree, "rev-parse", "HEAD")
    if head != baseline.base_sha:
        raise PilotfishError(
            "QUARANTINED", f"worker {worker.job.id} moved HEAD"
        )
    reject_hidden_index_flags(worker.worktree, "QUARANTINED")
    ignored = git(
        worker.worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--ignored=matching",
    ).stdout
    if any(
        record.startswith(b"!! ")
        for record in ignored.split(b"\0")
        if record
    ):
        raise PilotfishError(
            "QUARANTINED",
            f"worker {worker.job.id} created ignored files",
        )
    status_output = git(
        worker.worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout
    if worker.role.name != "executor":
        if status_output:
            raise PilotfishError(
                "QUARANTINED",
                f"read-only worker {worker.job.id} modified its worktree",
            )
        worker.status = "DONE"
        return
    if not status_output:
        raise PilotfishError(
            "QUARANTINED",
            f"executor {worker.job.id} produced an empty patch",
        )
    candidates = candidate_paths_before_stage(
        worker.worktree, baseline.base_sha
    )
    validate_changed_paths(worker.job, candidates)
    reject_unsafe_attributes_and_modes_before_stage(
        worker.worktree, baseline.base_sha, candidates
    )
    git(
        worker.worktree,
        "-c",
        "core.hooksPath=/dev/null",
        "add",
        "-A",
        "--",
        ".",
    )
    changed = nul_paths(
        git(
            worker.worktree,
            "diff",
            "--cached",
            "--name-only",
            "--no-renames",
            "-z",
            baseline.base_sha,
        ).stdout
    )
    validate_changed_paths(worker.job, changed)
    if tuple(sorted(candidates)) != tuple(sorted(changed)):
        raise PilotfishError(
            "QUARANTINED",
            f"candidate/staged path mismatch for {worker.job.id}",
        )
    reject_unsupported_raw_modes(worker.worktree, baseline.base_sha)
    reported_paths = worker.validated_result.get("changed_paths")
    if not isinstance(reported_paths, list) or tuple(
        sorted(reported_paths)
    ) != tuple(sorted(changed)):
        raise PilotfishError(
            "QUARANTINED",
            f"reported/actual changed path mismatch for {worker.job.id}",
        )
    enforce_patch_limits(
        worker.worktree, baseline.base_sha, changed, load_policy()
    )
    commit = git(
        worker.worktree,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=Pilotfish Supervisor",
        "-c",
        "user.email=pilotfish@localhost",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--no-verify",
        "-m",
        f"pilotfish snapshot {worker.job.id}",
        check=False,
    )
    if commit.returncode != 0:
        raise PilotfishError(
            "QUARANTINED",
            f"snapshot commit failed for {worker.job.id}",
        )
    worker.snapshot_sha = git_text(worker.worktree, "rev-parse", "HEAD")
    worker.snapshot_tree = git_text(
        worker.worktree, "rev-parse", "HEAD^{tree}"
    )
    worker.status = "SNAPSHOT_READY"


def minimal_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR"}
    secret_fragments = (
        "key",
        "secret",
        "token",
        "credential",
        "password",
        "canary",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowed
        and not any(
            fragment in key.lower() for fragment in secret_fragments
        )
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def run_argv(
    command: CommandSpec,
    cwd: Path,
    output_dir: Path,
    policy: InvocationPolicy,
) -> dict[str, Any]:
    executable = Path(command.argv[0]).name
    if command.effect_scope != "repo-local":
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "external-state verification requires main-loop handling",
        )
    if executable in policy.forbidden_executables:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"external-state command is forbidden: {executable}",
        )
    if any(
        token in argument
        for token in policy.forbidden_argv_tokens
        for argument in command.argv
    ):
        raise PilotfishError(
            "INTEGRATION_FAILED",
            "verification argv contains a forbidden external-state token",
        )
    process = subprocess.Popen(
        list(command.argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=minimal_environment(),
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + command.timeout_seconds
    try:
        while True:
            cancellation_checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PilotfishError(
                    "INTEGRATION_FAILED",
                    f"verification command timed out: {command.id}",
                )
            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, remaining)
                )
                break
            except subprocess.TimeoutExpired as timeout_exc:
                observed = len(timeout_exc.output or b"") + len(
                    timeout_exc.stderr or b""
                )
                if observed > policy.max_command_output_bytes:
                    raise PilotfishError(
                        "INTEGRATION_FAILED",
                        "verification output limit exceeded: "
                        f"{command.id}",
                    )
        if len(stdout) + len(stderr) > policy.max_command_output_bytes:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                f"verification output limit exceeded: {command.id}",
            )
        if process_group_exists_os(process.pid):
            terminate_process_group(process, 2.0)
            raise PilotfishError(
                "INTEGRATION_FAILED",
                f"verification leaked a child process: {command.id}",
            )
    except BaseException:
        try:
            terminate_process_group(process, 2.0)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        raise
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{command.id}.stdout").write_bytes(stdout)
    (output_dir / f"{command.id}.stderr").write_bytes(stderr)
    result = {
        "id": command.id,
        "argv": list(command.argv),
        "exit_code": process.returncode,
    }
    if process.returncode != 0:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"verification command failed: {result!r}",
        )
    return result


def refs_digest(repo: Path) -> str:
    raw = git(
        repo,
        "for-each-ref",
        "--sort=refname",
        "--format=%(refname)%00%(objectname)",
    ).stdout
    return hashlib.sha256(raw).hexdigest()


def create_isolated_verification_clone(
    baseline: RepoBaseline,
    layout: RunLayout,
    label: str,
    commit: str,
) -> Path:
    assert_checkout_safe(baseline)
    path = layout.verification_repos / normalize_prefix(label)
    if path.exists():
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"verification clone already exists: {path}",
        )
    try:
        git(
            baseline.root,
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--no-local",
            "--no-checkout",
            "--",
            str(baseline.root),
            str(path),
        )
        git(
            path,
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--detach",
            commit,
        )
        git(path, "remote", "remove", "origin")
    except BaseException:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        raise
    return path


def remove_verification_clone(path: Path, layout: RunLayout) -> None:
    if (
        path.parent != layout.verification_repos
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise PilotfishError(
            "QUARANTINED", f"refusing verification clone cleanup: {path}"
        )
    shutil.rmtree(path)


def assert_baseline_intact(baseline: RepoBaseline) -> None:
    if (
        git_text(baseline.root, "symbolic-ref", "--short", "HEAD")
        != baseline.branch
    ):
        raise PilotfishError(
            "SOURCE_DRIFTED", "source branch changed during verification"
        )
    if git_text(baseline.root, "rev-parse", "HEAD") != baseline.base_sha:
        raise PilotfishError(
            "SOURCE_DRIFTED", "source HEAD changed during verification"
        )
    if git(
        baseline.root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout:
        raise PilotfishError(
            "SOURCE_DRIFTED", "source tree changed during verification"
        )
    if git_text(baseline.root, "write-tree") != baseline.index_tree:
        raise PilotfishError(
            "SOURCE_DRIFTED", "source index changed during verification"
        )


def verify_worker_snapshot(
    worker: WorkerRun,
    baseline: RepoBaseline,
    layout: RunLayout,
    policy: InvocationPolicy,
) -> list[dict[str, Any]]:
    commit = (
        worker.snapshot_sha
        if worker.role.name == "executor"
        else baseline.base_sha
    )
    if commit is None:
        raise PilotfishError(
            "INTEGRATION_FAILED",
            f"missing verification commit: {worker.job.id}",
        )
    verification = create_isolated_verification_clone(
        baseline, layout, f"job-{worker.job.id}", commit
    )
    before_refs = refs_digest(baseline.root)
    try:
        results = [
            run_argv(
                command,
                verification,
                layout.artifacts / "job-checks" / worker.job.id,
                policy,
            )
            for command in worker.job.verification_commands
        ]
        if refs_digest(baseline.root) != before_refs:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                f"job check changed Git refs: {worker.job.id}",
            )
        assert_baseline_intact(baseline)
        return results
    finally:
        remove_verification_clone(verification, layout)


def integrate_snapshots(
    workers: Sequence[WorkerRun],
    baseline: RepoBaseline,
    layout: RunLayout,
    commands: Sequence[CommandSpec],
    policy: InvocationPolicy,
) -> tuple[Path, list[dict[str, Any]]]:
    for worker in sorted(workers, key=lambda item: item.job.id):
        if worker.role.name != "executor":
            continue
        if worker.status != "SNAPSHOT_READY" or worker.snapshot_sha is None:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                f"worker {worker.job.id} lacks a valid snapshot",
            )
        merged = git(
            layout.integration_worktree,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Pilotfish Supervisor",
            "-c",
            "user.email=pilotfish@localhost",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "merge.gpgSign=false",
            "merge",
            "--no-ff",
            "--no-edit",
            worker.snapshot_sha,
            check=False,
        )
        if merged.returncode != 0:
            git(
                layout.integration_worktree,
                "merge",
                "--abort",
                check=False,
            )
            if git(
                layout.integration_worktree,
                "status",
                "--porcelain=v1",
                "-z",
            ).stdout:
                raise PilotfishError(
                    "INTEGRATION_FAILED",
                    "merge conflict cleanup did not restore a clean "
                    "integration worktree",
                )
            raise PilotfishError(
                "INTEGRATION_FAILED",
                f"merge conflict for worker {worker.job.id}",
            )
    integration_head = git_text(
        layout.integration_worktree, "rev-parse", "HEAD"
    )
    integration_tree = git_text(
        layout.integration_worktree, "rev-parse", "HEAD^{tree}"
    )
    verification_worktree = create_isolated_verification_clone(
        baseline, layout, "integration", integration_head
    )
    refs_before = refs_digest(baseline.root)
    try:
        results = [
            run_argv(
                command,
                verification_worktree,
                layout.artifacts / "integration",
                policy,
            )
            for command in commands
        ]
        if refs_digest(baseline.root) != refs_before:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "verification command changed Git refs",
            )
        assert_baseline_intact(baseline)
        if (
            git_text(layout.integration_worktree, "rev-parse", "HEAD")
            != integration_head
        ):
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "integration HEAD drifted during verification",
            )
        if (
            git_text(
                layout.integration_worktree, "rev-parse", "HEAD^{tree}"
            )
            != integration_tree
        ):
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "integration tree drifted during verification",
            )
        if git(
            layout.integration_worktree,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout:
            raise PilotfishError(
                "INTEGRATION_FAILED",
                "integration worktree is not frozen and clean",
            )
        return layout.integration_worktree, results
    finally:
        remove_verification_clone(verification_worktree, layout)


class RepoLock(AbstractContextManager["RepoLock"]):
    def __init__(
        self,
        common_dir: Path,
        run_id: str,
        expected_common_dir_device_inode: tuple[int, int],
    ) -> None:
        self.common_dir = common_dir
        self.path = common_dir / "pilotfish-parallel.lock"
        self.run_id = run_id
        self.expected_common_dir_device_inode = (
            expected_common_dir_device_inode
        )
        self.handle: Any = None
        self.device_inode: tuple[int, int] | None = None

    def __enter__(self) -> "RepoLock":
        try:
            common_dir_descriptor = os.open(
                self.common_dir,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC,
            )
        except OSError as exc:
            raise PilotfishError(
                "PRECHECK_FAILED",
                f"cannot safely open repository common directory: {exc}",
            ) from exc
        try:
            common_dir_stat = os.fstat(common_dir_descriptor)
            observed_common_dir_device_inode = (
                common_dir_stat.st_dev,
                common_dir_stat.st_ino,
            )
            if (
                not stat.S_ISDIR(common_dir_stat.st_mode)
                or observed_common_dir_device_inode
                != self.expected_common_dir_device_inode
            ):
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "repository common directory identity changed",
                )
            try:
                descriptor = os.open(
                    self.path.name,
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=common_dir_descriptor,
                )
            except OSError as exc:
                raise PilotfishError(
                    "PRECHECK_FAILED", f"cannot safely open run lock: {exc}"
                ) from exc
            stat_result = os.fstat(descriptor)
            if (
                not stat.S_ISREG(stat_result.st_mode)
                or stat_result.st_uid != os.getuid()
                or stat_result.st_nlink != 1
                or stat.S_IMODE(stat_result.st_mode) & 0o077
            ):
                os.close(descriptor)
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "run lock must be owner-only regular file",
                )
            self.handle = os.fdopen(descriptor, "r+", encoding="utf-8")
            try:
                fcntl.flock(
                    self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError as exc:
                self.handle.close()
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "another Pilotfish run holds the active run lock",
                ) from exc
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(self.run_id + "\n")
            self.handle.flush()
            os.fsync(self.handle.fileno())
            stat_result = os.fstat(self.handle.fileno())
            self.device_inode = (stat_result.st_dev, stat_result.st_ino)
            try:
                path_stat = os.stat(
                    self.path.name,
                    dir_fd=common_dir_descriptor,
                    follow_symlinks=False,
                )
                current_common_dir_stat = os.stat(
                    self.common_dir,
                    follow_symlinks=False,
                )
            except OSError as exc:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
                self.handle = None
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    f"run lock path changed during acquisition: {exc}",
                ) from exc
            current_common_dir_device_inode = (
                current_common_dir_stat.st_dev,
                current_common_dir_stat.st_ino,
            )
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or self.device_inode != (path_stat.st_dev, path_stat.st_ino)
                or not stat.S_ISDIR(current_common_dir_stat.st_mode)
                or current_common_dir_device_inode
                != self.expected_common_dir_device_inode
            ):
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
                self.handle = None
                raise PilotfishError(
                    "PRECHECK_FAILED",
                    "run lock or common directory identity changed during acquisition",
                )
            return self
        finally:
            os.close(common_dir_descriptor)

    def assert_owned(self) -> None:
        if (
            self.handle is None
            or self.handle.closed
            or self.device_inode is None
        ):
            raise PilotfishError("SOURCE_DRIFTED", "run lock is not open")
        fd_stat = os.fstat(self.handle.fileno())
        try:
            common_dir_stat = os.stat(
                self.common_dir,
                follow_symlinks=False,
            )
            path_stat = self.path.lstat()
        except OSError as exc:
            raise PilotfishError(
                "SOURCE_DRIFTED",
                f"run lock or common directory path is unavailable: {exc}",
            ) from exc
        common_dir_device_inode = (
            common_dir_stat.st_dev,
            common_dir_stat.st_ino,
        )
        if (
            not stat.S_ISDIR(common_dir_stat.st_mode)
            or common_dir_device_inode
            != self.expected_common_dir_device_inode
        ):
            raise PilotfishError(
                "SOURCE_DRIFTED",
                "repository common directory inode was replaced",
            )
        observed = (fd_stat.st_dev, fd_stat.st_ino)
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or observed != self.device_inode
            or observed != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise PilotfishError(
                "SOURCE_DRIFTED", "run lock inode was replaced"
            )
        try:
            probe_descriptor = os.open(
                self.path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except OSError as exc:
            raise PilotfishError(
                "SOURCE_DRIFTED", "cannot probe run lock ownership"
            ) from exc
        try:
            probe_stat = os.fstat(probe_descriptor)
            if (
                not stat.S_ISREG(probe_stat.st_mode)
                or (probe_stat.st_dev, probe_stat.st_ino) != self.device_inode
            ):
                raise PilotfishError(
                    "SOURCE_DRIFTED", "run lock inode changed during probe"
                )
            try:
                fcntl.flock(
                    probe_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError:
                pass
            except OSError as exc:
                raise PilotfishError(
                    "SOURCE_DRIFTED", "cannot probe held run lock"
                ) from exc
            else:
                fcntl.flock(probe_descriptor, fcntl.LOCK_UN)
                raise PilotfishError(
                    "SOURCE_DRIFTED",
                    "run lock is no longer held by this handle",
                )
            try:
                fcntl.flock(
                    self.handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except OSError as exc:
                raise PilotfishError(
                    "SOURCE_DRIFTED",
                    "run lock is no longer held by this handle",
                ) from exc
        finally:
            os.close(probe_descriptor)
        self.handle.seek(0)
        if self.handle.read() != self.run_id + "\n":
            raise PilotfishError(
                "SOURCE_DRIFTED", "run lock ownership content drifted"
            )

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        if self.handle is not None:
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.flush()
            os.fsync(self.handle.fileno())
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def capture_worker_evidence(
    workers: Sequence[WorkerRun],
) -> list[dict[str, Any]]:
    return [
        {
            "job_id": worker.job.id,
            "role": worker.role.name,
            "status": worker.status,
            "pid": worker.process.pid if worker.process else None,
            "pgid": worker.process.pid if worker.process else None,
            "thread_id": worker.thread_id,
            "model": worker.runtime_metadata.get("model"),
            "effort": worker.runtime_metadata.get("effort"),
            "sandbox": worker.runtime_metadata.get("sandbox"),
            "attestation": worker.runtime_metadata.get("evidence"),
            "started": worker.started_monotonic,
            "finished": worker.finished_monotonic,
            "exit_code": worker.exit_code,
            "usage": worker.validated_result.get("usage", {}),
            "changed_paths": worker.validated_result.get(
                "changed_paths", []
            ),
            "events_path": str(worker.events_path),
            "stderr_path": str(worker.stderr_path),
            "final_path": str(worker.final_path),
            "snapshot_sha": worker.snapshot_sha,
            "snapshot_tree": worker.snapshot_tree,
        }
        for worker in workers
    ]


def transition_state(
    state: dict[str, Any],
    layout: RunLayout,
    new_state: str,
    **evidence: Any,
) -> None:
    if new_state not in RUN_STATES:
        raise PilotfishError(
            "QUARANTINED", f"invalid transition state: {new_state}"
        )
    serializable_evidence = {
        key: capture_worker_evidence(value)
        if key == "workers"
        else value
        for key, value in evidence.items()
    }
    state["state"] = new_state
    state["transitions"].append(
        {
            "state": new_state,
            "monotonic": time.monotonic(),
            "evidence": serializable_evidence,
        }
    )
    refresh_owned_state_records(
        state, layout, workers=evidence.get("workers")
    )
    persist_state(layout, state)


def run_final_verifier(
    manifest: Manifest,
    role: RoleConfig,
    integration: Path,
    layout: RunLayout,
    checks: Sequence[dict[str, Any]],
    codex_prefix: Sequence[str],
    policy: InvocationPolicy,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        role.name != "verifier"
        or (role.model, role.effort, role.sandbox)
        != APPROVED_ROLE_EXECUTION_CONTRACTS["verifier"]
    ):
        raise PilotfishError(
            "PRECHECK_FAILED", "final verifier execution contract drift"
        )
    integration_head = git_text(integration, "rev-parse", "HEAD")
    integration_tree = git_text(
        integration, "rev-parse", "HEAD^{tree}"
    )
    changed_paths = nul_paths(
        git(
            integration,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            manifest.base_sha,
            integration_head,
        ).stdout
    )
    diff = git(
        integration,
        "diff",
        "--binary",
        "--full-index",
        "--no-renames",
        manifest.base_sha,
        integration_head,
    ).stdout
    diff_text = (
        diff.decode("utf-8", errors="replace")
        if len(diff) <= policy.max_result_bytes // 2
        else None
    )
    evidence = {
        "task_requirement": manifest.task_requirement,
        "completion_claim": manifest.completion_claim,
        "overall_acceptance_criteria": list(
            manifest.overall_acceptance_criteria
        ),
        "requirements": [
            dataclasses.asdict(job) for job in manifest.jobs
        ],
        "base_sha": manifest.base_sha,
        "integration_head": integration_head,
        "integration_tree": integration_tree,
        "changed_paths": list(changed_paths),
        "checks": list(checks),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "diff": diff_text,
        "oversize_diff_instruction": (
            None
            if diff_text is not None
            else "Run git diff --binary "
            f"{manifest.base_sha} {integration_head} in this worktree."
        ),
    }
    job = JobSpec(
        id="final-verifier",
        role="verifier",
        goal=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        allowed_paths=(),
        denied_paths=(),
        acceptance_criteria=(
            "Adversarially verify every supplied requirement and the "
            "actual frozen integration tree.",
        ),
        verification_commands=(),
        timeout_seconds=policy.final_verifier_timeout_seconds,
    )
    verifier_manifest = dataclasses.replace(
        manifest, max_parallel=1, jobs=(job,)
    )
    worker = WorkerRun(
        job=job,
        role=role,
        worktree=integration,
        branch=layout.integration_branch,
        process=None,
        started_monotonic=0.0,
        finished_monotonic=None,
        events_path=layout.artifacts / "final-verifier" / "events.jsonl",
        stderr_path=layout.artifacts / "final-verifier" / "stderr.log",
        final_path=layout.artifacts / "final-verifier" / "final.json",
        status="CREATED",
        snapshot_sha=None,
        snapshot_tree=None,
    )
    run_bounded_workers(
        (worker,),
        verifier_manifest,
        codex_prefix,
        policy,
        state=state,
        layout=layout,
    )
    result = validate_worker_result(
        worker, verifier_manifest, integration_head, policy
    )
    result["runtime_evidence"] = capture_worker_evidence((worker,))[0]
    if git_text(integration, "rev-parse", "HEAD") != integration_head:
        raise PilotfishError(
            "VERIFICATION_FAILED", "final verifier moved integration HEAD"
        )
    if (
        git_text(integration, "rev-parse", "HEAD^{tree}")
        != integration_tree
    ):
        raise PilotfishError(
            "VERIFICATION_FAILED", "final verifier changed integration tree"
        )
    if git(
        integration,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout:
        raise PilotfishError(
            "VERIFICATION_FAILED",
            "final verifier dirtied integration worktree",
        )
    return result


def cleanup_command(repo_root: Path, run_id: str) -> str:
    return shlex.join(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "runner.py"),
            "cleanup",
            "--repo-root",
            str(repo_root),
            "--run-id",
            run_id,
        ]
    )


def persist_failure_state_best_effort(
    layout: RunLayout,
    state: dict[str, Any],
    workers: Sequence[WorkerRun],
) -> None:
    try:
        refresh_owned_state_records(state, layout, workers=workers)
        persist_state(layout, state)
    except BaseException as persist_exc:
        state["failure_state_persist_error"] = (
            f"{type(persist_exc).__name__}: {persist_exc}"
        )


def run_manifest(
    manifest_path: Path,
    codex_prefix: Sequence[str] = ("codex",),
) -> dict[str, Any]:
    prefix = validate_codex_prefix(codex_prefix)
    if prefix != ("codex",) and not os.environ.get("PILOTFISH_TESTING"):
        raise PilotfishError(
            "PRECHECK_FAILED",
            "production Codex prefix must be exactly ('codex',)",
        )
    manifest = load_manifest(manifest_path)
    roles = load_roles()
    policy = load_policy()
    baseline = preflight_repo(manifest)
    layout: RunLayout | None = None
    state: dict[str, Any] | None = None
    workers: list[WorkerRun] = []
    with CancellationController() as cancellation, RepoLock(
        baseline.common_dir,
        manifest.run_id,
        baseline.common_dir_device_inode,
    ) as lock:
        try:
            layout = create_layout(manifest)
            state = initialize_run_state(
                manifest, baseline, layout, lock
            )
            create_worktrees(
                manifest,
                baseline,
                roles,
                layout,
                state=state,
                workers_out=workers,
            )
            transition_state(
                state,
                layout,
                "PARALLEL_RUNNING",
                workers=workers,
            )
            scheduler = run_bounded_workers(
                workers,
                manifest,
                prefix,
                policy,
                state=state,
                layout=layout,
            )
            for worker in workers:
                validate_worker_result(
                    worker, manifest, baseline.base_sha, policy
                )
                if (
                    worker.validated_result["status"]
                    not in worker.role.success_statuses
                ):
                    raise PilotfishError(
                        "WORKER_FAILED",
                        "worker returned non-success terminal status: "
                        f"{worker.job.id}:"
                        f"{worker.validated_result['status']}",
                    )
                snapshot_worker(worker, baseline, layout)

            job_checks = {
                worker.job.id: verify_worker_snapshot(
                    worker, baseline, layout, policy
                )
                for worker in workers
            }
            transition_state(
                state,
                layout,
                "SNAPSHOTS_READY",
                workers=workers,
                job_checks=job_checks,
            )
            integration, integration_checks = integrate_snapshots(
                workers,
                baseline,
                layout,
                manifest.integration_verification_commands,
                policy,
            )
            all_checks = {
                "jobs": job_checks,
                "integration": integration_checks,
                "runtime": {
                    "scheduler": scheduler,
                    "workers": capture_worker_evidence(workers),
                },
            }
            transition_state(
                state,
                layout,
                "INTEGRATED",
                workers=workers,
                checks=all_checks,
            )
            expected_integration_head = git_text(
                integration, "rev-parse", "HEAD"
            )
            expected_integration_tree = git_text(
                integration, "rev-parse", "HEAD^{tree}"
            )
            verifier = run_final_verifier(
                manifest,
                roles["verifier"],
                integration,
                layout,
                (
                    {"scope": "jobs", "results": job_checks},
                    {
                        "scope": "integration",
                        "results": integration_checks,
                    },
                    {
                        "scope": "runtime",
                        "scheduler": scheduler,
                        "workers": capture_worker_evidence(workers),
                    },
                ),
                prefix,
                policy,
                state=state,
            )
            if verifier["status"] != "CONFIRMED":
                raise PilotfishError(
                    "VERIFICATION_FAILED",
                    f"final verifier returned {verifier['status']}",
                )
            transition_state(
                state,
                layout,
                "VERIFIED",
                workers=workers,
                verifier=verifier,
            )
            cancellation_checkpoint()
            with DeferredSignals(cancellation) as deferred:
                applied = apply_integration_transactionally(
                    baseline,
                    layout,
                    integration,
                    lock,
                    deferred,
                    expected_integration_head=expected_integration_head,
                    expected_integration_tree=expected_integration_tree,
                )
                rollback_bundle = applied.pop(
                    "_rollback_bundle_internal"
                )
                if not isinstance(rollback_bundle, RollbackBundle):
                    raise PilotfishError(
                        "ROLLBACK_FAILED",
                        "transaction returned an invalid rollback capability",
                    )
                try:
                    applied_state = copy.deepcopy(state)
                    applied_state.update(
                        state="APPLIED",
                        commit_point=True,
                        cleanup_required=True,
                        failure=None,
                        result={
                            "runner_identity": {
                                "skill_root": str(SKILL_ROOT),
                                "runner_sha256": hashlib.sha256(
                                    Path(__file__).read_bytes()
                                ).hexdigest(),
                            },
                            "workers": capture_worker_evidence(workers),
                            "scheduler": scheduler,
                            "checks": all_checks,
                            "verification": verifier,
                            **applied,
                        },
                    )
                    refresh_owned_state_records(
                        applied_state, layout, workers=workers
                    )
                    persist_state(layout, applied_state)
                except BaseException as persist_exc:
                    try:
                        restore_rollback_bundle(
                            baseline.root, rollback_bundle
                        )
                        assert_source_unchanged(baseline, lock)
                    except BaseException as rollback_exc:
                        state.update(
                            state="ROLLBACK_FAILED",
                            commit_point=False,
                            failure={
                                "type": type(rollback_exc).__name__,
                                "message": str(rollback_exc),
                            },
                        )
                        persist_failure_state_best_effort(
                            layout, state, workers
                        )
                        raise PilotfishError(
                            "ROLLBACK_FAILED",
                            "APPLIED persistence and exact rollback both "
                            "failed; emergency stop",
                        ) from rollback_exc
                    raise PilotfishError(
                        "INTEGRATION_FAILED",
                        "APPLIED state persistence failed; source was "
                        "rolled back",
                    ) from persist_exc
                state = applied_state

            deferred_signals = [
                item.name
                for item in sorted(deferred.received, key=int)
            ]
            result = {
                "status": "APPLIED",
                "run_id": manifest.run_id,
                "state_path": str(layout.state_path),
                "cleanup_required": True,
                "deferred_signals": deferred_signals,
                **(state["result"] or {}),
            }
            try:
                cleanup = cleanup_run(
                    baseline.root,
                    layout.state_path,
                    require_finished=True,
                    lock=lock,
                )
            except BaseException as cleanup_exc:
                result["cleanup_warning"] = (
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
                result["cleanup_command"] = cleanup_command(
                    baseline.root, manifest.run_id
                )
                return result
            result["cleanup_required"] = False
            result["cleanup"] = cleanup
            return result
        except BaseException as exc:
            termination_error: str | None = None
            try:
                terminate_and_wait_live_workers(workers)
            except PilotfishError as terminate_exc:
                termination_error = str(terminate_exc)

            details = {
                "run_id": manifest.run_id,
                "state_path": (
                    str(layout.state_path) if layout is not None else None
                ),
                "artifacts": (
                    str(layout.artifacts) if layout is not None else None
                ),
                "cleanup_command": cleanup_command(
                    baseline.root, manifest.run_id
                ),
            }
            if (
                state is not None
                and layout is not None
                and not state.get("commit_point", False)
            ):
                failure_state = (
                    exc.state
                    if isinstance(exc, PilotfishError)
                    else "WORKER_FAILED"
                )
                state.update(
                    state=failure_state,
                    cleanup_required=True,
                    failure={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                if termination_error is not None:
                    state["failure"]["termination_error"] = (
                        termination_error
                    )
                persist_failure_state_best_effort(
                    layout, state, workers
                )
            elif state is not None and state.get("commit_point", False):
                return {
                    "status": "APPLIED",
                    "run_id": manifest.run_id,
                    "state_path": (
                        str(layout.state_path)
                        if layout is not None
                        else None
                    ),
                    "cleanup_required": True,
                    "cleanup_warning": (
                        "post-commit exception: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "cleanup_command": cleanup_command(
                        baseline.root, manifest.run_id
                    ),
                    **(state.get("result") or {}),
                }
            if isinstance(exc, PilotfishError):
                exc.details.update(details)
                raise
            raise PilotfishError(
                "WORKER_FAILED", str(exc), details
            ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pilotfish-parallel")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--manifest", type=Path, required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--repo-root", type=Path, required=True)
    cleanup_parser.add_argument("--run-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            manifest = load_manifest(args.manifest.resolve())
            baseline = preflight_repo(manifest)
            result = {
                "status": "VALID",
                "repo_root": str(baseline.root),
                "base_sha": baseline.base_sha,
            }
        elif args.command == "run":
            result = run_manifest(args.manifest.resolve())
        else:
            result = cleanup_from_run_id(
                args.repo_root.resolve(), args.run_id
            )
    except PilotfishError as exc:
        print(
            json.dumps(
                {"status": exc.state, "error": str(exc), **exc.details},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
