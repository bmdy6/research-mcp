import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_mcp.rerank import (
    _describe_candidate,
    filter_candidates,
    get_ai_gateway_key,
    judge_candidates,
)


class RerankTest(unittest.TestCase):
    def test_get_ai_gateway_key_from_env(self):
        with patch.dict("os.environ", {"AI_GATEWAY_API_KEY": "test-key-123"}):
            self.assertEqual(get_ai_gateway_key(), "test-key-123")



    def test_describe_candidate_includes_index_source_and_title(self):
        line = _describe_candidate(3, {"source": "exa", "title": "标题", "summary": "摘要内容"})
        self.assertTrue(line.startswith("3. [exa] 标题"))
        self.assertIn("摘要内容", line)

    @patch("research_mcp.rerank._http_json")
    def test_judge_candidates_sends_one_request_when_fits_one_batch(self, mock_http):
        mock_http.return_value = {
            "answers": {f"keep_{i}": {"type": "boolean", "probability": 0.8} for i in range(1, 4)}
        }
        items = [{"title": f"item-{i}", "summary": "s"} for i in range(3)]
        probs = judge_candidates("query", items, "fake-key")
        self.assertEqual(probs, [0.8, 0.8, 0.8])
        # 放得下一批时只发一次请求：官方管这个叫 speculative fan-out
        mock_http.assert_called_once()
        questions = mock_http.call_args.kwargs["payload"]["questions"]
        self.assertEqual(sorted(questions), ["keep_1", "keep_2", "keep_3"])

    @patch("research_mcp.rerank._http_json")
    def test_judge_candidates_splits_into_batches(self, mock_http):
        # 官方 jaggedness：state 里无关内容越多准确率越差，所以要分批
        mock_http.side_effect = lambda *a, **kw: {
            "answers": {
                f"keep_{i}": {"type": "boolean", "probability": 0.5}
                for i in range(1, len(kw["payload"]["questions"]) + 1)
            }
        }
        items = [{"title": f"item-{i}"} for i in range(25)]
        probs = judge_candidates("query", items, "fake-key", batch_size=12)
        self.assertEqual(len(probs), 25)
        self.assertEqual(mock_http.call_count, 3)
        sizes = [len(c.kwargs["payload"]["questions"]) for c in mock_http.call_args_list]
        self.assertEqual(sorted(sizes), [1, 12, 12])

    @patch("research_mcp.rerank._http_json")
    def test_judge_candidates_returns_none_if_any_batch_fails(self, mock_http):
        # 部分成功不能当完整结果用：宁可整体降级
        mock_http.side_effect = [
            {"answers": {"keep_1": {"probability": 0.9}}},
            RuntimeError("network error"),
        ]
        items = [{"title": f"item-{i}"} for i in range(4)]
        self.assertIsNone(judge_candidates("query", items, "fake-key", batch_size=2))

    @patch("research_mcp.rerank._http_json")
    def test_judge_candidates_failure_returns_none(self, mock_http):
        mock_http.side_effect = RuntimeError("network error")
        self.assertIsNone(judge_candidates("q", [{"title": "t"}], "fake-key"))

    @patch("research_mcp.rerank._http_json")
    def test_judge_candidates_missing_answer_returns_none(self, mock_http):
        mock_http.return_value = {"answers": {"keep_1": {"probability": 0.9}}}
        items = [{"title": "a"}, {"title": "b"}]
        self.assertIsNone(judge_candidates("q", items, "fake-key"))

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="")
    def test_filter_candidates_fallback_when_no_key(self, mock_key):
        candidates = [{"title": f"item-{i}"} for i in range(5)]
        out, reason = filter_candidates("test", candidates, limit=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["title"], "item-0")
        self.assertEqual(reason, "skipped_no_key")

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=[0.1, 0.9, 0.2])
    def test_filter_candidates_drops_rejected_and_keeps_probability(self, mock_judge, mock_key):
        candidates = [
            {"title": "noise-item"},
            {"title": "good-item"},
            {"title": "weak-item"},
        ]
        out, reason = filter_candidates("query", candidates, limit=10)
        self.assertIsNone(reason)
        # 只留 Jev 判定值得保留的，不是把全部条目附上分数返回
        self.assertEqual([x["title"] for x in out], ["good-item"])
        self.assertEqual(out[0]["jev_keep"], 0.9)

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=[0.4, 0.3])
    def test_filter_candidates_default_threshold_is_calibrated(self, mock_judge, mock_key):
        # 0.35 是本地标注数据标定出来的，不是拍脑袋的 0.5；0.4 应过、0.3 应不过
        candidates = [{"title": "kept"}, {"title": "dropped"}]
        out, reason = filter_candidates("query", candidates, limit=10)
        self.assertIsNone(reason)
        self.assertEqual([x["title"] for x in out], ["kept"])

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=[0.1, 0.2])
    def test_filter_candidates_threshold_is_overridable(self, mock_judge, mock_key):
        # 样本要能区分“参数生效”和“参数失效”：
        #   参数生效（0.45）→ 只留 0.5 那条
        #   参数失效退回默认 0.35 → 两条都不到，走全判否兜底返回概率最高的 1 条
        # 只断言条数区分不出来（两种路径都返回 1 条），所以同时断言留下的是哪条：
        # 参数失效时兜底会返回 0.5 那条，与这里相同……因此再加一个概率更低的样本组合。
        candidates = [{"title": "edge"}, {"title": "weak"}]
        with patch("research_mcp.rerank.judge_candidates", return_value=[0.5, 0.2]):
            out, reason = filter_candidates("query", candidates, limit=10, min_keep_prob=0.45)
        self.assertIsNone(reason)
        self.assertEqual([x["title"] for x in out], ["edge"])

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    def test_threshold_override_lower_than_default_keeps_more(self, mock_key):
        # 比默认 0.35 更宽的阈值，应该留下默认会剔掉的条目。
        # 若 min_keep_prob 被忽略、退回默认 0.35，则 0.2 那条会被剔除，测试失败。
        candidates = [{"title": "high"}, {"title": "marginal"}]
        with patch("research_mcp.rerank.judge_candidates", return_value=[0.8, 0.2]):
            out, reason = filter_candidates("query", candidates, limit=10, min_keep_prob=0.1)
        self.assertIsNone(reason)
        self.assertEqual([x["title"] for x in out], ["high", "marginal"])

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    def test_threshold_override_higher_than_default_keeps_less(self, mock_key):
        # 比默认 0.35 更严的阈值，应该更进一步剔除。
        # 若 min_keep_prob 被忽略、退回默认 0.35，则两条都会留下，测试失败。
        candidates = [{"title": "high"}, {"title": "mid"}]
        with patch("research_mcp.rerank.judge_candidates", return_value=[0.8, 0.4]):
            out, reason = filter_candidates("query", candidates, limit=10, min_keep_prob=0.6)
        self.assertIsNone(reason)
        self.assertEqual([x["title"] for x in out], ["high"])

    def test_current_default_matches_eval_script_expectation(self):
        """标定脚本会读这个默认值打标记，它应该就是标定出来的 0.35。"""
        import inspect

        default = inspect.signature(filter_candidates).parameters["min_keep_prob"].default
        self.assertAlmostEqual(default, 0.35)

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=[0.9, 0.7, 0.8])
    def test_filter_candidates_sorts_by_probability(self, mock_judge, mock_key):
        candidates = [{"title": "b"}, {"title": "c"}, {"title": "a"}]
        out, reason = filter_candidates("query", candidates, limit=10)
        self.assertEqual([x["title"] for x in out], ["b", "a", "c"])
        self.assertIsNone(reason)

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=[0.1, 0.2])
    def test_filter_candidates_all_rejected_keeps_best(self, mock_judge, mock_key):
        candidates = [{"title": "a"}, {"title": "b"}]
        out, reason = filter_candidates("query", candidates, limit=2)
        # 全判否也不能返回空结果
        self.assertEqual(len(out), 2)
        self.assertIsNone(reason)

    @patch("research_mcp.rerank.get_ai_gateway_key", return_value="fake-key")
    @patch("research_mcp.rerank.judge_candidates", return_value=None)
    def test_filter_candidates_reports_all_failed(self, mock_judge, mock_key):
        candidates = [{"title": f"item-{i}"} for i in range(3)]
        out, reason = filter_candidates("query", candidates, limit=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(reason, "rerank_all_failed")

    def test_filter_candidates_empty_input(self):
        with patch("research_mcp.rerank.get_ai_gateway_key", return_value=""):
            self.assertEqual(filter_candidates("q", [], limit=5), ([], "skipped_no_key"))


if __name__ == "__main__":
    unittest.main()
