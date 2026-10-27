"""Platform-neutral checks for the Windows support: argv shapes, path folding and restore planning.
Nothing here needs PowerShell, so the suite runs the same on Linux, WSL and Windows."""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import inventory as inv  # noqa: E402
import restore as rs  # noqa: E402

WIN_CODEX = r"C:\Users\u\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64" \
            r"\vendor\x86_64-pc-windows-msvc\bin\codex.exe"
WIN_CODEX_APP = r"C:\Users\u\AppData\Local\OpenAI\Codex\bin\2993767bd1dfb009\codex.exe"


class ExeNameTests(unittest.TestCase):
    def test_strips_exe_and_accepts_both_separators(self):
        self.assertEqual(inv.exe_name(r"C:\Users\u\.local\bin\claude.exe"), "claude")
        self.assertEqual(inv.exe_name("C:/Users/u/.local/bin/Claude.EXE"), "claude")
        self.assertTrue(inv.is_claude_main([r"C:\Tools\CLAUDE.EXE"]))
        self.assertTrue(inv.is_codex_main([r"C:\Tools\VENDOR\codex.EXE"]))
        self.assertEqual(inv.exe_name("/home/u/.local/bin/claude"), "claude")
        self.assertEqual(inv.exe_name("claude"), "claude")


class MainProcessTests(unittest.TestCase):
    def test_claude_windows_forms(self):
        self.assertTrue(inv.is_claude_main([r"C:\Users\u\.local\bin\claude.exe"]))
        self.assertTrue(inv.is_claude_main(["claude", "--dangerously-skip-permissions"]))

    def test_claude_helpers_are_not_main(self):
        self.assertFalse(inv.is_claude_main([r"C:\Users\u\.local\bin\claude.exe", "--chrome-native-host"]))
        self.assertFalse(inv.is_claude_main(["claude", "--chrome-native-host"]))
        self.assertFalse(inv.is_claude_main(["claude", "-p", "hi"]))
        self.assertFalse(inv.is_claude_main(["claude", "--output-format", "stream-json"]))

    def test_codex_npm_binary_on_windows_is_main(self):
        self.assertTrue(inv.is_codex_main([WIN_CODEX]))
        self.assertTrue(inv.is_codex_main([WIN_CODEX, "resume", "019a"]))
        self.assertEqual(inv.argv_resume_sid([WIN_CODEX, "resume", "019a"]), "019a")

    def test_codex_desktop_app_and_servers_are_skipped(self):
        self.assertFalse(inv.is_codex_main([WIN_CODEX_APP, "app-server"]))
        self.assertFalse(inv.is_codex_main([WIN_CODEX_APP]))  # not under vendor/
        self.assertFalse(inv.is_codex_main([WIN_CODEX, "exec-server", "--remote", "x"]))
        self.assertFalse(inv.is_codex_main([WIN_CODEX, "app-server"]))
        self.assertFalse(inv.is_codex_main(["/v/vendor/codex", "exec-server"]))


class WindowsPathTests(unittest.TestCase):
    def test_drive_paths_fold_case_and_separators(self):
        self.assertEqual(inv.normalize_path("D:/Projects/ZeroPOS"), r"d:\projects\zeropos")
        self.assertEqual(inv.normalize_path(r"d:\Projects\ZeroPOS\\"), r"d:\projects\zeropos")
        self.assertEqual(inv.normalize_path("D:\\"), "d:\\")
        self.assertEqual(inv.normalize_path("D:/"), "d:\\")
        self.assertTrue(inv.is_windows_path(r"D:\x"))
        self.assertTrue(inv.is_windows_path("c:/x"))
        self.assertFalse(inv.is_windows_path("/home/u"))
        self.assertFalse(inv.is_windows_path(r"\\wsl.localhost\Ubuntu\home\u"))

    def test_posix_and_wsl_paths_unchanged(self):
        self.assertEqual(inv.normalize_path("/home/u/x/"), "/home/u/x")
        self.assertEqual(inv.normalize_path(r"\\wsl.localhost\Ubuntu\home\u\x"), "/home/u/x")


class WindowsRestorePlanTests(unittest.TestCase):
    def snap(self, **kw):
        base = {"runtime": "claude", "sid": "s1", "name": "n", "cwd": r"D:\Projects\ZeroPOS", "status": "idle",
                "flags": [], "dropped": [], "in_orca": False, "resumable": True, "hint": ""}
        base.update(kw)
        return base

    def test_windows_session_in_registered_worktree_is_reopened(self):
        plan = rs.build_plan([self.snap()], live_sids=set(), worktrees={inv.normalize_path("D:/Projects/ZeroPOS"): "id:wt"})
        self.assertEqual([(s["sid"], s["selector"]) for s in plan["orca"]], [("s1", "id:wt")])

    def test_windows_session_outside_orca_is_listed_only(self):
        plan = rs.build_plan([self.snap(cwd=r"D:\elsewhere")], live_sids=set(),
                             worktrees={inv.normalize_path("D:/Projects/ZeroPOS"): "id:wt"})
        self.assertEqual([s["sid"] for s in plan["terminal"]], ["s1"])
        self.assertEqual(plan["unregistered"], [])

    def test_windows_session_never_targets_wsl_worktree(self):
        plan = rs.build_plan([self.snap(cwd=r"D:\Projects\ZeroPOS")], live_sids=set(),
                             worktrees={inv.normalize_path(r"\\wsl.localhost\Ubuntu\home\u\ZeroPOS"): "id:wsl"})
        self.assertEqual(plan["orca"], [])
        self.assertEqual([s["sid"] for s in plan["terminal"]], ["s1"])

    def test_posix_rules_unchanged(self):
        sessions = [self.snap(sid="t", cwd="/repo", in_orca=False), self.snap(sid="u", cwd="/other", in_orca=True)]
        plan = rs.build_plan(sessions, live_sids=set(), worktrees={"/repo": "id:wt"})
        self.assertEqual([s["sid"] for s in plan["terminal"]], ["t"])
        self.assertEqual([s["sid"] for s in plan["unregistered"]], ["u"])


class WindowsProcessListTests(unittest.TestCase):
    def test_powershell_failure_is_flagged_not_silent(self):
        orig = (inv.IS_WINDOWS, inv.run_full, inv._win_procs, inv.win_procs_note)
        try:
            inv.IS_WINDOWS, inv.run_full, inv._win_procs = True, (lambda *a, **k: ("", "boom")), None
            self.assertEqual(inv.win_procs(refresh=True), {})
            self.assertIn("boom", inv.win_procs_note)
        finally:
            inv.IS_WINDOWS, inv.run_full, inv._win_procs, inv.win_procs_note = orig

    @unittest.skipUnless(os.name == "nt", "CommandLineToArgvW only exists on Windows")
    def test_win_split_uses_os_rules(self):
        self.assertEqual(inv.win_split(r'"C:\Program Files\x\claude.exe" --resume abc'),
                         [r"C:\Program Files\x\claude.exe", "--resume", "abc"])
        self.assertEqual(inv.win_split(""), [])

    @unittest.skipUnless(os.name == "nt", "needs PowerShell")
    def test_live_pids_includes_this_process(self):
        self.assertIn(os.getpid(), inv.live_pids())
        self.assertEqual(inv.exe_name(inv.proc_args(os.getpid())[0]).lower(), "python")


if __name__ == "__main__":
    unittest.main()
