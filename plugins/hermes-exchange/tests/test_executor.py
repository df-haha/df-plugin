from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

from hermes_exchange.executor import Executor  # noqa: E402


def _config(
    repository: Path,
    *,
    executor: str = "claude",
    enabled: bool = True,
    timeout_seconds: float = 2.0,
    output_limit_chars: int = 512,
) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(
            enabled=enabled,
            timeout_seconds=timeout_seconds,
            output_limit_chars=output_limit_chars,
            repositories={
                "demo": SimpleNamespace(path=repository, executor=executor),
            },
        )
    )


def _write_executable(directory: Path, name: str, source: str) -> Path:
    executable = directory / name
    executable.write_text(
        f"#!{sys.executable}\n" + textwrap.dedent(source),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    async def test_claude_uses_configured_repo_stdin_scrubbed_env_and_bounded_output(
        self,
    ) -> None:
        _write_executable(
            self.bin_dir,
            "claude",
            """
            import json
            import os
            import sys

            observed = {
                "argv": sys.argv[1:],
                "cwd": os.getcwd(),
                "stdin": sys.stdin.read(),
                "token": os.environ.get("TELEGRAM_BOT_TOKEN"),
            }
            print(json.dumps(observed, sort_keys=True))
            print("x" * 2_000)
            """,
        )
        path = str(self.bin_dir)
        runner = Executor(_config(self.repository, output_limit_chars=512))

        with patch.dict(
            os.environ,
            {"PATH": path, "TELEGRAM_BOT_TOKEN": "123456:secret"},
        ):
            result = await runner.execute("inspect this task", "demo")

        observed = json.loads(result.output.splitlines()[0])
        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(observed["argv"], ["-p", "--output-format", "text"])
        self.assertEqual(observed["cwd"], str(self.repository))
        self.assertEqual(observed["stdin"], "inspect this task")
        self.assertIsNone(observed["token"])
        self.assertEqual(len(result.output), 512)
        self.assertTrue(result.truncated)

    async def test_codex_uses_fixed_safe_argv_and_task_stdin(self) -> None:
        _write_executable(
            self.bin_dir,
            "codex",
            """
            import json
            import os
            import sys

            print(json.dumps({
                "argv": sys.argv[1:],
                "cwd": os.getcwd(),
                "stdin": sys.stdin.read(),
                "token": os.environ.get("TELEGRAM_BOT_TOKEN"),
            }, sort_keys=True))
            """,
        )
        path = str(self.bin_dir)
        runner = Executor(_config(self.repository, executor="codex"))

        with patch.dict(
            os.environ,
            {"PATH": path, "TELEGRAM_BOT_TOKEN": "123456:secret"},
        ):
            result = await runner.execute("review the change", "demo")

        observed = json.loads(result.output)
        self.assertTrue(result.success)
        self.assertEqual(
            observed["argv"],
            ["exec", "--color", "never", "-C", str(self.repository), "-"],
        )
        self.assertEqual(observed["cwd"], str(self.repository))
        self.assertEqual(observed["stdin"], "review the change")
        self.assertIsNone(observed["token"])
        forbidden = {
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-skip-permissions",
            "--full-auto",
            "--yolo",
            "--bare",
            "--safe-mode",
        }
        self.assertTrue(forbidden.isdisjoint(observed["argv"]))

    async def test_disabled_execution_returns_stable_result_without_launching(self) -> None:
        config = _config(self.repository, enabled=False)
        config.execution.repositories = {}

        result = await Executor(config).execute("do not run", "demo")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.error, "Local execution is disabled.")
        self.assertEqual(result.output, "")
        self.assertIsNone(result.exit_code)

    async def test_result_serializes_to_fixed_secret_free_payload(self) -> None:
        config = _config(self.repository, enabled=False)
        config.execution.repositories = {}
        result = await Executor(config).execute("private task", "private-repository")

        payload = result.to_dict()

        self.assertEqual(
            payload,
            {
                "success": False,
                "status": "disabled",
                "output": "",
                "error": "Local execution is disabled.",
                "exit_code": None,
                "truncated": False,
            },
        )
        self.assertNotIn("private", repr(payload))

    async def test_unknown_repository_alias_returns_stable_sanitized_result(self) -> None:
        result = await Executor(_config(self.repository)).execute(
            "do not run",
            "/tmp/attacker-controlled",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "unknown_repository")
        self.assertEqual(result.error, "Unknown configured repository.")
        self.assertNotIn("attacker-controlled", repr(result))
        self.assertEqual(result.output, "")

    async def test_missing_executor_binary_returns_stable_sanitized_result(self) -> None:
        runner = Executor(_config(self.repository))

        with patch.dict(
            os.environ,
            {"PATH": str(self.bin_dir), "TELEGRAM_BOT_TOKEN": "123456:secret"},
        ):
            result = await runner.execute("do not run", "demo")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "missing_binary")
        self.assertEqual(result.error, "Configured executor is unavailable.")
        self.assertNotIn("claude", result.error)
        self.assertNotIn("123456:secret", repr(result))
        self.assertEqual(result.output, "")

    async def test_executor_launch_os_error_is_sanitized(self) -> None:
        blocked = self.bin_dir / "claude"
        blocked.write_text("not executable", encoding="utf-8")
        blocked.chmod(0o644)

        with patch.dict(os.environ, {"PATH": str(self.bin_dir)}):
            result = await Executor(_config(self.repository)).execute(
                "private task", "demo"
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "launch_failed")
        self.assertEqual(result.error, "Configured executor could not be started.")
        self.assertNotIn(str(self.repository), repr(result))

    async def test_timeout_terminates_the_executor_process_group(self) -> None:
        marker = self.root / "descendant-escaped.txt"
        _write_executable(
            self.bin_dir,
            "claude",
            """
            import signal
            import subprocess
            import sys
            import time

            marker = sys.stdin.read()
            child_program = (
                "import signal,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(0.8); "
                f"Path({marker!r}).write_text('escaped', encoding='utf-8')"
            )
            subprocess.Popen([sys.executable, "-c", child_program])
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(30)
            """,
        )
        runner = Executor(
            _config(self.repository, timeout_seconds=0.2, output_limit_chars=512)
        )

        with patch.dict(os.environ, {"PATH": str(self.bin_dir)}):
            result = await runner.execute(str(marker), "demo")

        await asyncio.sleep(1.0)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error, "Executor timed out.")
        self.assertEqual(result.output, "")
        self.assertNotIn(str(marker), repr(result))
        self.assertFalse(marker.exists())

    async def test_nonzero_exit_returns_stable_sanitized_result(self) -> None:
        _write_executable(
            self.bin_dir,
            "claude",
            """
            import sys

            sys.stdin.read()
            print("sensitive executor stdout")
            print("sensitive executor stderr", file=sys.stderr)
            raise SystemExit(7)
            """,
        )
        runner = Executor(_config(self.repository))

        with patch.dict(os.environ, {"PATH": str(self.bin_dir)}):
            result = await runner.execute("private task", "demo")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Executor exited unsuccessfully.")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.output, "")
        self.assertNotIn("sensitive", repr(result))
        self.assertNotIn("private task", repr(result))


if __name__ == "__main__":
    unittest.main()
