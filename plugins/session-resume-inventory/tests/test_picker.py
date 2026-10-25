import json, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import picker as pk  # noqa: E402

SESSIONS = [
    {"runtime": "claude", "sid": "aaaa1111-0000-0000-0000-000000000000", "name": "proj-a", "cwd": "/w/proj",
     "status": "idle", "flags": [], "dropped": [], "in_orca": True, "resumable": True, "hint": "修 bug"},
    {"runtime": "claude", "sid": "bbbb2222-0000-0000-0000-000000000000", "name": "proj-b", "cwd": "/w/proj",
     "status": "idle", "flags": [], "dropped": [], "in_orca": True, "resumable": True, "hint": "x" * 300},
    {"runtime": "claude", "sid": "cccc3333-0000-0000-0000-000000000000", "name": "other", "cwd": "/w/other",
     "status": "busy", "flags": [], "dropped": [], "in_orca": True, "resumable": True, "hint": ""},
    {"runtime": "codex", "sid": None, "name": None, "cwd": "/w", "status": "empty", "flags": [], "dropped": [],
     "in_orca": False, "resumable": False, "hint": ""},
]


def write_snap(d: Path, name: str = "20261025-021719.json") -> Path:
    p = d / name
    p.write_text(json.dumps({"saved_at": "2026-10-25T02:17:19+08:00", "sessions": SESSIONS}), encoding="utf-8")
    return p


class RowsTests(unittest.TestCase):
    def test_rows_skip_unresumable_and_use_notes(self):
        rows = pk.build_rows(SESSIONS, {"aaaa1111": {"st": "done", "desc": "已合併", "proj": "專案甲"}})
        self.assertEqual([r["sid"] for r in rows], ["aaaa1111", "bbbb2222", "cccc3333"])
        a = rows[0]
        self.assertEqual((a["st"], a["desc"], a["proj"]), ("done", "已合併", "專案甲"))

    def test_rows_fallback_desc_and_project(self):
        rows = {r["sid"]: r for r in pk.build_rows(SESSIONS, {})}
        self.assertEqual(rows["bbbb2222"]["proj"], "proj")
        self.assertTrue(rows["bbbb2222"]["desc"].endswith("…"))
        self.assertLessEqual(len(rows["bbbb2222"]["desc"]), 121)
        self.assertEqual(rows["cccc3333"]["st"], "going")  # busy 視為進行中
        self.assertEqual(rows["aaaa1111"]["st"], "unknown")

    def test_html_embeds_rows_without_full_ids(self):
        html = pk.render_html(pk.build_rows(SESSIONS, {}), mode="save", saved_at="2026-10-25T02:17:19+08:00")
        self.assertIn("aaaa1111", html)
        self.assertNotIn("aaaa1111-0000", html)
        self.assertIn("要存", html)
        self.assertNotIn("%", html.split("<script>")[1].split("const DATA=")[0])

    def test_html_escapes_script_close(self):
        rows = pk.build_rows(SESSIONS, {"aaaa1111": {"desc": "</script><b>x"}})
        html = pk.render_html(rows, mode="restore", saved_at="t")
        self.assertNotIn("</script><b>", html)


    def test_html_survives_comment_script_trick(self):
        rows = pk.build_rows(SESSIONS, {"aaaa1111": {"desc": "看 <!--<script> 怎麼寫"}})
        html = pk.render_html(rows, mode="save", saved_at="t")
        body = html.split("<script>", 1)[1]
        self.assertNotIn("<!--", body)
        self.assertNotIn("<script", body)


class ParseTests(unittest.TestCase):
    TEXT = ("復原 session 選擇結果（快照 x）\n要開回 1 個：\n- p｜proj-a（aaaa1111）\n\n"
            "不開 2 個：\n- p｜proj-b（bbbb2222）\n- o｜other（cccc3333）\n")

    def test_parse_only_takes_picked_section(self):
        self.assertEqual(pk.parse_picked(self.TEXT), ["aaaa1111"])

    def test_parse_save_wording(self):
        text = "存 session 選擇結果\n要存 2 個：\n- a（aaaa1111）\n- b（bbbb2222）\n\n不存 0 個：\n- 無\n"
        self.assertEqual(pk.parse_picked(text), ["aaaa1111", "bbbb2222"])

    def test_parse_ignores_hex_looking_names(self):
        text = "要存 1 個：\n- race-1a2b3c4d｜deadbeef（aaaa1111）\n\n不存 0 個：\n- 無\n"
        self.assertEqual(pk.parse_picked(text), ["aaaa1111"])

    def test_parse_plain_list(self):
        self.assertEqual(pk.parse_picked("aaaa1111, bbbb2222"), ["aaaa1111", "bbbb2222"])


class ApplyTests(unittest.TestCase):
    def test_save_mode_rewrites_in_place_and_backs_up_full(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); snap = write_snap(d); snap.chmod(0o664)
            out = pk.apply_pick(snap, ["aaaa1111", "cccc3333"], mode="save")
            self.assertEqual(out, snap)
            kept = json.loads(snap.read_text(encoding="utf-8"))["sessions"]
            self.assertEqual([s["name"] for s in kept], ["proj-a", "other"])
            full = d / "full" / snap.name
            self.assertEqual(len(json.loads(full.read_text(encoding="utf-8"))["sessions"]), 4)
            self.assertEqual(sorted(p.name for p in d.glob("*.json")), [snap.name])  # restore 只看得到篩過的
            self.assertEqual(snap.stat().st_mode & 0o777, 0o600)
            self.assertEqual(full.stat().st_mode & 0o777, 0o600)

    def test_save_mode_repick_starts_from_full_list(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); snap = write_snap(d)
            pk.apply_pick(snap, ["aaaa1111"], mode="save")
            pk.apply_pick(snap, ["bbbb2222", "cccc3333"], mode="save")
            kept = json.loads(snap.read_text(encoding="utf-8"))["sessions"]
            self.assertEqual([s["name"] for s in kept], ["proj-b", "other"])
            self.assertEqual(len(json.loads((d / "full" / snap.name).read_text(encoding="utf-8"))["sessions"]), 4)

    def test_restore_mode_writes_separate_file(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); snap = write_snap(d)
            out = pk.apply_pick(snap, ["bbbb2222"], mode="restore")
            self.assertEqual(out.parent, d / "picked")
            self.assertEqual(len(json.loads(snap.read_text(encoding="utf-8"))["sessions"]), 4)
            self.assertEqual([s["name"] for s in json.loads(out.read_text(encoding="utf-8"))["sessions"]], ["proj-b"])

    def test_unknown_or_ambiguous_prefix_fails(self):
        with tempfile.TemporaryDirectory() as t:
            snap = write_snap(Path(t))
            with self.assertRaises(SystemExit):
                pk.apply_pick(snap, ["deadbeef"], mode="restore")
            with self.assertRaises(SystemExit):
                pk.apply_pick(snap, [], mode="restore")


if __name__ == "__main__":
    unittest.main()
