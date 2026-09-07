import shutil, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import ledger_check as lc  # noqa: E402

FX = HERE / "fixtures"


class ParseTests(unittest.TestCase):
    def test_parse_index_ok(self):
        idx = lc.parse_index(FX / "ok")
        self.assertEqual([w.path for w in idx.whitelist], ["草稿/報告.md"])
        self.assertEqual(idx.hard_zone, [])
        self.assertEqual(len(idx.active), 1)
        row = idx.active[0]
        self.assertEqual((row.id, row.status), ("R-0001", "打掉"))
        self.assertEqual(row.keywords, ["年約", "年度框架合約"])
        self.assertEqual(row.tier, 0)
        self.assertEqual(row.resurrect_count, 0)
        self.assertEqual(row.last_resurrection, "")
        self.assertFalse(row.warned)
        self.assertEqual(idx.archived, [])
        self.assertEqual(idx.problems, [])

    def test_parse_detail_ok(self):
        d = lc.parse_detail(FX / "ok" / "docs" / "裁決" / "R-0001.md")
        self.assertEqual(d.id, "R-0001")
        self.assertEqual(d.status, "打掉")
        self.assertIn("blanket PO", d.synonyms)
        self.assertTrue(d.review_condition)
        self.assertEqual(d.history, ["- 2026-08-28 打掉"])

    def test_section_heading_variants(self):
        idx = lc.parse_index(FX / "variant")
        self.assertEqual(len(idx.active), 1)
        self.assertEqual([w.path for w in idx.whitelist], ["草稿/報告.md"])
        self.assertEqual(idx.hard_zone, [])

    def test_escaped_pipe_kept_in_cell(self):
        idx = lc.parse_index(FX / "variant")
        self.assertEqual(idx.active[0].claim, "年約當主軸（含 A|B 兩案）")
        self.assertEqual(idx.active[0].keywords, ["年約", "年度框架合約"])

    def test_detail_indented_continuation(self):
        d = lc.parse_detail(FX / "variant" / "docs" / "裁決" / "R-0001.md")
        self.assertIn("核心商業模式", d.claim)
        self.assertIn("blanket PO", d.synonyms)


class LintTests(unittest.TestCase):
    def test_ok_has_no_problems(self):
        self.assertEqual(lc.lint(FX / "ok"), [])

    def test_bad_lint_reports_expected_problems(self):
        problems = "\n".join(lc.lint(FX / "bad_lint"))
        self.assertIn("R-0002: 缺詳情檔", problems)
        self.assertIn("R-0001: 索引狀態", problems)
        self.assertIn("R-0003: 狀態", problems)
        self.assertIn("欄數應為 7", problems)

    def test_id_must_increase(self):
        problems = "\n".join(lc.lint(FX / "bad_lint"))
        self.assertIn("R-0005: B 段 ID 未依出現順序遞增", problems)

    def test_defeated_entry_requires_fields(self):
        problems = "\n".join(lc.lint(FX / "bad_lint"))
        self.assertIn("R-0005: 打掉項缺同義表達", problems)
        self.assertIn("R-0005: 打掉項缺重審條件", problems)

    def test_history_line_format(self):
        problems = "\n".join(lc.lint(FX / "bad_lint"))
        self.assertIn("R-0001: 沿革該行不是「- YYYY-MM-DD 」開頭", problems)


class TierTests(unittest.TestCase):
    def test_tier_format_consistency(self):
        problems = "\n".join(lc.lint(FX / "tier"))
        self.assertIn("R-0001: 在硬禁區但層級為 0", problems)
        self.assertIn("R-0003: 層級 2 但索引該行 ID 前缺 ⚠", problems)
        self.assertIn("R-0003: 層級 2 但不在「## 0. 硬禁區」段", problems)

    def test_tier_upgrade_hints(self):
        problems = "\n".join(lc.lint(FX / "tier"))
        self.assertIn("提醒：R-0002 復活累計 1 次，提議升 1", problems)
        self.assertNotIn("提醒：R-0001 復活", problems)


class LintCoverageTests(unittest.TestCase):
    """補齊 fix round 1 指出的分支覆蓋缺口（bad_lint2 fixture）。"""

    def test_duplicate_id(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0001: ID 重複", problems)

    def test_detail_field_duplicate(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0001: 詳情欄位「命題」重複", problems)

    def test_resurrect_without_last_date(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0002: 復活 1 次但「最近復活」欄空白", problems)

    def test_tier_out_of_range(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0003: 層級「3」超出 0–2", problems)

    def test_detail_missing_history_section(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0004: 詳情缺「## 沿革」內容", problems)

    def test_hard_zone_entry_missing_from_active(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0099: 出現在硬禁區但 B 段沒有這條", problems)

    def test_archived_date_format(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0010: 歸檔日期「2026/09/01」不是 YYYY-MM-DD", problems)

    def test_orphan_detail_file(self):
        problems = "\n".join(lc.lint(FX / "bad_lint2"))
        self.assertIn("R-0077: 詳情檔存在但索引沒有這條", problems)


class TierPositiveTests(unittest.TestCase):
    """正向案例：⚠ 存在且層級 ≥1、層級 2 且在硬禁區 → 無層級一致性問題。"""

    def test_warned_and_hard_zone_consistent_rows_pass_clean(self):
        self.assertEqual(lc.lint(FX / "tier_ok"), [])


class DynamicLintTests(unittest.TestCase):
    """B 段超過 50 行、硬禁區超過 5 條的提醒 — 用 tempdir 動態產生，避免大型靜態 fixture。"""

    @staticmethod
    def _write_index(root: Path, body: str) -> None:
        index_path = root / lc.INDEX_REL
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(body, encoding="utf-8")

    def test_active_soft_limit_reminder(self):
        rows = "\n".join(
            f"| R-{i:04d} | 打掉 | 命題{i} | 關鍵字{i} | 0 | 0 | |" for i in range(1, 52)
        )
        index_md = f"""# 裁決帳本（索引）

## 0. 硬禁區

## A. 現在算數的檔案（白名單）

| 工作線 | 當前有效檔 | 說明 |
|---|---|---|

### 已淘汰檔（不得作事實來源）

| 檔 | 原因 |
|---|---|

## B. 有效裁決

| ID | 狀態 | 命題 | 關鍵字 | 層級 | 復活 | 最近復活 |
|---|---|---|---|---|---|---|
{rows}

## C. 已歸檔

| ID | 狀態 | 命題 | 歸檔日期 |
|---|---|---|---|
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_index(root, index_md)
            problems = "\n".join(lc.lint(root))
        self.assertIn("提醒：B 段有效裁決 51 行，超過 50，請歸檔（不擋）", problems)

    def test_hard_zone_limit_reminder(self):
        hard_entries = "\n\n".join(
            f"### R-{i:04d} 測試硬禁區{i}\n\n- 命題：測試\n- 範圍：測試\n- 重審條件：測試"
            for i in range(1, 7)
        )
        index_md = f"""# 裁決帳本（索引）

## 0. 硬禁區

{hard_entries}

## A. 現在算數的檔案（白名單）

| 工作線 | 當前有效檔 | 說明 |
|---|---|---|

### 已淘汰檔（不得作事實來源）

| 檔 | 原因 |
|---|---|

## B. 有效裁決

| ID | 狀態 | 命題 | 關鍵字 | 層級 | 復活 | 最近復活 |
|---|---|---|---|---|---|---|

## C. 已歸檔

| ID | 狀態 | 命題 | 歸檔日期 |
|---|---|---|---|
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_index(root, index_md)
            problems = "\n".join(lc.lint(root))
        self.assertIn("提醒：硬禁區有 6 條，超過上限 5，請先降舊的（不擋）", problems)


class PathsTests(unittest.TestCase):
    def test_ok_paths_all_exist(self):
        self.assertEqual(lc.check_paths(FX / "ok"), [])

    def test_bad_paths_reports_all_three(self):
        msgs = "\n".join(lc.check_paths(FX / "bad_paths"))
        self.assertIn("草稿/不存在.md: 檔案不存在", msgs)
        self.assertIn("不得使用絕對路徑", msgs)
        self.assertIn("不得含 ..", msgs)

    def test_paths_reports_repo_escape_via_mocked_resolver(self):
        with patch("ledger_check._resolved_inside", return_value=(Path("D:/outside/x.md"), False)):
            msgs = "\n".join(lc.check_paths(FX / "ok"))
        self.assertIn("解析後在 repo 之外", msgs)


class ResolvedInsideTests(unittest.TestCase):
    def test_path_resolving_outside_repo_root_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            outside = root / "outside"
            repo.mkdir()
            outside.mkdir()
            target, inside = lc._resolved_inside(repo, "../outside/x.md")
            self.assertFalse(inside)
            self.assertEqual(target, (outside / "x.md").resolve())


def _make_pptx(path: Path, sentence: str) -> None:
    """用標準庫產一個最小 .pptx：只有 [Content_Types].xml 與一張投影片。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    slide = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<p:cSld><p:spTree><a:t>{sentence}</a:t></p:spTree></p:cSld></p:sld>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("ppt/slides/slide1.xml", slide)


def _make_pptx_slides(path: Path, slide_at_tags: list) -> None:
    """比照 _make_pptx，但一次可放多張投影片，且呼叫端自己決定 <a:t ...> 標籤的寫法
    （用來覆蓋 fix round 1 指出的「PowerPoint 常輸出帶屬性的 <a:t xml:space="preserve">」個案）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        for i, at_xml in enumerate(slide_at_tags, 1):
            slide = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f'<p:cSld><p:spTree>{at_xml}</p:spTree></p:cSld></p:sld>'
            )
            z.writestr(f"ppt/slides/slide{i}.xml", slide)


def _make_docx(path: Path, paragraphs: list) -> None:
    """用標準庫產一個最小 .docx：只有 [Content_Types].xml 與 word/document.xml，
    一個字串一個 <w:p>；空字串會產生內容為空的 <w:t></w:t>，段落本身仍會被 W_P_RE 抓到
    （驗證「行號＝段落序號」，即使中間有空段落也不跳號）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("word/document.xml", document)


class ResurrectTests(unittest.TestCase):
    def setUp(self):
        self.hits = lc.resurrect_scan(FX / "resurrect", [])

    def test_every_hit_is_recorded(self):
        self.assertEqual(len(self.hits), 8)

    def test_hints_by_line(self):
        by_line = {}
        for h in self.hits:
            by_line.setdefault(h.line_no, []).append(h.hint)
        self.assertEqual(by_line[3], ["否定"])
        self.assertEqual(by_line[4], ["疑似"])
        self.assertEqual(by_line[5], ["引用"])
        self.assertEqual(by_line[6], ["否定"])

    def test_far_negation_stays_suspect(self):
        line7 = [h for h in self.hits if h.line_no == 7]
        self.assertEqual([h.hint for h in line7], ["疑似"])

    def test_reference_with_rid_is_still_listed(self):
        line8 = [h for h in self.hits if h.line_no == 8]
        self.assertEqual([h.hint for h in line8], ["引用"])
        self.assertEqual(line8[0].term, "年約")

    def test_multiple_terms_on_one_line(self):
        line9 = sorted(h.term for h in self.hits if h.line_no == 9)
        # brief 原斷言為 ["年約", "年度框架合約"]，但 Python 字串排序依 code point，
        # "度"（U+5EA6）< "約"（U+7D04），故「年度框架合約」排在「年約」之前；
        # 已依 fixture 內容＋實際排序結果修正（task-4-report.md 有記錄）。
        self.assertEqual(line9, ["年度框架合約", "年約"])


class PptxTests(unittest.TestCase):
    def test_pptx_slide_text_is_scanned(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp / "repo"
            shutil.copytree(FX / "resurrect", repo)
            _make_pptx(repo / "草稿" / "簡報.pptx", "第一頁主張推動年度框架合約")
            index = repo / "docs" / "裁決帳本.md"
            text = index.read_text(encoding="utf-8").replace(
                "| 報告 | 草稿/報告.md | 上場版 |",
                "| 報告 | 草稿/報告.md | 上場版 |\n| 簡報 | 草稿/簡報.pptx | 上場版 |",
            )
            index.write_text(text, encoding="utf-8")
            hits = [h for h in lc.resurrect_scan(repo, []) if h.file.endswith("簡報.pptx")]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].line_no, 1)
            self.assertEqual(hits[0].term, "年度框架合約")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_attributed_a_t_tag_is_scanned(self):
        """fix round 1 [Important]：PowerPoint 常輸出 <a:t xml:space="preserve">…</a:t>，
        修前的 A_T_RE = r"<a:t>(.*?)</a:t>" 只認裸標籤，會整段靜默漏檢。"""
        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp / "repo"
            shutil.copytree(FX / "resurrect", repo)
            path = repo / "草稿" / "簡報2.pptx"
            _make_pptx_slides(path, [
                '<a:t>與本案無關的第一頁</a:t>',
                '<a:t xml:space="preserve">推動年度框架合約</a:t>',
            ])
            index = repo / "docs" / "裁決帳本.md"
            text = index.read_text(encoding="utf-8").replace(
                "| 報告 | 草稿/報告.md | 上場版 |",
                "| 報告 | 草稿/報告.md | 上場版 |\n| 簡報2 | 草稿/簡報2.pptx | 上場版 |",
            )
            index.write_text(text, encoding="utf-8")
            hits = [h for h in lc.resurrect_scan(repo, []) if h.file.endswith("簡報2.pptx")]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].line_no, 2)
            self.assertEqual(hits[0].term, "年度框架合約")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DocxTests(unittest.TestCase):
    """fix round 1 [Important]：.docx 掃描路徑先前完全沒有測試覆蓋。"""

    def test_docx_paragraph_text_is_scanned(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp / "repo"
            shutil.copytree(FX / "resurrect", repo)
            _make_docx(repo / "草稿" / "報告2.docx", [
                "與本案無關的段落。",
                "",
                "本案建議推動年度框架合約。",
            ])
            index = repo / "docs" / "裁決帳本.md"
            text = index.read_text(encoding="utf-8").replace(
                "| 報告 | 草稿/報告.md | 上場版 |",
                "| 報告 | 草稿/報告.md | 上場版 |\n| 報告二 | 草稿/報告2.docx | 上場版 |",
            )
            index.write_text(text, encoding="utf-8")
            hits = [h for h in lc.resurrect_scan(repo, []) if h.file.endswith("報告2.docx")]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].line_no, 3)
            self.assertEqual(hits[0].term, "年度框架合約")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DocxSelfClosingParagraphTests(unittest.TestCase):
    """fix round 2（6a）：自我封閉 <w:p/>／<w:p w:rsidR="..."/> 先前不被 W_P_RE 匹配，
    導致段落序號整體位移，命中回報的行號會錯。"""

    def test_self_closing_paragraphs_do_not_shift_line_numbers(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp / "repo"
            shutil.copytree(FX / "resurrect", repo)
            path = repo / "草稿" / "報告3.docx"
            path.parent.mkdir(parents=True, exist_ok=True)
            content_types = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '</Types>'
            )
            # 段落序：1=文字、2=自我封閉空段、3=命中、4=帶屬性的自我封閉空段、5=另一命中
            body = (
                '<w:p><w:r><w:t>與本案無關的段落。</w:t></w:r></w:p>'
                '<w:p/>'
                '<w:p><w:r><w:t>本案建議推動年度框架合約。</w:t></w:r></w:p>'
                '<w:p w:rsidR="00AB12CD"/>'
                '<w:p><w:r><w:t>另提一次年約規劃。</w:t></w:r></w:p>'
            )
            document = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{body}</w:body></w:document>'
            )
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("[Content_Types].xml", content_types)
                z.writestr("word/document.xml", document)
            index = repo / "docs" / "裁決帳本.md"
            text = index.read_text(encoding="utf-8").replace(
                "| 報告 | 草稿/報告.md | 上場版 |",
                "| 報告 | 草稿/報告.md | 上場版 |\n| 報告三 | 草稿/報告3.docx | 上場版 |",
            )
            index.write_text(text, encoding="utf-8")
            hits = [h for h in lc.resurrect_scan(repo, []) if h.file.endswith("報告3.docx")]
            lines = {h.line_no: h.term for h in hits}
            self.assertIn(3, lines)
            self.assertEqual(lines[3], "年度框架合約")
            self.assertIn(5, lines)
            self.assertEqual(lines[5], "年約")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TierNonNumericTests(unittest.TestCase):
    """fix round 2（6c）：層級欄非數字時，先前會多報一條洩漏內部哨兵 -1 的
    「層級「-1」超出 0–2」訊息；修後只報一條「不是數字」，且不再出現 -1。"""

    def test_non_numeric_tier_reports_once_without_leaking_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(FX / "ok", repo)
            index = repo / "docs" / "裁決帳本.md"
            text = index.read_text(encoding="utf-8")
            self.assertIn("| R-0001 | 打掉 |", text)
            lines = text.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith("| R-0001 |"):
                    cells = line.split("|")
                    # cells: ['', ' R-0001 ', ' 打掉 ', ..., ' 0 ', ' 0 ', ' ', '']
                    cells[5] = " 高 "
                    line = "|".join(cells)
                new_lines.append(line)
            index.write_text("\n".join(new_lines), encoding="utf-8")
            problems = lc.lint(repo)
            joined = "\n".join(problems)
            self.assertIn("不是數字", joined)
            self.assertNotIn("-1", joined)
            self.assertEqual(lc.main([str(repo)]), 1)


class DisplayPathTests(unittest.TestCase):
    """fix round 1 [Minor]：--scan / extra_targets 指到 repo 外的絕對路徑時，
    _display_path 的 relative_to 例外 fallback 先前沒有自動化測試。"""

    def test_scan_target_outside_repo_uses_absolute_path_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "外部.md"
            outside.write_text("外部文件提到推動年度框架合約。", encoding="utf-8")
            hits = lc.resurrect_scan(FX / "resurrect", [outside])
            match = [h for h in hits if h.term == "年度框架合約" and h.file == outside.as_posix()]
            self.assertEqual(len(match), 1)

    def test_display_path_uses_forward_slashes_not_backslashes(self):
        """fix round 2（6d）：Windows 上 relative_to 會回傳含反斜線的路徑，
        與帳本白名單一律用正斜線的寫法不一致，此處鎖定一律用 as_posix()。"""
        hits = lc.resurrect_scan(FX / "resurrect", [])
        report_hits = [h for h in hits if h.file.endswith("報告.md")]
        self.assertTrue(report_hits)
        for h in report_hits:
            self.assertIn("/", h.file)
            self.assertNotIn("\\", h.file)


class ExitCodeTests(unittest.TestCase):
    def test_any_hit_returns_two(self):
        self.assertEqual(lc.main([str(FX / "resurrect"), "--resurrect"]), 2)

    def test_clean_repo_returns_zero(self):
        self.assertEqual(lc.main([str(FX / "ok")]), 0)


class ScanHardeningTests(unittest.TestCase):
    """review 2026-09-07 追加：白名單越界不掃、壞檔不中斷。"""

    def _repo_with_whitelist(self, tmp: Path, whitelist_path: str) -> Path:
        repo = tmp / "repo"
        shutil.copytree(FX / "resurrect", repo)
        index = repo / "docs" / "裁決帳本.md"
        text = index.read_text(encoding="utf-8").replace("草稿/報告.md", whitelist_path)
        index.write_text(text, encoding="utf-8")
        return repo

    def test_whitelist_traversal_is_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            outside = tmp / "outside.md"
            outside.write_text("這裡在講年約框架合約\n", encoding="utf-8")
            repo = self._repo_with_whitelist(tmp, "../outside.md")
            hits = lc.resurrect_scan(repo, [])
            self.assertEqual(hits, [])

    def test_unreadable_file_reported_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            shutil.copytree(FX / "resurrect", repo)
            (repo / "草稿" / "壞檔.md").write_bytes(b"\xff\xfe\x00bad")
            (repo / "草稿" / "壞簡報.pptx").write_bytes(b"not a zip")
            hits = lc.resurrect_scan(repo, [Path("草稿")])
            failed = sorted(h.file for h in hits if h.term == "讀取失敗")
            self.assertEqual(failed, sorted(["草稿/壞簡報.pptx", "草稿/壞檔.md"]))
            self.assertTrue(any(h.term == "年約" for h in hits))


class DirectoryWhitelistTests(unittest.TestCase):
    """白名單列可填目錄或 `.`；遞迴掃描並略過 SKIP_DIRS。"""

    def _repo(self, tmp: Path, whitelist_path: str) -> Path:
        repo = tmp / "repo"
        shutil.copytree(FX / "resurrect", repo)
        index = repo / "docs" / "裁決帳本.md"
        index.write_text(index.read_text(encoding="utf-8").replace("草稿/報告.md", whitelist_path), encoding="utf-8")
        (repo / "草稿" / "子目錄").mkdir()
        (repo / "草稿" / "子目錄" / "深層.md").write_text("深層也談年約\n", encoding="utf-8")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "套件.md").write_text("套件裡的年約不該被掃\n", encoding="utf-8")
        return repo

    def test_directory_row_scans_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp), "草稿")
            files = {h.file for h in lc.resurrect_scan(repo, [])}
            self.assertIn("草稿/報告.md", files)
            self.assertIn("草稿/子目錄/深層.md", files)
            self.assertEqual(lc.check_paths(repo), [])

    def test_dot_row_scans_whole_repo_and_skips_generated_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp), ".")
            files = {h.file for h in lc.resurrect_scan(repo, [])}
            self.assertIn("草稿/子目錄/深層.md", files)
            self.assertFalse(any(f.startswith("node_modules") for f in files))
            self.assertFalse(any(f.startswith("docs/裁決") for f in files))
            self.assertEqual(lc.check_paths(repo), [])


if __name__ == "__main__":
    unittest.main()
