"""渠道层单测：解析、清洗、门槛过滤。全部离线，不真调上游。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_mcp.channels import bilibili, exa, juejin

EXA_SAMPLE = """Title: openai/openai-agents-python
URL: https://github.com/openai/openai-agents-python
Published: 2025-03-11T03:42:36.000Z
Author: openai
Highlights:
轻量多 agent 框架
---
Title: ag2ai/ag2
URL: https://github.com/ag2ai/ag2
Published: N/A
Author: N/A
Highlights:
AutoGen 后继项目
"""


class BilibiliChannelTest(unittest.TestCase):
    def test_to_item_cleans_highlight_and_builds_url(self):
        item = bilibili._to_item({
            "bvid": "BV1xx", "title": '<em class="keyword">英语</em>时态', "description": "讲得清楚",
            "author": "某UP", "play": 1234, "pubdate": 1597809096,
        })
        self.assertEqual(item["title"], "英语时态")
        self.assertEqual(item["url"], "https://www.bilibili.com/video/BV1xx")
        self.assertEqual(item["source"], "bilibili")
        self.assertTrue(item["published_at"].startswith("2020-08-"))

    def test_to_item_drops_rows_without_bvid(self):
        self.assertIsNone(bilibili._to_item({"title": "无 bvid 的脏条目"}))

    def test_search_filters_by_min_play_and_dedupes(self):
        rows = [
            {"bvid": "BV1", "title": "a", "play": 100, "pubdate": 1700000000},
            {"bvid": "BV2", "title": "b", "play": 10, "pubdate": 1700000000},
            {"bvid": "BV1", "title": "重复", "play": 100, "pubdate": 1700000000},
        ]
        with patch.object(bilibili, "_search_api", return_value=rows), \
                patch.object(bilibili, "_buvid3", return_value=""):
            items = bilibili.search({"bilibili_queries": ["x"], "bilibili_min_play": 50})
        self.assertEqual([i["url"] for i in items], ["https://www.bilibili.com/video/BV1"])

    def test_search_returns_empty_without_queries(self):
        self.assertEqual(bilibili.search({}), [])


class ExaChannelTest(unittest.TestCase):
    def test_parse_splits_blocks_and_normalizes_na(self):
        items = exa._parse(EXA_SAMPLE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://github.com/openai/openai-agents-python")
        self.assertEqual(items[0]["author"], "openai")
        self.assertIsNone(items[1]["published_at"])
        self.assertEqual(items[1]["author"], "")
        self.assertIn("AutoGen", items[1]["summary"])

    def test_parse_ignores_blocks_without_title_or_url(self):
        self.assertEqual(exa._parse("Highlights:\n只有片段\n---\nTitle: 只有标题\n"), [])

    def test_search_returns_empty_without_query(self):
        self.assertEqual(exa.search({}), [])

    def test_search_merges_queries_and_dedupes(self):
        with patch.object(exa, "_call_direct", side_effect=[EXA_SAMPLE, EXA_SAMPLE]):
            items = exa.search({"exa_queries": ["a", "b"], "exa_limit": 10})
        self.assertEqual(len(items), 2)  # 两个查询返回同一份结果，按 URL 去重


class ExaRateLimitTest(unittest.TestCase):
    """限流要能识别、退避重试，并且一次限流后短期内不再打。"""

    def setUp(self):
        exa._reset_rate_limit_state()
        self.addCleanup(exa._reset_rate_limit_state)

    def _fake_rpc(self, failures_before_success):
        calls = {"n": 0}
        def fake(method, params, identity, headers, timeout):
            if method == "initialize":
                calls["n"] += 1
                if calls["n"] <= failures_before_success:
                    raise RuntimeError("429 free MCP rate limit")
                return {"protocolVersion": "2024-11-05"}
            if method == "tools/list":
                return {"tools": [{"name": exa.TOOL_NAME}]}
            return {"content": [{"text": EXA_SAMPLE}]}
        return fake, calls

    def test_recognizes_rate_limit_wording(self):
        self.assertTrue(exa._is_rate_limited("429 You've hit Exa's free MCP rate limit."))
        self.assertTrue(exa._is_rate_limited("RESOURCE_EXHAUSTED"))
        self.assertFalse(exa._is_rate_limited("connection reset by peer"))

    def test_retries_after_rate_limit_then_succeeds(self):
        fake, calls = self._fake_rpc(failures_before_success=1)
        with patch.object(exa, "_rpc", side_effect=fake), \
                patch.object(exa.time, "sleep") as sleeper:
            text = exa._call_direct("q", 5, 30)
        self.assertIn("openai-agents-python", text)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(sleeper.call_count, 1)

    def test_gives_up_and_stops_calling_after_persistent_limit(self):
        fake, calls = self._fake_rpc(failures_before_success=99)
        with patch.object(exa, "_rpc", side_effect=fake), \
                patch.object(exa.time, "sleep"):
            with self.assertRaises(RuntimeError) as caught:
                exa._call_direct("q", 5, 30)
        self.assertIn("限流", str(caught.exception))
        self.assertEqual(calls["n"], exa.MAX_ATTEMPTS)
        # 已记住限流状态：紧接着的调用不应再起子进程
        with patch.object(exa, "_rpc", side_effect=fake):
            with self.assertRaises(RuntimeError) as second:
                exa._call_direct("q", 5, 30)
        self.assertIn("跳过", str(second.exception))
        self.assertEqual(calls["n"], exa.MAX_ATTEMPTS)

    def test_non_rate_limit_failure_does_not_retry(self):
        with patch.object(exa, "_rpc", side_effect=RuntimeError("connection reset")) as rpc:
            with self.assertRaises(RuntimeError):
                exa._call_direct("q", 5, 30)
        self.assertEqual(rpc.call_count, 1)

    def test_api_key_switches_endpoint(self):
        with patch.object(exa, "exa_api_key", return_value="abc123"):
            self.assertEqual(exa.endpoint_selector(), f"{exa.FREE_ENDPOINT}?exaApiKey=abc123")
        with patch.object(exa, "exa_api_key", return_value=""):
            self.assertEqual(exa.endpoint_selector(), exa.FREE_ENDPOINT)




if __name__ == "__main__":
    unittest.main()


class JuejinChannelTest(unittest.TestCase):
    def test_to_item_maps_fields(self):
        item = juejin._to_item({"result_model": {
            "article_info": {"article_id": "123", "title": "<em>AI</em> 入门", "brief_content": "简介",
                             "ctime": "1773882915", "view_count": 100},
            "author_user_info": {"user_name": "某人"},
        }})
        self.assertEqual(item["url"], "https://juejin.cn/post/123")
        self.assertEqual(item["title"], "AI 入门")
        self.assertEqual(item["source"], "juejin")
        self.assertTrue(item["published_at"].startswith("20"))

    def test_to_item_drops_rows_without_article_id(self):
        self.assertIsNone(juejin._to_item({"result_model": {"article_info": {"title": "无 id"}}}))

    def test_search_returns_empty_without_queries(self):
        self.assertEqual(juejin.search({}), [])




class ExaHTTPTest(unittest.TestCase):
    def response(self, body, content_type='application/json'):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.headers = {'Content-Type': content_type, 'Mcp-Session-Id': 'synthetic-session'}
        response.status = 200
        response.read.return_value = body.encode()
        response.__enter__.return_value = response
        return response

    def test_rpc_json_and_sse_preserve_response_identity_and_session(self):
        for body, content_type in [('{"jsonrpc":"2.0","id":3,"result":{"content":[]}}', 'application/json'),
                                  ('event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"content":[]}}\n\n', 'text/event-stream')]:
            with self.subTest(content_type=content_type), patch.object(exa.urllib.request, 'urlopen', return_value=self.response(body, content_type)):
                headers = {'User-Agent': 'research-mcp/0.1'}
                self.assertEqual(exa._rpc('tools/call', {}, 3, headers, 20), {'content': []})
                self.assertEqual(headers['Mcp-Session-Id'], 'synthetic-session')

    def test_rpc_errors_are_bounded_and_do_not_echo_credentials(self):
        for body in ['{"id":9,"result":{}}', '{"id":3,"error":{"message":"SECRET-synthetic"}}', '{invalid']:
            with self.subTest(body=body), patch.object(exa.urllib.request, 'urlopen', return_value=self.response(body)):
                with self.assertRaises(RuntimeError) as caught:
                    exa._rpc('tools/call', {}, 3, {}, 20)
                self.assertNotIn('SECRET', str(caught.exception))
