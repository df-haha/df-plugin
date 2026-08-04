from __future__ import annotations

import copy
import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import runner
from tests.helpers import valid_worker_result


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ProcessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pilotfish-process-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.roles = runner.load_roles()
        self.policy = runner.load_policy()

    def command_spec(
        self,
        command_id: str = "check",
        argv: tuple[str, ...] = ("python3", "-V"),
    ) -> runner.CommandSpec:
        return runner.CommandSpec(command_id, argv, 30, "repo-local")

    def job(
        self,
        job_id: str = "writer-a",
        *,
        role: str = "executor",
        timeout_seconds: int = 2,
        commands: tuple[runner.CommandSpec, ...] = (),
        goal: str = "complete the fixture job",
    ) -> runner.JobSpec:
        return runner.JobSpec(
            id=job_id,
            role=role,
            goal=goal,
            allowed_paths=("src",),
            denied_paths=(),
            acceptance_criteria=("The fixture is complete.",),
            verification_commands=commands,
            timeout_seconds=timeout_seconds,
        )

    def manifest(
        self,
        jobs: tuple[runner.JobSpec, ...],
        *,
        max_parallel: int = 1,
        task_requirement: str = "Complete the fixture jobs.",
    ) -> runner.Manifest:
        return runner.Manifest(
            schema_version=1,
            run_id="run-1",
            task_requirement=task_requirement,
            completion_claim="The fixture jobs are complete.",
            overall_acceptance_criteria=("All fixture checks pass.",),
            repo_root=self.root,
            base_branch="main",
            base_sha=BASE_SHA,
            max_parallel=max_parallel,
            integration_verification_commands=(),
            jobs=jobs,
        )

    def worker(
        self,
        job: runner.JobSpec | None = None,
        *,
        suffix: str | None = None,
    ) -> runner.WorkerRun:
        job = job or self.job()
        suffix = suffix or job.id
        worktree = self.root / "worktrees" / suffix
        worktree.mkdir(parents=True, exist_ok=True)
        artifacts = self.root / "artifacts" / suffix
        return runner.WorkerRun(
            job=job,
            role=self.roles[job.role],
            worktree=worktree,
            branch=f"pf/run-1/{job.id}",
            process=None,
            started_monotonic=0.0,
            finished_monotonic=None,
            events_path=artifacts / "events.jsonl",
            stderr_path=artifacts / "stderr.log",
            final_path=artifacts / "final.json",
            status="CREATED",
            snapshot_sha=None,
            snapshot_tree=None,
        )

    def spawn(
        self,
        worker: runner.WorkerRun,
        command: tuple[str, ...],
        prompt: bytes = b"fixture prompt",
    ) -> None:
        runner.spawn_worker(
            worker,
            command,
            prompt,
            max_prompt_bytes=self.policy.max_prompt_bytes,
        )

    def write_valid_artifacts(
        self,
        worker: runner.WorkerRun,
        manifest: runner.Manifest,
        *,
        result: dict[str, object] | None = None,
    ) -> dict[str, object]:
        worker.events_path.parent.mkdir(parents=True, exist_ok=True)
        worker.events_path.write_text(
            json.dumps({"type": "thread.started", "thread_id": "thread-1"})
            + "\n"
            + json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        if result is None:
            result = valid_worker_result(
                run_id=manifest.run_id,
                job_id=worker.job.id,
                role=worker.role.name,
                base_sha=manifest.base_sha,
                worktree_head_sha=HEAD_SHA,
                changed_paths=("src/output.txt",)
                if worker.role.name == "executor"
                else (),
                commands=tuple(
                    {
                        "id": command.id,
                        "argv": list(command.argv),
                        "exit_code": 0,
                    }
                    for command in worker.job.verification_commands
                ),
            )
        worker.final_path.write_text(json.dumps(result), encoding="utf-8")
        worker.process = object()  # validate_worker_result only requires presence.
        worker.status = "DONE"
        worker.exit_code = 0
        worker.invocation_argv = tuple(
            runner.build_worker_command(
                ("codex",),
                worker.role,
                worker.worktree,
                worker.final_path,
                self.policy,
            )
        )
        return result


class WorkerCommandTests(ProcessTestCase):
    def test_executor_command_is_pinned_and_isolated(self) -> None:
        worker = self.worker()

        command = runner.build_worker_command(
            codex_prefix=("codex",),
            role=worker.role,
            worktree=worker.worktree,
            final_path=worker.final_path,
            policy=self.policy,
        )

        joined = "\n".join(command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--model", command)
        self.assertIn("workspace-write", command)
        self.assertIn('model="gpt-5.6-sol"', command)
        self.assertIn('model_reasoning_effort="medium"', command)
        self.assertIn(
            "features.multi_agent_v2.max_concurrent_threads_per_session=1",
            command,
        )
        self.assertIn("features.plugins=false", command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn('trust_level="untrusted"', joined)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", command)
        self.assertIn(
            "sandbox_workspace_write.exclude_tmpdir_env_var=true", command
        )
        self.assertIn("sandbox_workspace_write.writable_roots=[]", command)
        self.assertEqual(command[-1], "-")

    def test_codex_prefix_must_be_a_nonempty_argv_tuple(self) -> None:
        worker = self.worker()
        for prefix in ((), ["codex"], ("",), ("codex\x00bad",)):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(runner.PilotfishError, "prefix"):
                    runner.build_worker_command(
                        prefix,
                        worker.role,
                        worker.worktree,
                        worker.final_path,
                        self.policy,
                    )

    def test_worker_prompt_keeps_goal_as_data(self) -> None:
        forbidden = self.root / "forbidden"
        goal = f'$(touch {forbidden}) ; `id` ; "quoted"'
        job = self.job(goal=goal)
        manifest = self.manifest((job,), task_requirement=goal)

        payload = runner.build_worker_prompt(manifest, job)

        text = payload.decode("utf-8")
        encoded_job = text.split("<pilotfish_job>\n", 1)[1].split(
            "\n</pilotfish_job>", 1
        )[0]
        decoded_job = json.loads(encoded_job)
        self.assertEqual(decoded_job["job"]["goal"], goal)
        self.assertEqual(decoded_job["task_requirement"], goal)
        self.assertFalse(forbidden.exists())

    def test_worker_environment_drops_canary_and_api_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PILOTFISH_CANARY_TOKEN": "never-forward",
                "OPENAI_API_KEY": "never-forward",
                "HOME": str(self.root),
                "PATH": os.environ.get("PATH", ""),
            },
            clear=True,
        ):
            environment = runner.worker_environment()

        self.assertNotIn("PILOTFISH_CANARY_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(environment["HOME"], str(self.root))
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def test_worker_environment_rejects_proxy_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://user:password@example.invalid:8080"},
            clear=True,
        ):
            with self.assertRaisesRegex(runner.PilotfishError, "credential-bearing"):
                runner.worker_environment()


class WorkerProcessTests(ProcessTestCase):
    def test_spawn_rejects_background_thread_before_popen(self) -> None:
        worker = self.worker()
        ready = threading.Event()
        release = threading.Event()

        def background_waiter() -> None:
            ready.set()
            release.wait(timeout=5)

        background = threading.Thread(target=background_waiter)
        background.start()
        self.assertTrue(ready.wait(timeout=1))
        try:
            with patch.object(runner.subprocess, "Popen") as popen:
                popen.return_value = object()
                with self.assertRaises(runner.PilotfishError) as raised:
                    self.spawn(
                        worker,
                        (sys.executable, "-c", "raise SystemExit(0)"),
                    )

                self.assertEqual(raised.exception.state, "PRECHECK_FAILED")
                self.assertRegex(str(raised.exception), "single-thread|main thread")
                popen.assert_not_called()
        finally:
            release.set()
            background.join(timeout=1)
        self.assertFalse(background.is_alive())

    def test_publication_interrupt_blocks_signals_and_reaps_local_process(
        self,
    ) -> None:
        worker = self.worker(self.job(timeout_seconds=30))
        spawned: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        class InterruptingWorker:
            def __init__(self, wrapped: runner.WorkerRun) -> None:
                object.__setattr__(self, "wrapped", wrapped)
                object.__setattr__(self, "interrupted", False)
                object.__setattr__(self, "publication_mask", set())

            def __getattr__(self, name: str) -> object:
                return getattr(self.wrapped, name)

            def __setattr__(self, name: str, value: object) -> None:
                if (
                    name == "process"
                    and value is not None
                    and not self.interrupted
                ):
                    object.__setattr__(self, "interrupted", True)
                    object.__setattr__(
                        self,
                        "publication_mask",
                        signal.pthread_sigmask(signal.SIG_BLOCK, set()),
                    )
                    raise KeyboardInterrupt("synthetic publication interrupt")
                setattr(self.wrapped, name, value)

        interrupting = InterruptingWorker(worker)

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        with patch.object(runner.subprocess, "Popen", side_effect=capture_popen):
            with self.assertRaisesRegex(
                KeyboardInterrupt, "synthetic publication interrupt"
            ):
                self.spawn(
                    interrupting,
                    (sys.executable, "-c", "import time; time.sleep(60)"),
                )

        self.assertEqual(len(spawned), 1)
        survived = process_group_exists(spawned[0].pid)
        if survived:
            runner.terminate_process_group(spawned[0], 0.1)
        self.assertFalse(survived, "publication failure leaked its local process")
        self.assertIn(signal.SIGINT, interrupting.publication_mask)
        self.assertIn(signal.SIGTERM, interrupting.publication_mask)

    def test_large_prompt_to_nonreading_child_never_blocks_spawn(self) -> None:
        worker = self.worker(self.job(timeout_seconds=1))
        command = (sys.executable, "-c", "import time; time.sleep(60)")
        prompt = b"x" * self.policy.max_prompt_bytes

        started = time.monotonic()
        self.spawn(worker, command, prompt)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        runner.monitor_workers(
            [worker], poll_interval=0.02, terminate_grace_seconds=0.1
        )
        self.assertEqual(worker.status, "WORKER_FAILED")
        self.assertFalse(process_group_exists(worker.process.pid))

    def test_spawn_rejects_prompt_over_limit_before_starting_process(self) -> None:
        worker = self.worker()

        with self.assertRaisesRegex(runner.PilotfishError, "prompt"):
            self.spawn(
                worker,
                (sys.executable, "-c", "raise SystemExit(0)"),
                b"x" * (self.policy.max_prompt_bytes + 1),
            )

        self.assertIsNone(worker.process)

    def test_timeout_kills_entire_process_group(self) -> None:
        worker = self.worker(self.job(timeout_seconds=1))
        child_code = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "time.sleep(60)"
        )
        self.spawn(worker, (sys.executable, "-c", child_code))

        runner.monitor_workers(
            [worker], poll_interval=0.02, terminate_grace_seconds=0.1
        )

        self.assertEqual(worker.status, "WORKER_FAILED")
        self.assertIsNotNone(worker.finished_monotonic)
        self.assertFalse(process_group_exists(worker.process.pid))

    def test_three_fake_workers_have_a_real_overlap_window(self) -> None:
        workers = [
            self.worker(self.job(f"worker-{index}"), suffix=f"overlap-{index}")
            for index in range(3)
        ]
        for worker in workers:
            self.spawn(
                worker,
                (sys.executable, "-c", "import time; time.sleep(0.25)"),
            )

        runner.monitor_workers(workers, poll_interval=0.01)

        self.assertTrue(all(worker.status == "DONE" for worker in workers))
        self.assertLess(
            max(worker.started_monotonic for worker in workers),
            min(worker.finished_monotonic for worker in workers),
        )

    def test_parent_canary_is_absent_from_spawned_child(self) -> None:
        worker = self.worker()
        observed = self.root / "child-environment.txt"
        code = (
            "import os,pathlib; "
            f"pathlib.Path({str(observed)!r}).write_text("
            "os.environ.get('PILOTFISH_CANARY_TOKEN','absent'))"
        )
        with patch.dict(
            os.environ,
            {"PILOTFISH_CANARY_TOKEN": "parent-secret"},
            clear=False,
        ):
            self.spawn(worker, (sys.executable, "-c", code))
        runner.monitor_workers([worker], poll_interval=0.01)

        self.assertEqual(worker.status, "DONE")
        self.assertEqual(observed.read_text(encoding="utf-8"), "absent")

    def test_monitor_cancellation_reaps_process_group_for_sigint_and_sigterm(
        self,
    ) -> None:
        for item in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=item.name):
                worker = self.worker(
                    self.job(f"cancel-{item.name.lower()}", timeout_seconds=30),
                    suffix=f"cancel-{item.name.lower()}",
                )
                self.spawn(
                    worker,
                    (sys.executable, "-c", "import time; time.sleep(60)"),
                )
                timer = threading.Timer(0.1, os.kill, args=(os.getpid(), item))
                timer.start()
                self.addCleanup(timer.cancel)

                with runner.CancellationController():
                    with self.assertRaises(runner.PilotfishError) as raised:
                        runner.monitor_workers(
                            [worker],
                            poll_interval=0.02,
                            terminate_grace_seconds=0.1,
                        )

                timer.join(timeout=1)
                self.assertEqual(raised.exception.state, "CANCELLED")
                self.assertEqual(worker.status, "CANCELLED")
                self.assertFalse(process_group_exists(worker.process.pid))


class BoundedSchedulerTests(ProcessTestCase):
    def concurrency_script(self, name: str, sleep_seconds: float = 0.25) -> Path:
        state = self.root / name
        state.mkdir()
        script = state / "fake-codex.py"
        script.write_text(
            "import fcntl, os, time\n"
            "from pathlib import Path\n"
            f"root = Path({str(state)!r})\n"
            "lock = (root / 'lock').open('a+')\n"
            "fcntl.flock(lock.fileno(), fcntl.LOCK_EX)\n"
            "marker = root / f'active-{os.getpid()}'\n"
            "marker.write_text('active')\n"
            "count = len(list(root.glob('active-*')))\n"
            "with (root / 'counts').open('a') as output:\n"
            "    output.write(str(count) + '\\n')\n"
            "fcntl.flock(lock.fileno(), fcntl.LOCK_UN)\n"
            f"time.sleep({sleep_seconds!r})\n"
            "fcntl.flock(lock.fileno(), fcntl.LOCK_EX)\n"
            "marker.unlink()\n"
            "fcntl.flock(lock.fileno(), fcntl.LOCK_UN)\n",
            encoding="utf-8",
        )
        return script

    def test_scheduler_peak_is_exactly_bounded_for_caps_one_two_and_three(
        self,
    ) -> None:
        for cap in (1, 2, 3):
            with self.subTest(max_parallel=cap):
                script = self.concurrency_script(f"cap-{cap}")
                jobs = tuple(self.job(f"job-{index}") for index in range(3))
                workers = tuple(
                    self.worker(job, suffix=f"cap-{cap}-{job.id}") for job in jobs
                )
                manifest = self.manifest(jobs, max_parallel=cap)

                evidence = runner.run_bounded_workers(
                    workers,
                    manifest,
                    (sys.executable, str(script)),
                    self.policy,
                    poll_interval=0.01,
                    terminate_grace_seconds=0.1,
                )

                counts = [
                    int(value)
                    for value in (script.parent / "counts")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                self.assertEqual(max(counts), cap)
                self.assertEqual(evidence["peak_active"], cap)
                self.assertEqual(evidence["max_parallel"], cap)
                self.assertTrue(all(worker.status == "DONE" for worker in workers))

    def test_partial_spawn_failure_reaps_already_started_process_groups(self) -> None:
        script = self.root / "sleeping-fake-codex.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        jobs = tuple(self.job(f"job-{index}", timeout_seconds=30) for index in range(3))
        workers = tuple(self.worker(job) for job in jobs)
        manifest = self.manifest(jobs, max_parallel=2)
        original_spawn = runner.spawn_worker
        calls = 0

        def fail_second_spawn(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic spawn failure")
            original_spawn(*args, **kwargs)

        with patch.object(runner, "spawn_worker", side_effect=fail_second_spawn):
            with self.assertRaisesRegex(OSError, "synthetic spawn failure"):
                runner.run_bounded_workers(
                    workers,
                    manifest,
                    (sys.executable, str(script)),
                    self.policy,
                    poll_interval=0.01,
                    terminate_grace_seconds=0.1,
                )

        self.assertIsNotNone(workers[0].process)
        self.assertIsNotNone(workers[0].process.poll())
        self.assertFalse(process_group_exists(workers[0].process.pid))
        self.assertEqual(workers[0].status, "CANCELLED")
        self.assertEqual(workers[2].status, "CANCELLED")

    def test_scheduler_reaps_spawning_worker_after_internal_cleanup_fails(
        self,
    ) -> None:
        script = self.root / "cleanup-failure-fake-codex.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        worker = self.worker(self.job("job-a", timeout_seconds=30))
        original_terminate = runner.terminate_process_group
        cleanup_calls = 0

        class PublicationFailureWorker:
            def __init__(self, wrapped: runner.WorkerRun) -> None:
                object.__setattr__(self, "wrapped", wrapped)
                object.__setattr__(self, "failed", False)

            def __getattr__(self, name: str) -> object:
                return getattr(self.wrapped, name)

            def __setattr__(self, name: str, value: object) -> None:
                if name == "invocation_argv" and not self.failed:
                    object.__setattr__(self, "failed", True)
                    raise RuntimeError("synthetic post-process publication failure")
                setattr(self.wrapped, name, value)

        failing_worker = PublicationFailureWorker(worker)

        def fail_first_cleanup(
            process: subprocess.Popen[bytes], grace_seconds: float
        ) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise runner.PilotfishError(
                    "WORKER_FAILED", "synthetic internal cleanup failure"
                )
            original_terminate(process, min(grace_seconds, 0.1))

        with patch.object(
            runner,
            "terminate_process_group",
            side_effect=fail_first_cleanup,
        ):
            with self.assertRaises(runner.PilotfishError):
                runner.run_bounded_workers(
                    (failing_worker,),
                    self.manifest((worker.job,)),
                    (sys.executable, str(script)),
                    self.policy,
                    poll_interval=0.01,
                    terminate_grace_seconds=0.1,
                )

        self.assertIsNotNone(worker.process)
        survived = process_group_exists(worker.process.pid)
        if survived:
            original_terminate(worker.process, 0.1)
        self.assertGreaterEqual(cleanup_calls, 2)
        self.assertFalse(survived, "scheduler leaked its spawning worker")

    def test_scheduler_recovers_unpublished_handle_after_cleanup_failure(
        self,
    ) -> None:
        script = self.root / "unpublished-cleanup-failure-codex.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        worker = self.worker(self.job("job-a", timeout_seconds=30))
        original_popen = subprocess.Popen
        original_terminate = runner.terminate_process_group
        spawned: list[subprocess.Popen[bytes]] = []
        cleanup_calls = 0

        class UnpublishableWorker:
            def __init__(self, wrapped: runner.WorkerRun) -> None:
                object.__setattr__(self, "wrapped", wrapped)

            def __getattr__(self, name: str) -> object:
                return getattr(self.wrapped, name)

            def __setattr__(self, name: str, value: object) -> None:
                if name == "process" and value is not None:
                    raise RuntimeError("synthetic process publication failure")
                setattr(self.wrapped, name, value)

        unpublishable = UnpublishableWorker(worker)

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        def fail_first_cleanup(
            process: subprocess.Popen[bytes], grace_seconds: float
        ) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise runner.PilotfishError(
                    "WORKER_FAILED", "synthetic first cleanup failure"
                )
            original_terminate(process, min(grace_seconds, 0.1))

        with (
            patch.object(runner.subprocess, "Popen", side_effect=capture_popen),
            patch.object(
                runner,
                "terminate_process_group",
                side_effect=fail_first_cleanup,
            ),
        ):
            with self.assertRaises(runner.PilotfishError) as raised:
                runner.run_bounded_workers(
                    (unpublishable,),
                    self.manifest((worker.job,)),
                    (sys.executable, str(script)),
                    self.policy,
                    poll_interval=0.01,
                    terminate_grace_seconds=0.1,
                )

        self.assertEqual(len(spawned), 1)
        survived = process_group_exists(spawned[0].pid)
        if survived:
            original_terminate(spawned[0], 0.1)
        self.assertFalse(survived, "structured spawn cleanup leaked its process")
        self.assertGreaterEqual(cleanup_calls, 2)
        self.assertIs(getattr(raised.exception, "process", None), spawned[0])
        self.assertIsInstance(
            getattr(raised.exception, "original_error", None), RuntimeError
        )
        self.assertIsInstance(
            getattr(raised.exception, "cleanup_error", None),
            runner.PilotfishError,
        )
        self.assertIsNone(worker.process)

    def test_timeout_forced_failure_stays_failed_when_sigterm_exits_zero(
        self,
    ) -> None:
        script = self.root / "timeout-zero-fake-codex.py"
        script.write_text(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        job = self.job("timeout-zero", timeout_seconds=1)
        worker = self.worker(job)

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.run_bounded_workers(
                (worker,),
                self.manifest((job,)),
                (sys.executable, str(script)),
                self.policy,
                poll_interval=0.01,
                terminate_grace_seconds=0.5,
            )

        self.assertEqual(raised.exception.state, "WORKER_FAILED")
        self.assertEqual(worker.exit_code, 0)
        self.assertEqual(worker.status, "WORKER_FAILED")
        self.assertFalse(process_group_exists(worker.process.pid))

    def test_event_limit_forced_failure_stays_failed_when_sigterm_exits_zero(
        self,
    ) -> None:
        script = self.root / "event-limit-zero-fake-codex.py"
        script.write_text(
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "os.write(1, b'x' * 2048)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        policy = dataclasses.replace(self.policy, max_event_log_bytes=1024)
        job = self.job("event-limit-zero", timeout_seconds=30)
        worker = self.worker(job)

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.run_bounded_workers(
                (worker,),
                self.manifest((job,)),
                (sys.executable, str(script)),
                policy,
                poll_interval=0.01,
                terminate_grace_seconds=0.5,
            )

        self.assertEqual(raised.exception.state, "WORKER_FAILED")
        self.assertEqual(worker.exit_code, 0)
        self.assertEqual(worker.status, "WORKER_FAILED")
        self.assertFalse(process_group_exists(worker.process.pid))

    def test_nonzero_worker_lets_active_peer_finish_and_cancels_queue(self) -> None:
        script = self.root / "selective-fake-codex.py"
        script.write_text(
            "import sys, time\n"
            "from pathlib import Path\n"
            "worktree = Path(sys.argv[sys.argv.index('-C') + 1])\n"
            "if worktree.name == 'job-0':\n"
            "    raise SystemExit(7)\n"
            "time.sleep(0.2)\n",
            encoding="utf-8",
        )
        jobs = tuple(self.job(f"job-{index}") for index in range(3))
        workers = tuple(self.worker(job) for job in jobs)

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.run_bounded_workers(
                workers,
                self.manifest(jobs, max_parallel=2),
                (sys.executable, str(script)),
                self.policy,
                poll_interval=0.01,
                terminate_grace_seconds=0.1,
            )

        self.assertEqual(raised.exception.state, "WORKER_FAILED")
        self.assertEqual(workers[0].status, "WORKER_FAILED")
        self.assertEqual(workers[1].status, "DONE")
        self.assertEqual(workers[2].status, "CANCELLED")
        self.assertIsNotNone(workers[1].finished_monotonic)


class WorkerResultTests(ProcessTestCase):
    def prepared_worker(
        self,
        *,
        role: str = "executor",
        commands: tuple[runner.CommandSpec, ...] = (),
    ) -> tuple[runner.WorkerRun, runner.Manifest, dict[str, object]]:
        worker = self.worker(
            self.job("worker-a", role=role, commands=commands),
            suffix=f"result-{role}",
        )
        manifest = self.manifest((worker.job,))
        result = self.write_valid_artifacts(worker, manifest)
        return worker, manifest, result

    def assert_quarantined(self, context: unittest.case._AssertRaisesContext) -> None:
        self.assertEqual(context.exception.state, "QUARANTINED")

    def test_valid_result_records_usage_thread_and_strict_attestation(self) -> None:
        command = self.command_spec()
        worker, manifest, _ = self.prepared_worker(commands=(command,))

        result = runner.validate_worker_result(
            worker, manifest, HEAD_SHA, self.policy
        )

        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(worker.thread_id, "thread-1")
        self.assertEqual(worker.validated_result, result)
        self.assertEqual(worker.runtime_metadata["model"], worker.role.model)
        self.assertEqual(worker.runtime_metadata["effort"], worker.role.effort)
        self.assertEqual(worker.runtime_metadata["sandbox"], worker.role.sandbox)
        self.assertIn("strict-attested", worker.runtime_metadata["evidence"])

    def test_nonzero_exit_with_valid_json_is_rejected(self) -> None:
        worker = self.worker(self.job(timeout_seconds=5))
        manifest = self.manifest((worker.job,))
        result = valid_worker_result(
            run_id=manifest.run_id,
            job_id=worker.job.id,
            role=worker.role.name,
            base_sha=manifest.base_sha,
            worktree_head_sha=HEAD_SHA,
            changed_paths=("src/output.txt",),
        )
        code = (
            "import json,pathlib,sys; "
            "print(json.dumps({'type':'thread.started','thread_id':'thread-1'})); "
            "print(json.dumps({'type':'turn.completed','usage':{}})); "
            f"pathlib.Path({str(worker.final_path)!r}).parent.mkdir(parents=True,exist_ok=True); "
            f"pathlib.Path({str(worker.final_path)!r}).write_text({json.dumps(result)!r}); "
            "raise SystemExit(7)"
        )
        self.spawn(worker, (sys.executable, "-c", code))
        runner.monitor_workers([worker], poll_interval=0.01)

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)

        self.assertEqual(raised.exception.state, "WORKER_FAILED")

    def test_truncated_jsonl_is_quarantined(self) -> None:
        path = self.root / "truncated.jsonl"
        path.write_text('{"type": "thread.started"}\n{"type":', encoding="utf-8")

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.read_jsonl(path)

        self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_oversize_jsonl_is_quarantined_before_parsing(self) -> None:
        worker, manifest, _ = self.prepared_worker()
        worker.events_path.write_bytes(b"x" * (self.policy.max_event_log_bytes + 1))

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)

        self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_jsonl_requires_thread_started_and_turn_completed(self) -> None:
        cases = (
            ({"type": "turn.completed", "usage": {}}, "thread.started"),
            ({"type": "thread.started", "thread_id": "thread-1"}, "turn.completed"),
        )
        for event, expected in cases:
            with self.subTest(missing=expected):
                path = self.root / f"missing-{expected}.jsonl"
                path.write_text(json.dumps(event) + "\n", encoding="utf-8")

                with self.assertRaisesRegex(runner.PilotfishError, expected) as raised:
                    runner.read_jsonl(path)

                self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_jsonl_failure_event_is_worker_failed(self) -> None:
        path = self.root / "failed.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"type": "thread.started", "thread_id": "thread-1"},
                    {"type": "error", "message": "synthetic failure"},
                    {"type": "turn.completed", "usage": {}},
                )
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.read_jsonl(path)

        self.assertEqual(raised.exception.state, "WORKER_FAILED")

    def test_oversize_and_schema_invalid_final_are_quarantined(self) -> None:
        worker, manifest, _ = self.prepared_worker()
        worker.final_path.write_bytes(b"x" * (self.policy.max_result_bytes + 1))
        with self.assertRaises(runner.PilotfishError) as oversize:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)
        self.assertEqual(oversize.exception.state, "QUARANTINED")

        invalid = valid_worker_result()
        del invalid["status"]
        worker.final_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(runner.PilotfishError) as schema:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)
        self.assertEqual(schema.exception.state, "QUARANTINED")

    def test_result_identity_mismatches_are_quarantined(self) -> None:
        worker, manifest, original = self.prepared_worker()
        cases = {
            "run_id": "other-run",
            "job_id": "other-job",
            "role": "scout",
            "base_sha": "c" * 40,
            "worktree_head_sha": "d" * 40,
        }
        for key, value in cases.items():
            with self.subTest(field=key):
                mutated = copy.deepcopy(original)
                mutated[key] = value
                worker.final_path.write_text(json.dumps(mutated), encoding="utf-8")

                with self.assertRaises(runner.PilotfishError) as raised:
                    runner.validate_worker_result(
                        worker, manifest, HEAD_SHA, self.policy
                    )

                self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_missing_or_tampered_strict_invocation_is_quarantined(self) -> None:
        worker, manifest, _ = self.prepared_worker()
        original = worker.invocation_argv
        cases = (
            tuple(value for value in original if value != "--strict-config"),
            original + ("-c", "features.plugins=true"),
            tuple(
                "danger-full-access"
                if value == worker.role.sandbox
                else value
                for value in original
            ),
        )
        for argv in cases:
            with self.subTest(argv_tail=argv[-4:]):
                worker.invocation_argv = argv

                with self.assertRaises(runner.PilotfishError) as raised:
                    runner.validate_worker_result(
                        worker, manifest, HEAD_SHA, self.policy
                    )

                self.assertEqual(raised.exception.state, "QUARANTINED")
        worker.invocation_argv = original

    def test_command_claim_id_argv_and_exit_must_match(self) -> None:
        command = self.command_spec("check", ("python3", "-V"))
        worker, manifest, original = self.prepared_worker(commands=(command,))
        mutations = (
            {"id": "other", "argv": ["python3", "-V"], "exit_code": 0},
            {"id": "check", "argv": ["python3", "--version"], "exit_code": 0},
            {"id": "check", "argv": ["python3", "-V"], "exit_code": 1},
        )
        for claim in mutations:
            with self.subTest(claim=claim):
                mutated = copy.deepcopy(original)
                mutated["commands"] = [claim]
                worker.final_path.write_text(json.dumps(mutated), encoding="utf-8")

                with self.assertRaises(runner.PilotfishError) as raised:
                    runner.validate_worker_result(
                        worker, manifest, HEAD_SHA, self.policy
                    )

                self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_command_claim_count_must_match(self) -> None:
        command = self.command_spec()
        worker, manifest, original = self.prepared_worker(commands=(command,))
        original["commands"] = []
        worker.final_path.write_text(json.dumps(original), encoding="utf-8")

        with self.assertRaisesRegex(runner.PilotfishError, "count") as raised:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)

        self.assertEqual(raised.exception.state, "QUARANTINED")

    def test_read_only_role_cannot_report_changed_paths(self) -> None:
        worker, manifest, result = self.prepared_worker(role="scout")
        result["changed_paths"] = ["src/unexpected.txt"]
        worker.final_path.write_text(json.dumps(result), encoding="utf-8")

        with self.assertRaises(runner.PilotfishError) as raised:
            runner.validate_worker_result(worker, manifest, HEAD_SHA, self.policy)

        self.assertEqual(raised.exception.state, "QUARANTINED")


if __name__ == "__main__":
    unittest.main()
