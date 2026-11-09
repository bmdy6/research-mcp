"""ldjson 抽取的单测：标准 JSON-LD 与 Next.js 流式载荷两条路径。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_mcp.tools import ldjson

CHART_TEXT = "Title: x\n\nMarkdown Content:\nHigher is better\nLower is better\nLog scale\n"

class ChartPageTest(unittest.TestCase):
    def test_detects_chart_page(self):
        self.assertTrue(ldjson.looks_like_chart_page(CHART_TEXT))
    def test_normal_article_is_not_chart_page(self):
        self.assertFalse(ldjson.looks_like_chart_page("Title: x\n\nMarkdown Content:\n这是正常文章正文，讲了一件事。\n"))
    def test_long_article_mentioning_annotations_is_not_chart_page(self):
        """回归：正常评测/定价文章也会多次写 “higher is better”，不能当图表页。

        只看图注条数会误伤这类文章，进而白跑一轮抽数；以前还会直接报失败。
        """
        article = (
            "Title: 评测说明\n\nMarkdown Content:\n"
            + "这一项 higher is better，分数越高越好。\n" * 3
            + "\n".join(f"第 {i} 段正常正文，讲的是这些任务上的表现和取舍。" for i in range(30))
        )
        self.assertFalse(ldjson.looks_like_chart_page(article))

class LdjsonTest(unittest.TestCase):
    def test_standard_dataset(self):
        raw = '<script type="application/ld+json">{"@type":"Dataset","name":"Speed","data":[{"label":"A","value":1.5}]}</script>'
        ds = ldjson.extract_chart_datasets(raw)
        self.assertEqual(ds[0][0], "Speed")
        self.assertEqual(ds[0][1][0]["values"]["value"], 1.5)
    def test_ignores_blocks_without_data(self):
        raw = '<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>'
        self.assertEqual(ldjson.extract_chart_datasets(raw), [])
    def test_flight_payload_with_title(self):
        # 标题在数组之前（真实页面的顺序），数组后跟下一个字段
        raw = ('<script>self.__next_f.push([1,"\\"children\\":\\"Cost per Task\\"}]}],'
               '\\"data\\":[{\\"id\\":\\"a\\",\\"label\\":\\"Grok 4.6\\",'
               '\\"value\\":1.85,\\"displayValue\\":\\"$$1.86\\"}],\\"focusIds\\":\\"$x\\"}"])</script>')
        ds = ldjson.extract_chart_datasets(raw)
        self.assertEqual(ds[0][0], "Cost per Task")
        self.assertEqual(ds[0][1][0]["values"]["value"], "$$1.86")
    def test_tolerates_trailing_comma(self):
        raw = '<script type="application/ld+json">{"name":"A","data":[{"label":"x","v":1},]}</script>'
        self.assertEqual(ldjson.extract_chart_datasets(raw)[0][1][0]["label"], "x")
    def test_render_handles_nested_values(self):
        md = ldjson.render_chart_datasets([("Elo", [{"label": "A", "values": {"elo": {"mid": 1657.2}}}])])
        self.assertIn("mid=1657.2", md)

if __name__ == "__main__":
    unittest.main()
