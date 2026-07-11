from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import runner


def run_git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def git_text(repo: Path, *args: str) -> str:
    return run_git(repo, *args).stdout.decode("utf-8", errors="strict").strip()


@contextmanager
def make_repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="pilotfish-git-") as directory:
        repo = Path(directory).resolve()
        run_git(repo, "init", "-b", "main")
        run_git(repo, "config", "user.name", "Pilotfish Test")
        run_git(repo, "config", "user.email", "pilotfish@example.invalid")
        (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        run_git(repo, "add", "tracked.txt")
        run_git(repo, "commit", "-m", "initial fixture")
        yield repo


def manifest_for(
    repo: Path,
    *,
    run_id: str = "run-1",
    base_branch: str = "main",
    base_sha: str | None = None,
) -> runner.Manifest:
    import runner

    if base_sha is None:
        base_sha = git_text(repo, "rev-parse", "HEAD")
    writer = runner.JobSpec(
        id="writer-a",
        role="executor",
        goal="write fixture",
        allowed_paths=("tracked.txt",),
        denied_paths=(),
        acceptance_criteria=("The fixture job is complete.",),
        verification_commands=(),
        timeout_seconds=30,
    )
    return runner.Manifest(
        schema_version=1,
        run_id=run_id,
        task_requirement="Implement the fixture job.",
        completion_claim="The fixture job is complete.",
        overall_acceptance_criteria=("All checks pass.",),
        repo_root=repo.resolve(),
        base_branch=base_branch,
        base_sha=base_sha,
        max_parallel=1,
        integration_verification_commands=(),
        jobs=(writer,),
    )


def repo_lock(baseline: runner.RepoBaseline, run_id: str) -> runner.RepoLock:
    import runner

    return runner.RepoLock(
        baseline.common_dir,
        run_id,
        baseline.common_dir_device_inode,
    )


def command_dict(
    command_id: str = "check",
    argv: tuple[str, ...] = ("python3", "-V"),
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    return {
        "id": command_id,
        "argv": list(argv),
        "timeout_seconds": timeout_seconds,
        "effect_scope": "repo-local",
    }


def make_job(
    job_id: str = "writer-a",
    role: str = "executor",
    allowed_paths: tuple[str, ...] = ("a.txt",),
    denied_paths: tuple[str, ...] = (),
    goal: str = "write fixture",
    verification_commands: tuple[dict[str, Any], ...] = (),
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "role": role,
        "goal": goal,
        "allowed_paths": list(allowed_paths),
        "denied_paths": list(denied_paths),
        "acceptance_criteria": ["The fixture job is complete."],
        "verification_commands": [dict(command) for command in verification_commands],
        "timeout_seconds": timeout_seconds,
    }


def manifest_dict(
    jobs: tuple[dict[str, Any], ...] | None = None,
    *,
    run_id: str = "run-1",
    task_requirement: str = "Implement the fixture jobs.",
    completion_claim: str = "All fixture jobs are complete.",
    overall_acceptance_criteria: tuple[str, ...] = ("All checks pass.",),
    repo_root: str = "/tmp/repo",
    base_branch: str = "main",
    base_sha: str = "a" * 40,
    max_parallel: int = 2,
    integration_verification_commands: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    if jobs is None:
        jobs = (make_job("scout-a", "scout", ("src",)), make_job())
    return {
        "schema_version": 1,
        "run_id": run_id,
        "task_requirement": task_requirement,
        "completion_claim": completion_claim,
        "overall_acceptance_criteria": list(overall_acceptance_criteria),
        "repo_root": repo_root,
        "base_branch": base_branch,
        "base_sha": base_sha,
        "max_parallel": max_parallel,
        "integration_verification_commands": [
            dict(command) for command in integration_verification_commands
        ],
        "jobs": [dict(job) for job in jobs],
    }


def valid_worker_result(
    *,
    run_id: str = "run-1",
    job_id: str = "writer-a",
    role: str = "executor",
    base_sha: str = "a" * 40,
    worktree_head_sha: str = "b" * 40,
    status: str = "DONE",
    summary: str = "The fixture job completed.",
    changed_paths: tuple[str, ...] = ("a.txt",),
    commands: tuple[dict[str, Any], ...] = (),
    evidence: tuple[str, ...] = ("The fixture exists.",),
    blocking_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "job_id": job_id,
        "role": role,
        "base_sha": base_sha,
        "worktree_head_sha": worktree_head_sha,
        "status": status,
        "summary": summary,
        "changed_paths": list(changed_paths),
        "commands": [dict(command) for command in commands],
        "evidence": list(evidence),
        "blocking_reason": blocking_reason,
    }


def valid_verifier_result(
    *,
    run_id: str = "run-1",
    job_id: str = "verifier-a",
    base_sha: str = "a" * 40,
    worktree_head_sha: str = "b" * 40,
    status: str = "CONFIRMED",
    summary: str = "The completion claim is confirmed.",
    changed_paths: tuple[str, ...] = (),
    commands: tuple[dict[str, Any], ...] = (),
    evidence: tuple[str, ...] = ("All checks passed.",),
    blocking_reason: str | None = None,
) -> dict[str, Any]:
    return valid_worker_result(
        run_id=run_id,
        job_id=job_id,
        role="verifier",
        base_sha=base_sha,
        worktree_head_sha=worktree_head_sha,
        status=status,
        summary=summary,
        changed_paths=changed_paths,
        commands=commands,
        evidence=evidence,
        blocking_reason=blocking_reason,
    )
