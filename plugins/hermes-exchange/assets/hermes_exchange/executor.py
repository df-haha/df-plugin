"""Controlled local Claude Code and Codex execution for Hermes Relay Lite."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


class RepositorySpec(Protocol):
    path: Path
    executor: str


class ExecutionSpec(Protocol):
    enabled: bool
    timeout_seconds: float
    output_limit_chars: int
    repositories: Mapping[str, RepositorySpec]


class RelayConfigSpec(Protocol):
    execution: ExecutionSpec


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Stable result returned by the optional local executor."""

    success: bool
    status: str
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the fixed JSON-safe payload consumed by RelayRuntime."""

        return {
            "success": self.success,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "truncated": self.truncated,
        }


class Executor:
    """Run a task only in a repository selected by a configured alias."""

    def __init__(self, config: RelayConfigSpec) -> None:
        self._execution = config.execution

    async def execute(self, task: str, repository: str) -> ExecutionResult:
        if not self._execution.enabled:
            return ExecutionResult(
                success=False,
                status="disabled",
                error="Local execution is disabled.",
            )
        repo = self._execution.repositories.get(repository)
        if repo is None:
            return ExecutionResult(
                success=False,
                status="unknown_repository",
                error="Unknown configured repository.",
            )
        repo_path = os.fspath(repo.path)
        if repo.executor == "codex":
            argv = ("codex", "exec", "--color", "never", "-C", repo_path, "-")
        else:
            argv = ("claude", "-p", "--output-format", "text")
        child_env = os.environ.copy()
        child_env.pop("TELEGRAM_BOT_TOKEN", None)

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=repo_path,
                env=child_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                status="missing_binary",
                error="Configured executor is unavailable.",
            )
        except OSError:
            return ExecutionResult(
                success=False,
                status="launch_failed",
                error="Configured executor could not be started.",
            )
        communication = asyncio.create_task(
            _communicate_bounded(
                process,
                task,
                output_limit_chars=self._execution.output_limit_chars,
            )
        )
        try:
            output, truncated = await asyncio.wait_for(
                asyncio.shield(communication),
                timeout=self._execution.timeout_seconds,
            )
        except TimeoutError:
            await _terminate_process_group(process)
            await communication
            return ExecutionResult(
                success=False,
                status="timeout",
                error="Executor timed out.",
            )
        except asyncio.CancelledError:
            await _terminate_process_group(process)
            await communication
            raise
        if process.returncode != 0:
            return ExecutionResult(
                success=False,
                status="failed",
                error="Executor exited unsuccessfully.",
                exit_code=process.returncode,
            )
        return ExecutionResult(
            success=True,
            status="completed",
            output=output,
            exit_code=process.returncode,
            truncated=truncated,
        )


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
    task: str,
    *,
    output_limit_chars: int,
) -> tuple[str, bool]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("executor pipes were not created")

    stdout_task = asyncio.create_task(
        _read_bounded_text(process.stdout, output_limit_chars)
    )
    await asyncio.gather(
        _write_stdin(process.stdin, task),
        stdout_task,
        _drain_stream(process.stderr),
        process.wait(),
    )
    return stdout_task.result()


async def _write_stdin(stream: asyncio.StreamWriter, task: str) -> None:
    try:
        stream.write(task.encode("utf-8"))
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()
        try:
            await stream.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def _read_bounded_text(
    stream: asyncio.StreamReader,
    limit: int,
) -> tuple[str, bool]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    captured = 0
    truncated = False

    while chunk := await stream.read(8_192):
        text = decoder.decode(chunk)
        remaining = limit - captured
        if remaining > 0:
            parts.append(text[:remaining])
            captured += min(len(text), remaining)
        if len(text) > max(remaining, 0):
            truncated = True

    final_text = decoder.decode(b"", final=True)
    remaining = limit - captured
    if remaining > 0:
        parts.append(final_text[:remaining])
    if len(final_text) > max(remaining, 0):
        truncated = True
    return "".join(parts), truncated


async def _drain_stream(stream: asyncio.StreamReader) -> None:
    while await stream.read(8_192):
        pass


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=0.2)
    except TimeoutError:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    if process.returncode is None:
        await process.wait()
