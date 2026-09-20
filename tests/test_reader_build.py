"""阅读器构建工具的回归测试：确保正文改动后网页可重建、且不会泄露内部资料。"""

import tempfile
import unittest
from pathlib import Path

from scripts import reader_build


CHAPTER_TEMPLATE = """# {title}

{body}
"""

LONG_BODY = "这是一段足够长的正文。" * 150


class ProseRenderingTests(unittest.TestCase):
    def test_paragraphs_quotes_and_breaks(self):
        body = "第一段。\n\n第二段。\n\n> 记录一\n> 记录二\n\n---\n\n结尾。"
        prose = reader_build.render_prose(body)
        self.assertEqual(prose.count("<p>"), 4)
        self.assertIn("<blockquote>", prose)
        self.assertIn("记录一<br>记录二", prose)
        self.assertIn('class="scene-break"', prose)
        self.assertEqual(prose.count("<blockquote>"), prose.count("</blockquote>"))

    def test_quote_blank_line_splits_paragraphs(self):
        prose = reader_build.render_prose("> 甲\n>\n> 乙")
        self.assertEqual(prose.count("<p>"), 2)

    def test_markup_is_escaped(self):
        prose = reader_build.render_prose("他说：<script>alert(1)</script> 还有 **强调**。")
        self.assertNotIn("<script>", prose)
        self.assertIn("&lt;script&gt;", prose)
        self.assertIn("<strong>强调</strong>", prose)

    def test_prose_chars_ignores_markup_and_whitespace(self):
        self.assertEqual(reader_build.prose_chars("# 标题\n\n> 甲\n\n乙"), 4)


class SourceLoadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "正文"

    def tearDown(self):
        self._tmp.cleanup()

    def write_chapter(self, volume, filename, title, body=LONG_BODY):
        folder = self.src / volume
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(CHAPTER_TEMPLATE.format(title=title, body=body), encoding="utf-8")

    def test_chapters_are_ordered_by_volume_then_number(self):
        self.write_chapter("卷02_第二卷", "第003章_丙.md", "第三章　丙")
        self.write_chapter("卷01_第一卷", "第001章_甲.md", "第一章　甲")
        self.write_chapter("卷01_第一卷", "第002章_乙.md", "第二章　乙")
        chapters = reader_build.collect_chapters(self.src)
        self.assertEqual([chapter["title"] for chapter in chapters], ["第一章　甲", "第二章　乙", "第三章　丙"])
        self.assertEqual([chapter["volume_label"] for chapter in chapters], ["卷01", "卷01", "卷02"])

    def test_numbering_gap_is_rejected(self):
        self.write_chapter("卷01_第一卷", "第001章_甲.md", "第一章　甲")
        self.write_chapter("卷01_第一卷", "第003章_丙.md", "第三章　丙")
        with self.assertRaises(reader_build.BuildError):
            reader_build.collect_chapters(self.src)

    def test_duplicate_number_is_rejected(self):
        self.write_chapter("卷01_第一卷", "第001章_甲.md", "第一章　甲")
        self.write_chapter("卷02_第二卷", "第001章_乙.md", "第一章　乙")
        with self.assertRaises(reader_build.BuildError):
            reader_build.collect_chapters(self.src)

    def test_short_chapter_is_rejected(self):
        self.write_chapter("卷01_第一卷", "第001章_甲.md", "第一章　甲", body="太短了。")
        with self.assertRaises(reader_build.BuildError):
            reader_build.collect_chapters(self.src)

    def test_missing_heading_is_rejected(self):
        folder = self.src / "卷01_第一卷"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "第001章_甲.md").write_text("没有标题行\n\n" + LONG_BODY, encoding="utf-8")
        with self.assertRaises(reader_build.BuildError):
            reader_build.collect_chapters(self.src)


class SiteRenderingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "正文"
        folder = self.src / "卷01_第一卷"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "第001章_甲.md").write_text("# 第一章　甲\n\n" + LONG_BODY, encoding="utf-8")
        (folder / "第002章_乙.md").write_text("# 第二章　乙\n\n" + LONG_BODY, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def render(self):
        return reader_build.render_site(self.src, reader_build.DEFAULT_TEMPLATE, reader_build.DEFAULT_CONFIG)

    def test_site_structure_matches_sources(self):
        site, chapters = self.render()
        reader_build.validate_site(site, chapters)
        self.assertEqual(site.count('class="chapter active"'), 1)
        self.assertEqual(site.count('class="chapter-button"'), 2)
        self.assertIn("第二章　乙", site)
        self.assertEqual(site.count("<!--"), 0)

    def test_sidebar_footer_reports_counts(self):
        site, _ = self.render()
        self.assertIn("已更新 2 章", site)

    def test_validate_site_detects_tampering(self):
        site, chapters = self.render()
        broken = site.replace('<section class="chapter" data-title="第二章　乙">', "")
        with self.assertRaises(reader_build.BuildError):
            reader_build.validate_site(broken, chapters)


class RepositoryGuardTests(unittest.TestCase):
    def test_private_paths_are_gitignored(self):
        gitignore = (reader_build.ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in reader_build.PRIVATE_PATHS:
            self.assertIn(name, gitignore)

    def test_private_paths_are_untracked(self):
        reader_build.check_private_paths_untracked(reader_build.ROOT)

    def test_published_site_matches_current_sources(self):
        out = reader_build.DEFAULT_OUT
        if not out.exists():
            self.skipTest("尚未生成 dist/index.html")
        site, chapters = reader_build.render_site(
            reader_build.DEFAULT_SRC, reader_build.DEFAULT_TEMPLATE, reader_build.DEFAULT_CONFIG
        )
        reader_build.validate_site(site, chapters)
        self.assertEqual(out.read_text(encoding="utf-8"), site, "正文已改动但没重建网页，请运行 build")


if __name__ == "__main__":
    unittest.main()
