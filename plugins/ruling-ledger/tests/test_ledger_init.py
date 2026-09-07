import shutil, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import ledger_init as li  # noqa: E402


class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "CLAUDE.md").write_text("# 專案\n\n## 其他\n內容\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_index_dir_and_inserts_block(self):
        r = li.init(self.tmp)
        index = self.tmp / "docs" / "裁決帳本.md"
        self.assertTrue(index.exists())
        self.assertTrue((self.tmp / "docs" / "裁決").is_dir())
        text = index.read_text(encoding="utf-8")
        self.assertIn("## 0. 硬禁區", text)
        self.assertIn("| ID | 狀態 | 命題 | 關鍵字 | 層級 | 復活 | 最近復活 |", text)
        cm = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("## 立場來源", cm)
        self.assertLess(cm.index("## 立場來源"), cm.index("## 其他"))
        self.assertTrue(r["backup"] and Path(r["backup"]).exists())
        # 補：其餘鍵值覆蓋（index_created/detail_dir_created 的 True 分支、claude_md == "inserted"）
        self.assertTrue(r["index_created"])
        self.assertTrue(r["detail_dir_created"])
        self.assertEqual(r["claude_md"], "inserted")

    def test_idempotent(self):
        li.init(self.tmp)
        r2 = li.init(self.tmp)
        self.assertFalse(r2["index_created"])
        self.assertEqual(r2["claude_md"], "already")
        cm = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(cm.count("## 立場來源"), 1)
        # 補：detail_dir_created 的 False 分支、"already" 分支下 backup 為 None
        self.assertFalse(r2["detail_dir_created"])
        self.assertIsNone(r2["backup"])

    def test_dry_run_writes_nothing(self):
        li.init(self.tmp, dry_run=True)
        self.assertFalse((self.tmp / "docs" / "裁決帳本.md").exists())
        # 補：dry-run 對整個目標 repo 零寫入（含 CLAUDE.md 不被改動、不留備份、目錄不建立）
        self.assertFalse((self.tmp / "docs" / "裁決").exists())
        self.assertFalse(list(self.tmp.glob("CLAUDE.md.bak-*")))
        cm = (self.tmp / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("## 立場來源", cm)

    def test_missing_claude_md(self):
        # repo 沒有 CLAUDE.md：claude_md 回 "missing"，backup 為 None，且不建立任何 CLAUDE.md
        tmp2 = Path(tempfile.mkdtemp())
        try:
            r = li.init(tmp2)
            self.assertEqual(r["claude_md"], "missing")
            self.assertIsNone(r["backup"])
            self.assertFalse((tmp2 / "CLAUDE.md").exists())
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_insert_block_h1_no_blank_line_after(self):
        # H1 後直接接內容、無空行：不可把區塊插進標題行中間
        claude_md = self.tmp / "CLAUDE.md"
        claude_md.write_text("# 專案\n內容第一行\n", encoding="utf-8")
        li._insert_block(claude_md, "## 立場來源\n\n1. 內容\n")
        text = claude_md.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0], "# 專案")
        self.assertIn("## 立場來源", text)
        self.assertLess(text.index("# 專案"), text.index("## 立場來源"))

    def test_insert_block_file_is_only_h1(self):
        # 檔案只有一行 H1：插入不可破壞唯一那行標題
        claude_md = self.tmp / "CLAUDE.md"
        claude_md.write_text("# 專案\n", encoding="utf-8")
        li._insert_block(claude_md, "## 立場來源\n\n1. 內容\n")
        text = claude_md.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0], "# 專案")
        self.assertIn("## 立場來源", text)
        self.assertLess(text.index("# 專案"), text.index("## 立場來源"))

    def test_insert_block_no_h1(self):
        # 沒有 H1：放最前，不會誤插進任何既有標題中間
        claude_md = self.tmp / "CLAUDE.md"
        claude_md.write_text("## 其他\n內容\n", encoding="utf-8")
        li._insert_block(claude_md, "## 立場來源\n\n1. 內容\n")
        text = claude_md.read_text(encoding="utf-8")
        self.assertLess(text.index("## 立場來源"), text.index("## 其他"))

    def test_backup_collision_never_overwrites(self):
        # 同日第二次 init（使用者移除區塊後再跑）：兩個備份檔都要留著、內容不同，不可覆蓋第一份
        r1 = li.init(self.tmp)
        backup1 = Path(r1["backup"])
        self.assertTrue(backup1.exists())
        backup1_content = backup1.read_text(encoding="utf-8")

        # 模擬使用者手動移除「## 立場來源」區塊，讓第二次 init 判定仍需插入
        (self.tmp / "CLAUDE.md").write_text("# 專案\n\n## 其他\n內容\n改過\n", encoding="utf-8")

        r2 = li.init(self.tmp)
        self.assertEqual(r2["claude_md"], "inserted")
        backup2 = Path(r2["backup"])

        self.assertNotEqual(backup1, backup2)
        self.assertEqual(backup2.name, f"{backup1.name}-2")
        self.assertTrue(backup1.exists())
        self.assertTrue(backup2.exists())
        # 第一份備份沒被第二次寫入動過
        self.assertEqual(backup1.read_text(encoding="utf-8"), backup1_content)
        self.assertNotEqual(backup1_content, backup2.read_text(encoding="utf-8"))

    def test_insert_block_preserves_crlf(self):
        # CRLF 檔案：插入後除新增區塊外其餘行仍是 \r\n，新增區塊也用 \r\n
        claude_md = self.tmp / "CLAUDE.md"
        with open(claude_md, "w", encoding="utf-8", newline="") as f:
            f.write("# 專案\r\n\r\n## 其他\r\n內容\r\n")
        li._insert_block(claude_md, "## 立場來源\n\n1. 內容\n")
        with open(claude_md, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        self.assertIn("# 專案\r\n", raw)
        self.assertIn("## 其他\r\n", raw)
        self.assertIn("內容\r\n", raw)
        self.assertIn("## 立場來源\r\n", raw)
        self.assertIn("1. 內容\r\n", raw)
        # 不應該出現落單的 \n（沒有前面配對的 \r）
        self.assertNotIn("\n", raw.replace("\r\n", ""))

    def test_insert_block_preserves_lf(self):
        # LF 檔案：行為不變，不會被改成 CRLF
        claude_md = self.tmp / "CLAUDE.md"
        with open(claude_md, "w", encoding="utf-8", newline="") as f:
            f.write("# 專案\n\n## 其他\n內容\n")
        li._insert_block(claude_md, "## 立場來源\n\n1. 內容\n")
        with open(claude_md, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        self.assertNotIn("\r", raw)
        self.assertIn("## 立場來源\n", raw)


if __name__ == "__main__":
    unittest.main()
