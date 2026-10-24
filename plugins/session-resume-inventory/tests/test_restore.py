import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import inventory as inv  # noqa: E402
import restore as rs  # noqa: E402


class RestoreFlagsTests(unittest.TestCase):
    def test_claude_keeps_bypass_and_drops_resume(self):
        kept, dropped = inv.restore_flags("claude", ["claude", "--resume", "abc", "--dangerously-skip-permissions"])
        self.assertEqual(kept, ["--dangerously-skip-permissions"])
        self.assertEqual(dropped, [])

    def test_claude_keeps_value_flags_and_reports_unknown(self):
        kept, dropped = inv.restore_flags(
            "claude", ["claude", "--model", "opus", "--effort=high", "-c", "--debug", "修一下 bug"])
        self.assertEqual(kept, ["--model", "opus", "--effort=high"])
        self.assertEqual(dropped, ["--debug", "修一下 bug"])

    def test_claude_resume_without_value_keeps_next_flag(self):
        kept, dropped = inv.restore_flags("claude", ["claude", "--resume", "--model", "opus"])
        self.assertEqual(kept, ["--model", "opus"])
        self.assertEqual(dropped, [])

    def test_claude_drops_name_flag_silently(self):
        kept, dropped = inv.restore_flags("claude", ["claude", "-n", "my-session", "--chrome"])
        self.assertEqual(kept, ["--chrome"])
        self.assertEqual(dropped, [])

    def test_codex_resume_subcommand_and_flags(self):
        kept, dropped = inv.restore_flags(
            "codex", ["/x/vendor/codex", "resume", "019a-id", "--dangerously-bypass-approvals-and-sandbox",
                      "-m", "gpt-5"])
        self.assertEqual(kept, ["--dangerously-bypass-approvals-and-sandbox", "-m", "gpt-5"])
        self.assertEqual(dropped, [])

    def test_resume_command_quotes(self):
        cmd = rs.resume_command({"runtime": "claude", "sid": "abc", "flags": ["--model", "opus 4"]})
        self.assertEqual(cmd, "claude --resume abc --model 'opus 4'")
        cmd = rs.resume_command({"runtime": "codex", "sid": "x1", "flags": ["--yolo"]})
        self.assertEqual(cmd, "codex resume x1 --yolo")


class ArgvResumeTests(unittest.TestCase):
    def test_claude_resume_forms(self):
        self.assertEqual(rs.argv_resume_sid(["claude", "--resume", "abc", "--x"]), "abc")
        self.assertEqual(rs.argv_resume_sid(["claude", "--resume=abc"]), "abc")
        self.assertEqual(rs.argv_resume_sid(["claude", "-r", "abc"]), "abc")
        self.assertIsNone(rs.argv_resume_sid(["claude", "--dangerously-skip-permissions"]))
        self.assertIsNone(rs.argv_resume_sid(["claude", "--resume", "--model", "opus"]))

    def test_codex_resume_subcommand(self):
        self.assertEqual(rs.argv_resume_sid(["/v/vendor/codex", "resume", "019a", "--yolo"]), "019a")
        self.assertIsNone(rs.argv_resume_sid(["/v/vendor/codex", "resume", "--last"]))


class CodexReviewFixTests(unittest.TestCase):
    def test_variadic_claude_flags_keep_all_values(self):
        kept, dropped = inv.restore_flags(
            "claude", ["claude", "--add-dir", "/a", "/b", "--mcp-config", "x.json", "y.json", "--chrome"])
        self.assertEqual(kept, ["--add-dir", "/a", "/b", "--mcp-config", "x.json", "y.json", "--chrome"])
        self.assertEqual(dropped, [])

    def test_shared_helpers_live_in_inventory(self):
        self.assertEqual(inv.normalize_path("\\\\wsl.localhost\\Ubuntu\\home\\a"), "/home/a")
        self.assertEqual(inv.argv_resume_sid(["/v/vendor/codex", "resume", "019a"]), "019a")

    def test_orca_exe_never_falls_back_to_bare_orca(self):
        import shutil
        orig = shutil.which
        shutil.which = lambda name: "/usr/bin/orca" if name == "orca" else None
        try:
            self.assertIsNone(inv.orca_exe())
        finally:
            shutil.which = orig

    def test_snapshot_is_owner_only(self):
        import tempfile, stat
        with tempfile.TemporaryDirectory() as d:
            orig = inv.SNAPSHOT_DIR
            inv.SNAPSHOT_DIR = Path(d) / "snaps"
            try:
                out = inv.save_snapshot([], None)
            finally:
                inv.SNAPSHOT_DIR = orig
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(out.parent.stat().st_mode), 0o700)


class CodexReviewRound2Tests(unittest.TestCase):
    def test_codex_resume_options_before_id(self):
        args = ["/v/vendor/codex", "resume", "--model", "gpt-5", "--yolo", "019a"]
        self.assertEqual(inv.argv_resume_sid(args), "019a")
        kept, dropped = inv.restore_flags("codex", args)
        self.assertEqual(kept, ["--model", "gpt-5", "--yolo"])
        self.assertEqual(dropped, [])

    def test_codex_resume_last_has_no_id(self):
        self.assertIsNone(inv.argv_resume_sid(["/v/vendor/codex", "resume", "-m", "x", "--last"]))

    def test_custom_save_path_leaves_parent_permissions_alone(self):
        import tempfile, stat
        with tempfile.TemporaryDirectory() as d:
            Path(d).chmod(0o755)
            out = Path(d) / "snap.json"
            inv.save_snapshot([], out)
            self.assertEqual(stat.S_IMODE(Path(d).stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)


class CodexReviewRound3Tests(unittest.TestCase):
    def test_codex_resume_last_with_prompt_has_no_id(self):
        args = ["/v/vendor/codex", "resume", "--last", "continue fixing tests"]
        self.assertIsNone(inv.argv_resume_sid(args))
        kept, dropped = inv.restore_flags("codex", args)
        self.assertEqual(kept, [])
        self.assertEqual(dropped, ["continue fixing tests"])

    def test_codex_resume_variadic_image(self):
        args = ["/v/vendor/codex", "resume", "--image", "a.png", "b.png", "--model", "gpt-5", "019a"]
        self.assertEqual(inv.argv_resume_sid(args), "019a")
        kept, dropped = inv.restore_flags("codex", args)
        self.assertEqual(kept, ["--model", "gpt-5"])
        self.assertEqual(dropped, ["--image", "a.png", "b.png"])


class PlanTests(unittest.TestCase):
    def snap(self, **kw):
        base = {"runtime": "claude", "sid": "s1", "name": "n", "cwd": "/repo", "status": "idle",
                "flags": [], "dropped": [], "in_orca": True, "resumable": True, "hint": ""}
        base.update(kw)
        return base

    def test_classification(self):
        sessions = [
            self.snap(sid="live"),
            self.snap(sid="orca-ok"),
            self.snap(sid="orca-unreg", cwd="/other"),
            self.snap(sid="terminal", in_orca=False),
            self.snap(sid="broken", resumable=False),
            self.snap(sid=None),
        ]
        plan = rs.build_plan(sessions, live_sids={"live"}, worktrees={"/repo": "id:wt1"})
        self.assertEqual([s["sid"] for s in plan["already_live"]], ["live"])
        self.assertEqual([(s["sid"], s["selector"]) for s in plan["orca"]], [("orca-ok", "id:wt1")])
        self.assertEqual([s["sid"] for s in plan["unregistered"]], ["orca-unreg"])
        self.assertEqual([s["sid"] for s in plan["terminal"]], ["terminal"])
        self.assertEqual([s["sid"] for s in plan["skipped"]], ["broken", None])

    def test_duplicate_sid_in_snapshot_opens_once(self):
        plan = rs.build_plan([self.snap(), self.snap()], live_sids=set(), worktrees={"/repo": "id:wt1"})
        self.assertEqual(len(plan["orca"]), 1)

    def test_closed_session_not_reopened(self):
        sessions = [self.snap(sid="closed"), self.snap(sid="open"), self.snap(sid="closed-live")]
        plan = rs.build_plan(sessions, live_sids={"closed-live"}, worktrees={"/repo": "id:wt1"},
                             not_needed={"closed": rs.REASON_CLOSED, "closed-live": rs.REASON_CLOSED})
        self.assertEqual([(s["sid"], s["reason"]) for s in plan["not_needed"]], [("closed", rs.REASON_CLOSED)])
        self.assertEqual([s["sid"] for s in plan["orca"]], ["open"])
        self.assertEqual([s["sid"] for s in plan["already_live"]], ["closed-live"])

    def test_wsl_unc_path_normalized(self):
        self.assertEqual(rs.normalize_path("\\\\wsl.localhost\\Ubuntu\\home\\haha\\x"), "/home/haha/x")
        self.assertEqual(rs.normalize_path("/home/haha/x/"), "/home/haha/x")


class SessionCloseTests(unittest.TestCase):
    def write(self, path: Path, records: list[dict]) -> Path:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def user(text, **kw):
        return {"type": "user", "message": {"role": "user", "content": text}, **kw}

    def test_claude_slash_command_marks_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [
                self.user("修 bug"),
                self.user("<command-message>session-close</command-message>\n<command-name>/session-close</command-name>"),
            ])
            self.assertEqual(rs.claude_skip_reason(t), rs.REASON_CLOSED)

    def test_claude_skill_tool_call_marks_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [{"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": "session-close"}}]}}])
            self.assertEqual(rs.claude_skip_reason(t), rs.REASON_CLOSED)

    def test_claude_mentions_and_sidechain_do_not_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [
                self.user("有輸入 /session-close 指令過的就不用打開了"),
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "<command-name>/session-close</command-name>"}]}},
                self.user("<command-name>/session-close</command-name>", isSidechain=True),
            ])
            self.assertIsNone(rs.claude_skip_reason(t))

    def test_claude_inventory_only_session_is_not_needed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [
                self.user("<command-message>session-resume-inventory:session-resume-inventory</command-message>\n"
                          "<command-name>/session-resume-inventory:session-resume-inventory</command-name>"),
                self.user("<command-name>/model</command-name>"),
                self.user("存session"),
                self.user("Stop hook feedback: 缺逐項結果"),
            ])
            self.assertEqual(rs.claude_skip_reason(t), rs.REASON_INVENTORY_ONLY)

    def test_claude_restore_session_with_bash_call_is_not_needed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [
                self.user("復原 session"),
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
                    "input": {"command": "python3 /p/scripts/restore.py --apply"}}]}},
                self.user("resume"),
            ])
            self.assertEqual(rs.claude_skip_reason(t), rs.REASON_INVENTORY_ONLY)

    def test_claude_inventory_plus_real_work_is_reopened(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = self.write(Path(d) / "a.jsonl", [
                self.user("<command-name>/session-resume-inventory</command-name>"),
                self.user("存session"),
                self.user("修改進這個skills內"),
            ])
            self.assertIsNone(rs.claude_skip_reason(t))
            plain = self.write(Path(d) / "b.jsonl", [self.user("存session")])
            self.assertIsNone(rs.claude_skip_reason(plain))

    def test_other_slash_command_paste_or_shell_counts_as_work(self):
        import tempfile
        inventory = self.user("<command-name>/session-resume-inventory</command-name>")
        work_records = [
            self.user("<command-message>version-check</command-message>\n<command-name>/version-check</command-name>"),
            self.user("\n\n<pasted_content id=\"x\">log</pasted_content>\n\n這什麼意思? 幫我修"),
            self.user("<bash-input>git status</bash-input>"),
        ]
        with tempfile.TemporaryDirectory() as d:
            for i, work in enumerate(work_records):
                t = self.write(Path(d) / f"{i}.jsonl", [inventory, work, self.user("存session")])
                self.assertIsNone(rs.claude_skip_reason(t), work["message"]["content"])

    def test_mgmt_prompt_must_be_only_session_words(self):
        for text in ("存session", "存SESSION", "復原 session", "回復session", "resume session", "resume",
                     "把 session 開回來"):
            self.assertTrue(rs.is_session_mgmt_prompt(text), text)
        for text in ("記錄session然後幫我修bug", "修 session bug", "session 為什麼掛了"):
            self.assertFalse(rs.is_session_mgmt_prompt(text), text)

    def test_malformed_records_fail_open(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            projects, codex_root = Path(d) / "projects", Path(d) / "codex"
            (projects / "-repo").mkdir(parents=True)
            (projects / "-repo" / "c1.jsonl").write_text('{"type":"user","message":null}\n', encoding="utf-8")
            (codex_root / "2026").mkdir(parents=True)
            (codex_root / "2026" / "rollout-x1.jsonl").write_text('{"payload":null,"role":"user"}\n'
                                                                    '{"x":1,"role":"user"}\n', encoding="utf-8")
            sessions = [{"runtime": "claude", "sid": "c1"}, {"runtime": "codex", "sid": "x1"}]
            self.assertEqual(rs.not_needed_sessions(sessions, projects, codex_root), {})

    def test_codex_user_invocation_marks_closed(self):
        import tempfile
        def rec(text):
            return {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                          "content": [{"type": "input_text", "text": text}]}}
        with tempfile.TemporaryDirectory() as d:
            closed = self.write(Path(d) / "c.jsonl", [rec("hi"), rec("$session-close")])
            mention = self.write(Path(d) / "m.jsonl", [rec("說明一下 $session-close 是什麼")])
            self.assertEqual(rs.codex_skip_reason(closed), rs.REASON_CLOSED)
            self.assertIsNone(rs.codex_skip_reason(mention))

    def test_not_needed_sessions_looks_up_transcripts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            projects, codex_root = Path(d) / "projects", Path(d) / "codex"
            self.write(projects / "-repo" / "c1.jsonl", [self.user("<command-name>/session-close</command-name>")])
            self.write(projects / "-repo" / "c2.jsonl", [self.user("繼續")])
            self.write(codex_root / "2026" / "rollout-x-x1.jsonl", [{"type": "response_item", "payload": {
                "role": "user", "content": [{"type": "input_text", "text": "/session-close"}]}}])
            sessions = [{"runtime": "claude", "sid": "c1"}, {"runtime": "claude", "sid": "c2"},
                        {"runtime": "claude", "sid": "missing"}, {"runtime": "codex", "sid": "x1"},
                        {"runtime": "codex", "sid": None}]
            self.assertEqual(rs.not_needed_sessions(sessions, projects, codex_root),
                             {"c1": rs.REASON_CLOSED, "x1": rs.REASON_CLOSED})


if __name__ == "__main__":
    unittest.main()
