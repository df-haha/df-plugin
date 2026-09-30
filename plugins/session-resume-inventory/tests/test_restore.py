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
                      "-m", "gpt-5", "--last"])
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
            out = Path(d) / "sub" / "snap.json"
            inv.save_snapshot([], out)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(out.parent.stat().st_mode), 0o700)


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

    def test_wsl_unc_path_normalized(self):
        self.assertEqual(rs.normalize_path("\\\\wsl.localhost\\Ubuntu\\home\\haha\\x"), "/home/haha/x")
        self.assertEqual(rs.normalize_path("/home/haha/x/"), "/home/haha/x")


if __name__ == "__main__":
    unittest.main()
