from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "bin" / "insane_research.py"
sys.path.insert(0, str(REPO_ROOT / "bin"))

IS_WSL_WITHOUT_DISPLAY = (
    platform.system() == "Linux"
    and "microsoft" in platform.release().lower()
    and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
)
WINDOWS_PYTHONS = list(
    Path("/mnt/c/Users").glob("*/AppData/Local/Microsoft/WindowsApps/python.exe")
)
WINDOWS_PYTHON = WINDOWS_PYTHONS[0] if WINDOWS_PYTHONS else None
WINDOWS_TEMP = WINDOWS_PYTHON.parents[2] / "Temp" if WINDOWS_PYTHON else None
if WINDOWS_PYTHON is not None:
    try:
        HAS_WINDOWS_INTEROP = subprocess.run(
            [str(WINDOWS_PYTHON), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        HAS_WINDOWS_INTEROP = False
else:
    HAS_WINDOWS_INTEROP = False

import insane_research as research


def run_cli(
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def make_fake_windows_python(directory: Path) -> Path:
    executable = directory / "fake-windows-python"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('Python 3.13.0')\n"
        "else:\n"
        "    print(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


class InsaneResearchCliTests(unittest.TestCase):
    def test_start_uses_research_specific_browser_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--dry-run",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            state = json.loads(
                (Path(payload["run_dir"]) / "state.json").read_text(encoding="utf-8")
            )
            self.assertIn(".insane-research", state["browser_profile"])
            self.assertIn(".insane-research", state["browser_config"])
            self.assertNotIn(".insane-review", state["browser_profile"])
            self.assertNotIn(".insane-review", state["browser_config"])

    def test_live_start_fails_closed_without_windows_python(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(tmp_path / "missing-python"),
                },
            )

            self.assertEqual(result.returncode, 5)
            self.assertIn("Windows Python", result.stderr)
            self.assertFalse((tmp_path / "runs").exists())

    def test_live_start_fails_closed_with_unusable_windows_python(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")
            unusable = tmp_path / "unusable-python"
            unusable.write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
            unusable.chmod(0o700)

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(unusable),
                },
            )

            self.assertEqual(result.returncode, 5)
            self.assertIn("Windows Python", result.stderr)
            self.assertFalse((tmp_path / "runs").exists())

    def test_live_start_fails_closed_without_path_translation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")
            fake_python = make_fake_windows_python(tmp_path)

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(fake_python),
                    "INSANE_RESEARCH_WSLPATH": str(tmp_path / "missing-wslpath"),
                    "INSANE_RESEARCH_WSL_DISTRO": "",
                    "WSL_DISTRO_NAME": "",
                },
            )

            self.assertEqual(result.returncode, 5)
            self.assertIn("path translation", result.stderr.lower())


    def test_option_first_status_reexec_preserves_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            fake_python = make_fake_windows_python(tmp_path)
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run",
                        "status": "RESEARCHING",
                        "browser_driver": "cdp",
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "status",
                "--refresh",
                str(run_dir),
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(fake_python),
                    "INSANE_RESEARCH_WSL_DISTRO": "Ubuntu",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            forwarded = json.loads(result.stdout)
            self.assertEqual(forwarded[0], "status")
            self.assertTrue(forwarded[1].endswith("\\run"))
            self.assertEqual(forwarded[2:], ["--refresh", "--json"])

    def test_option_first_fetch_reexec_preserves_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            fake_python = make_fake_windows_python(tmp_path)
            run_dir = tmp_path / "run"

            result = run_cli(
                "fetch",
                "--json",
                str(run_dir),
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(fake_python),
                    "INSANE_RESEARCH_WSL_DISTRO": "Ubuntu",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            forwarded = json.loads(result.stdout)
            self.assertEqual(forwarded[0], "fetch")
            self.assertTrue(forwarded[1].endswith("\\run"))
            self.assertEqual(forwarded[2:], ["--json"])

    def test_start_help_stays_local(self) -> None:
        result = run_cli("start", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertIn("--prompt-file", result.stdout)

    def test_failed_windows_start_still_hardens_created_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")
            output_root = tmp_path / "runs"
            run_dir = output_root / "failed-run"
            fake_python = tmp_path / "failing-windows-python"
            fake_python.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "if sys.argv[1:] == ['--version']:\n"
                "    print('Python 3.13.0')\n"
                "    raise SystemExit(0)\n"
                f"run = pathlib.Path({str(run_dir)!r})\n"
                "run.mkdir(parents=True)\n"
                "run.chmod(0o755)\n"
                "for name in ('request.md', 'state.json'):\n"
                "    path = run / name\n"
                "    path.write_text('sensitive', encoding='utf-8')\n"
                "    path.chmod(0o644)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(output_root),
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(fake_python),
                    "INSANE_RESEARCH_WSL_DISTRO": "Ubuntu",
                },
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((run_dir / "request.md").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((run_dir / "state.json").stat().st_mode), 0o600
            )

    @unittest.skipUnless(
        IS_WSL_WITHOUT_DISPLAY and HAS_WINDOWS_INTEROP,
        "requires WSL without a display and working Windows interop",
    )
    def test_start_reexecutes_on_windows_with_dedicated_cdp_port(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")
            output_root = tmp_path / "runs"

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(output_root),
                "--dry-run",
                "--json",
                extra_env={"INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            state = json.loads(
                (Path(payload["run_dir"]) / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["runtime_os"], "Windows")
            self.assertEqual(state["cdp_port"], 9333)
            self.assertEqual(Path(payload["run_dir"]).parent, output_root.resolve())
            run_dir = Path(payload["run_dir"])
            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((run_dir / "request.md").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((run_dir / "state.json").stat().st_mode), 0o600
            )

    @unittest.skipUnless(
        IS_WSL_WITHOUT_DISPLAY and HAS_WINDOWS_INTEROP and WINDOWS_TEMP is not None,
        "requires WSL without a display, Windows interop, and a Windows temp path",
    )
    def test_windows_drive_run_dir_round_trips_for_local_status(self) -> None:
        assert WINDOWS_TEMP is not None
        with tempfile.TemporaryDirectory(dir=WINDOWS_TEMP) as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research HTTP history.", encoding="utf-8")
            output_root = tmp_path / "runs"

            start_result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(output_root),
                "--dry-run",
                "--json",
                extra_env={"INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1"},
            )

            self.assertEqual(start_result.returncode, 0, start_result.stderr)
            payload = json.loads(start_result.stdout)
            self.assertTrue(payload["run_dir"].startswith("/mnt/c/"))
            status_result = run_cli("status", payload["run_dir"], "--json")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            self.assertEqual(json.loads(status_result.stdout)["status"], "CREATED")

    def test_start_dry_run_creates_isolated_persisted_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research reusable rocket economics.", encoding="utf-8")
            output_root = tmp_path / "runs"

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(output_root),
                "--dry-run",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            run_dir = Path(payload["run_dir"])
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            request = (run_dir / "request.md").read_text(encoding="utf-8")

            self.assertEqual(run_dir.parent, output_root.resolve())
            self.assertEqual(state["run_id"], run_dir.name)
            self.assertEqual(state["status"], "CREATED")
            self.assertTrue(state["prompt_sha256"])
            self.assertIsNone(state["conversation_url"])
            self.assertEqual(request, "Research reusable rocket economics.")

    def test_agent_driver_start_prepares_run_without_browser_submission(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research browser agent bridges.", encoding="utf-8")

            result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--browser-driver",
                "agent",
                "--json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            state = json.loads(
                (Path(payload["run_dir"]) / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "CREATED")
            self.assertEqual(state["browser_driver"], "agent")
            self.assertEqual(state["target_model"], "GPT-5.6 Sol")
            self.assertEqual(state["target_effort"], "Extra High")

    def test_agent_submission_observation_fails_closed_on_wrong_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt = "Research browser agent bridges."
            prompt_file.write_text(prompt, encoding="utf-8")
            start_result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--browser-driver",
                "agent",
                "--json",
            )
            self.assertEqual(start_result.returncode, 0, start_result.stderr)
            run_dir = Path(json.loads(start_result.stdout)["run_dir"])
            observation_file = tmp_path / "submission.json"
            observation_file.write_text(
                json.dumps(
                    {
                        "kind": "submission",
                        "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
                        "base_assistant": 0,
                        "base_copy": 0,
                        "base_message_ids": [],
                        "verified_model": "GPT-5.5",
                        "verified_effort": "Extra High",
                        "deep_research_active": True,
                        "prompt_verified": True,
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "record",
                str(run_dir),
                "--observation-file",
                str(observation_file),
                "--json",
            )

            self.assertNotEqual(result.returncode, 0)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "CREATED")
            self.assertIsNone(state["conversation_url"])

    def test_agent_submission_rejects_non_chatgpt_conversation_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            run_dir, state = research.create_run(
                "Research browser trust boundaries.",
                tmp_path / "runs",
                browser_driver="agent",
            )
            observation = {
                "conversation_url": "https://evil.example/c/12345678-1234-1234-1234-123456789abc",
                "base_assistant": 0,
                "base_copy": 0,
                "base_message_ids": [],
                "verified_model": "GPT-5.6 Sol",
                "verified_effort": "Extra High",
                "deep_research_active": True,
                "prompt_verified": True,
            }

            with self.assertRaisesRegex(ValueError, "conversation URL"):
                research.apply_submission_observation(run_dir, state, observation)

            persisted = research.load_state(run_dir)
            self.assertEqual(persisted["status"], "CREATED")
            self.assertIsNone(persisted["conversation_url"])

    def test_agent_submission_rejects_malformed_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            run_dir, state = research.create_run(
                "Research browser trust boundaries.",
                tmp_path / "runs",
                browser_driver="agent",
            )
            observation = {
                "conversation_url": "https://chatgpt.com/c/12345678----",
                "base_assistant": 0,
                "base_copy": 0,
                "base_message_ids": [],
                "verified_model": "GPT-5.6 Sol",
                "verified_effort": "Extra High",
                "deep_research_active": True,
                "prompt_verified": True,
            }

            with self.assertRaisesRegex(ValueError, "conversation URL"):
                research.apply_submission_observation(run_dir, state, observation)

            self.assertEqual(research.load_state(run_dir)["status"], "CREATED")

    def test_agent_observations_bind_and_complete_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            prompt_file = tmp_path / "prompt.md"
            prompt_file.write_text("Research browser agent bridges.", encoding="utf-8")
            start_result = run_cli(
                "start",
                "--prompt-file",
                str(prompt_file),
                "--out-dir",
                str(tmp_path / "runs"),
                "--browser-driver",
                "agent",
                "--json",
            )
            self.assertEqual(start_result.returncode, 0, start_result.stderr)
            run_dir = Path(json.loads(start_result.stdout)["run_dir"])
            conversation_url = "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
            submission_file = tmp_path / "submission.json"
            submission_file.write_text(
                json.dumps(
                    {
                        "kind": "submission",
                        "conversation_url": conversation_url,
                        "base_assistant": 0,
                        "base_copy": 0,
                        "base_message_ids": [],
                        "verified_model": "GPT-5.6 Sol",
                        "verified_effort": "Extra High",
                        "deep_research_active": True,
                        "prompt_verified": True,
                    }
                ),
                encoding="utf-8",
            )
            bind_result = run_cli(
                "record",
                str(run_dir),
                "--observation-file",
                str(submission_file),
                "--json",
            )
            self.assertEqual(bind_result.returncode, 0, bind_result.stderr)
            self.assertEqual(json.loads(bind_result.stdout)["status"], "PROMPT_SUBMITTED")

            report = "Deep research completed.\n\n" + ("Source-backed evidence. " * 20)
            completion_file = tmp_path / "completion.json"
            completion_file.write_text(
                json.dumps(
                    {
                        "kind": "refresh",
                        "conversation_url": conversation_url,
                        "message_id": "new-message",
                        "assistant_text": report,
                        "response_text": report,
                        "links": ["https://example.com/a", "https://example.com/b"],
                        "turn_complete": True,
                        "streaming": False,
                        "quota": None,
                    }
                ),
                encoding="utf-8",
            )
            ambiguous_result = run_cli(
                "record",
                str(run_dir),
                "--observation-file",
                str(completion_file),
                "--json",
            )
            self.assertEqual(ambiguous_result.returncode, 0, ambiguous_result.stderr)
            self.assertEqual(
                json.loads(ambiguous_result.stdout)["status"], "RESEARCHING"
            )
            self.assertFalse((run_dir / "response.md").exists())

            completion = json.loads(completion_file.read_text(encoding="utf-8"))
            completion["terminal_signal"] = "deep_research_report_frame"
            completion_file.write_text(json.dumps(completion), encoding="utf-8")
            complete_result = run_cli(
                "record",
                str(run_dir),
                "--observation-file",
                str(completion_file),
                "--json",
            )

            self.assertEqual(complete_result.returncode, 0, complete_result.stderr)
            self.assertEqual(json.loads(complete_result.stdout)["status"], "COMPLETED")
            self.assertEqual(
                (run_dir / "response.md").read_text(encoding="utf-8"),
                report.rstrip() + "\n",
            )

    def test_status_reports_persisted_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp) / "20260817_120000_deadbeef"
            run_dir.mkdir()
            state = {
                "schema_version": 1,
                "run_id": run_dir.name,
                "status": "RESEARCHING",
                "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            }
            (run_dir / "state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            result = run_cli("status", str(run_dir), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["run_id"], run_dir.name)
            self.assertEqual(payload["status"], "RESEARCHING")
            self.assertEqual(payload["conversation_url"], state["conversation_url"])

    def test_status_refresh_rejects_agent_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp) / "20260817_120000_deadbeef"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": run_dir.name,
                        "status": "RESEARCHING",
                        "browser_driver": "agent",
                        "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "status",
                str(run_dir),
                "--refresh",
                "--json",
                extra_env={
                    "INSANE_RESEARCH_FORCE_WINDOWS_REEXEC": "1",
                    "INSANE_RESEARCH_WINDOWS_PYTHON": str(run_dir / "missing-python"),
                },
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("browser_driver=cdp", result.stderr)

    def test_fetch_refuses_incomplete_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp) / "20260817_120000_deadbeef"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": run_dir.name,
                        "status": "RESEARCHING",
                        "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli("fetch", str(run_dir))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not complete", result.stderr.lower())
            self.assertFalse((run_dir / "report.md").exists())

    def test_fetch_persists_completed_report_and_marks_harvested(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp) / "20260817_120000_deadbeef"
            run_dir.mkdir()
            response = "# Research report\n\nA source-backed result.\n"
            (run_dir / "response.md").write_text(response, encoding="utf-8")
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": run_dir.name,
                        "status": "COMPLETED",
                        "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
                        "report_file": None,
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli("fetch", str(run_dir), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual((run_dir / "report.md").read_text(encoding="utf-8"), response)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "HARVESTED")
            self.assertEqual(state["report_file"], "report.md")
            self.assertEqual(payload["status"], "HARVESTED")


class FakeAssistantNode:
    def __init__(self, text: str, message_id: str, links: list[str]) -> None:
        self._text = text
        self._message_id = message_id
        self._links = links

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        return self._message_id if name == "data-message-id" else None

    def eval_on_selector_all(self, selector: str, script: str) -> list[str]:
        return self._links


class FakePage:
    def close(self) -> None:
        pass


class FakeLazyPage(FakePage):
    def __init__(self) -> None:
        self.revealed = False

    def evaluate(self, script: str) -> None:
        self.revealed = True


class FakePlaywright:
    def stop(self) -> None:
        pass


class FakeDeepResearchNode:
    def __init__(self, page, text: str, *, activates: bool = False) -> None:
        self.page = page
        self.text = text
        self.activates = activates

    def inner_text(self) -> str:
        return self.text

    def click(self) -> None:
        if self.activates:
            self.page.active = True


class FakeDeepResearchPage:
    def __init__(self, *, active: bool = False, menu_choice: bool = False) -> None:
        self.active = active
        self.menu_choice = menu_choice
        self.active_node = FakeDeepResearchNode(self, "深入研究")
        self.choice_node = FakeDeepResearchNode(
            self,
            "深入研究 獲取詳細報告",
            activates=True,
        )

    def query_selector_all(self, selector: str):
        if selector == '[data-inline-selection-pill][data-id="plugin:connector_openai_deep_research"]':
            return [self.active_node] if self.active else []
        if selector == "div.__menu-item":
            return [self.choice_node] if self.menu_choice else []
        return []

    def query_selector(self, selector: str):
        return None


class FakeModelMenuNode:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self) -> str:
        return self.text

    def is_visible(self) -> bool:
        return True


class FakeModelMenuPage:
    def __init__(self) -> None:
        self.nodes = [
            FakeModelMenuNode("模型\nGPT-5.6 Sol"),
            FakeModelMenuNode("推理強度\n超高"),
        ]

    def query_selector_all(self, selector: str):
        return self.nodes if selector == '[role="menuitem"]' else []


class InsaneResearchBrowserAdapterTests(unittest.TestCase):
    def test_current_labeled_model_menu_is_verified(self) -> None:
        page = FakeModelMenuPage()
        with (
            mock.patch.object(research.web, "_open_switcher", return_value=True),
            mock.patch.object(research, "_press_escape"),
        ):
            verified = research.select_required_model_effort(
                page,
                "GPT-5.6 Sol",
                "Extra High",
            )

        self.assertEqual(verified, "GPT-5.6 Sol (Extra High)")

    def test_refresh_accepts_unknown_composer_state_only_with_valid_session_cookie(self) -> None:
        page = object()
        context = object()
        with (
            mock.patch.object(research.web, "login_state", return_value="unknown"),
            mock.patch.object(
                research.web,
                "_cookie_state",
                return_value=("ok", "2026-11-17"),
            ),
        ):
            self.assertFalse(
                research._logged_in_for_operation(
                    context, page, require_composer=True
                )
            )
            self.assertTrue(
                research._logged_in_for_operation(
                    context, page, require_composer=False
                )
            )

    def test_current_deep_research_pill_is_recognized(self) -> None:
        page = FakeDeepResearchPage(active=True)

        self.assertTrue(research._deep_research_is_active(page))

    def test_current_deep_research_menu_item_is_selected_and_verified(self) -> None:
        page = FakeDeepResearchPage(menu_choice=True)

        with mock.patch.object(research.time, "sleep", return_value=None):
            selected = research.select_deep_research(page)

        self.assertTrue(selected)
        self.assertTrue(page.active)

    def test_submission_stops_before_prompt_when_required_model_is_unverified(self) -> None:
        state: dict[str, object] = {
            "target_model": "GPT-5.6 Sol",
            "target_effort": "Extra High",
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            with (
                mock.patch.object(
                    research,
                    "_ensure_logged_in_page",
                    return_value=(FakePlaywright(), FakePage()),
                ),
                mock.patch.object(
                    research,
                    "select_required_model_effort",
                    return_value=None,
                ),
                mock.patch.object(research.web, "put_text") as put_text,
            ):
                with self.assertRaisesRegex(RuntimeError, "model or reasoning effort"):
                    research.submit_research(
                        Path(raw_tmp), state, "Sensitive research prompt", None
                    )

        put_text.assert_not_called()


class InsaneResearchRefreshTests(unittest.TestCase):
    def refresh_with(
        self,
        text: str,
        message_id: str,
        *,
        turn_complete: bool,
        initial_status: str = "RESEARCHING",
        quota: str | None = None,
    ) -> dict[str, object]:
        links = ["https://example.com/a", "https://example.com/b"]
        node = FakeAssistantNode(text, message_id, links)
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": initial_status,
            "browser_driver": "cdp",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_assistant": 1,
            "base_copy": 1,
            "base_message_ids": ["old-message"],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)
            if initial_status == "COMPLETED":
                state["response_file"] = "response.md"
                (run_dir / "response.md").write_text("Persisted report.\n", encoding="utf-8")
            with (
                mock.patch.object(
                    research,
                    "_ensure_logged_in_page",
                    return_value=(FakePlaywright(), FakePage()),
                ),
                mock.patch.object(research.web, "detect_quota_block", return_value=quota),
                mock.patch.object(research.web, "last_assistant_node", return_value=node),
                mock.patch.object(research.web, "is_streaming", return_value=False),
                mock.patch.object(
                    research.web,
                    "last_turn_complete",
                    return_value=turn_complete,
                ),
                mock.patch.object(research.web, "copy_last_turn", return_value=text),
            ):
                return research.refresh_research(run_dir, state, None)

    def test_completed_run_cannot_regress_on_refresh(self) -> None:
        state = self.refresh_with(
            "",
            "old-message",
            turn_complete=False,
            initial_status="COMPLETED",
        )

        self.assertEqual(state["status"], "COMPLETED")

    def test_completed_report_wins_over_quota_banner(self) -> None:
        text = "Deep research completed.\n\n" + ("Final evidence. " * 30)

        state = self.refresh_with(
            text,
            "new-message",
            turn_complete=True,
            quota="Deep Research limit reached",
        )

        self.assertEqual(state["status"], "COMPLETED")

    def test_long_source_bearing_progress_message_is_not_completed(self) -> None:
        text = "Research progress update. " + ("Still investigating evidence. " * 40)

        state = self.refresh_with(text, "new-message", turn_complete=True)

        self.assertEqual(state["status"], "RESEARCHING")

    def test_completion_phrase_inside_clarification_is_not_completed(self) -> None:
        text = (
            "Before research is complete, I need clarification.\n\n"
            + ("Please clarify the scope and intended audience. " * 12)
        )

        state = self.refresh_with(text, "new-message", turn_complete=True)

        self.assertEqual(state["status"], "WAITING_CLARIFICATION")

    def test_old_assistant_turn_is_not_completed(self) -> None:
        text = "Deep research completed. " + ("Final evidence. " * 30)

        state = self.refresh_with(text, "old-message", turn_complete=True)

        self.assertEqual(state["status"], "RESEARCHING")

    def test_verified_new_completed_turn_is_harvested(self) -> None:
        text = "Deep research completed.\n\n" + ("Final evidence. " * 30)

        state = self.refresh_with(text, "new-message", turn_complete=True)

        self.assertEqual(state["status"], "COMPLETED")

    def test_completion_rejects_mismatched_response_text(self) -> None:
        report = "Deep research completed.\n\n" + ("Final evidence. " * 30)
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": "RESEARCHING",
            "browser_driver": "agent",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_message_ids": [],
        }
        observation = {
            "conversation_url": state["conversation_url"],
            "message_id": "new-message",
            "assistant_text": report,
            "response_text": "x",
            "links": ["https://example.com/a", "https://example.com/b"],
            "turn_complete": True,
            "terminal_signal": "deep_research_report_frame",
            "streaming": False,
            "quota": None,
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)

            with self.assertRaisesRegex(ValueError, "harvested report"):
                research.apply_refresh_observation(
                    run_dir,
                    state,
                    observation,
                    required_driver="agent",
                )

            self.assertFalse((run_dir / "response.md").exists())

    def test_completion_rejects_near_match_with_changed_conclusion(self) -> None:
        observed = ("Evidence supports option A. " * 40) + "\nDeep research completed."
        harvested = (
            ("Evidence supports option A. " * 40)
            + "\nFINAL CONCLUSION: choose option B immediately."
            + "\nDeep research completed."
        )
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": "RESEARCHING",
            "browser_driver": "agent",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_message_ids": [],
        }
        observation = {
            "conversation_url": state["conversation_url"],
            "message_id": "new-message",
            "assistant_text": observed,
            "response_text": harvested,
            "links": ["https://example.com/a", "https://example.com/b"],
            "turn_complete": True,
            "terminal_signal": "deep_research_report_frame",
            "streaming": False,
            "quota": None,
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)

            with self.assertRaisesRegex(ValueError, "harvested report"):
                research.apply_refresh_observation(
                    run_dir,
                    state,
                    observation,
                    required_driver="agent",
                )

            self.assertFalse((run_dir / "response.md").exists())

    def test_agent_record_rejects_cdp_run(self) -> None:
        report = "Deep research completed.\n\n" + ("Final evidence. " * 30)
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": "RESEARCHING",
            "browser_driver": "cdp",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_message_ids": [],
        }
        observation = {
            "conversation_url": state["conversation_url"],
            "message_id": "new-message",
            "assistant_text": report,
            "response_text": report,
            "links": ["https://example.com/a", "https://example.com/b"],
            "turn_complete": True,
            "terminal_signal": "deep_research_report_frame",
            "streaming": False,
            "quota": None,
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            with self.assertRaisesRegex(ValueError, "browser_driver=agent"):
                research.apply_refresh_observation(
                    Path(raw_tmp),
                    state,
                    observation,
                    required_driver="agent",
                )

    def test_nested_deep_research_report_is_harvested(self) -> None:
        report = "Deep research completed.\n\n" + ("Verified report evidence. " * 20)
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": "RESEARCHING",
            "browser_driver": "cdp",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_assistant": 0,
            "base_copy": 0,
            "base_message_ids": [],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)
            with (
                mock.patch.object(
                    research,
                    "_ensure_logged_in_page",
                    return_value=(FakePlaywright(), FakePage()),
                ),
                mock.patch.object(
                    research,
                    "_deep_research_report",
                    return_value={
                        "message_id": "deep-research-report:abc123",
                        "text": report,
                        "links": ["https://example.com/a", "https://example.com/b"],
                    },
                ),
                mock.patch.object(research.web, "detect_quota_block", return_value=None),
                mock.patch.object(research.web, "last_assistant_node", return_value=None),
                mock.patch.object(research.web, "is_streaming", return_value=False),
            ):
                refreshed = research.refresh_research(run_dir, state, None)

            self.assertEqual(refreshed["status"], "COMPLETED")
            self.assertEqual(
                (run_dir / "response.md").read_text(encoding="utf-8"),
                report.rstrip() + "\n",
            )

    def test_refresh_reveals_lazy_latest_turn_before_report_lookup(self) -> None:
        report = "Deep research completed.\n\n" + ("Verified report evidence. " * 20)
        page = FakeLazyPage()
        state: dict[str, object] = {
            "schema_version": 1,
            "run_id": "20260817_120000_deadbeef",
            "status": "RESEARCHING",
            "browser_driver": "cdp",
            "conversation_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            "base_assistant": 0,
            "base_copy": 0,
            "base_message_ids": [],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)

            def lazy_report(current_page, *, wait_seconds):
                if not current_page.revealed:
                    return None
                return {
                    "message_id": "deep-research-report:abc123",
                    "text": report,
                    "links": ["https://example.com/a", "https://example.com/b"],
                }

            with (
                mock.patch.object(
                    research,
                    "_ensure_logged_in_page",
                    return_value=(FakePlaywright(), page),
                ),
                mock.patch.object(
                    research,
                    "_deep_research_report",
                    side_effect=lazy_report,
                ),
                mock.patch.object(research.web, "detect_quota_block", return_value=None),
                mock.patch.object(research.web, "last_assistant_node", return_value=None),
                mock.patch.object(research.web, "is_streaming", return_value=False),
            ):
                refreshed = research.refresh_research(run_dir, state, None)

            self.assertTrue(page.revealed)
            self.assertEqual(refreshed["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
