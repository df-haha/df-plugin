import re, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "ruling-ledger" / "SKILL.md"


class SkillDocTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_judgement_input_format_present(self):
        for token in [
            "判定輸入（固定格式）",
            "R-ID：",
            "檔案:行：",
            "命中詞：",
            "原句：",
            "前後 3 行：",
            "命題：",
            "範圍：",
        ]:
            self.assertIn(token, self.text)

    def test_three_valued_output_and_clean_hit_log(self):
        for token in ["**復活**", "**乾淨**", "**誤報**", "乾淨命中 檔案:行"]:
            self.assertIn(token, self.text)

    def test_read_detail_first_and_no_claude_md_pinning(self):
        self.assertIn("必讀詳情的三個時機", self.text)
        self.assertIn("判定前先 Read 該 R 的詳情檔", self.text)
        self.assertIsNone(re.search(r"釘.{0,4}CLAUDE\.md", self.text))
        self.assertIn("## 0. 硬禁區", self.text)


if __name__ == "__main__":
    unittest.main()
