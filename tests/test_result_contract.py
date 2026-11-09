"""Pure synthetic/offline contract tests, including the existing MCP entrypoint."""
import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_mcp import research
from research_mcp.result_contract import ResultBudget, fetch_result, search_result, serialize_result
from research_mcp import service


def payload(items=None, failed=None, succeeded=None):
    items = [] if items is None else items
    return {"query": "合成查询", "candidates": items, "total": len(items), "pooled": len(items), "returned": len(items),
            "truncated": False, "truncated_by": [], "attempted_channels": ["a", "b"],
            "succeeded_channels": ["a", "b"] if succeeded is None else succeeded, "failed_channels": failed or []}


class ResultContractTest(unittest.TestCase):
    def test_empty_partial_and_all_failed_are_distinct(self):
        failed = [{"channel": "a", "error": "synthetic failure"}]
        self.assertEqual(search_result(payload())["status"], "empty")
        partial = search_result(payload(failed=failed, succeeded=["b"]))
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["ok"])
        failed_result = search_result(payload(failed=failed + [{"channel": "b", "error": "failed"}], succeeded=[]))
        self.assertEqual(failed_result["status"], "error")
        self.assertEqual(failed_result["errors"][0]["code"], "ALL_CHANNELS_FAILED")
        no_channels = payload(); no_channels.update(attempted_channels=[], succeeded_channels=[])
        self.assertEqual(search_result(no_channels)["errors"][0]["code"], "NO_QUERY_CHANNELS")

    def test_filtered_search_counts_are_bounded_and_preserved(self):
        raw = payload()
        raw["filtered_by"] = {"outside_time_range": 4}
        result = search_result(raw)
        self.assertEqual(result["filtered_by"], {"outside_time_range": 4})
        self.assertIn("候选截断前排除", "".join(result["display_notes"]))
        for bad in ({"secret": 2}, {"outside_time_range": True}, {"outside_time_range": 0}, []):
            raw["filtered_by"] = bad
            self.assertEqual(search_result(raw)["status"], "error")

    def test_search_counts_and_rerank_semantics_survive_output_clipping(self):
        raw = payload([{"title": "测试🙂" * 5000, "summary": "资料" * 3000, "url": f"https://fixture.invalid/{n}"} for n in range(500)])
        raw.update(total=900, pooled=800, truncated=True, truncated_by=["limit", "relevance"], rerank_downgraded="no_api_key")
        before = copy.deepcopy(raw)
        result = search_result(raw)
        text = serialize_result(result, ResultBudget(max_chars=1800, field_chars=120, max_items=20))
        parsed = json.loads(text)
        self.assertLessEqual(len(text), 1800)
        self.assertEqual(parsed["total"], 900); self.assertEqual(parsed["returned"], 500)
        self.assertEqual(parsed["truncated_by"], ["limit", "relevance"])
        self.assertEqual(parsed["rerank_downgraded"], "no_api_key")
        self.assertEqual(parsed["delivered"], len(parsed["candidates"]))
        self.assertTrue(parsed["output_truncation"]["truncated"])
        self.assertEqual(raw, before)
        self.assertEqual(len(result["candidates"]), 500)

    def test_no_truncation_flag_when_everything_fits(self):
        data = json.loads(serialize_result(search_result(payload([{"title": "短内容", "url": "https://fixture.invalid"}]))))
        self.assertFalse(data["output_truncation"]["truncated"])
        self.assertEqual(data["output_truncation"]["reasons"], {})
        self.assertEqual(data["delivered"], data["returned"])

    def test_read_failure_and_transcript_fields_are_preserved(self):
        result = fetch_result({"kind": "article", "url": "https://fixture.invalid", "ok": False, "error": "timeout", "retryable": True})
        parsed = json.loads(serialize_result(result))
        self.assertFalse(parsed["ok"]); self.assertEqual(parsed["errors"][0]["code"], "READ_FAILED")
        self.assertTrue(parsed["errors"][0]["retryable"])
        result = fetch_result({"kind": "bilibili", "ok": True, "transcript": "离线合成字幕", "bvid": "fixture", "duration_used": 25, "source": "whisper", "partial": True, "failed_segments": [1]})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["transcript"], "离线合成字幕")
        self.assertEqual(result["data"]["duration_used"], 25)

    def test_transcript_fallback_remains_usable_but_is_not_complete_success(self):
        raw = {"kind": "bilibili", "url": "https://fixture.invalid/video",
               "content": "这里只是网页简介", "via": "jina", "transcript_error": "synthetic unavailable"}
        before = copy.deepcopy(raw)
        result = json.loads(serialize_result(fetch_result(raw)))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "TRANSCRIPT_UNAVAILABLE")
        self.assertEqual(result["data"]["content"], raw["content"])
        self.assertEqual(result["data"]["via"], "jina")
        self.assertNotIn("transcript", result["data"])
        self.assertTrue(result["display_notes"])
        self.assertEqual(raw, before)
        failed = fetch_result({**raw, "ok": False, "error": "page unavailable"})
        self.assertEqual(failed["status"], "error")
        self.assertEqual([entry["code"] for entry in failed["errors"]], ["READ_FAILED"])

    def test_long_read_body_remains_valid_unicode_json(self):
        result = fetch_result({"kind": "article", "content": '\\"汉字🙂\n' * 10000, "url": "https://fixture.invalid"})
        text = serialize_result(result, ResultBudget(max_chars=1024))
        parsed = json.loads(text)
        self.assertLessEqual(len(text), 1024)
        self.assertTrue(parsed["output_truncation"]["truncated"])
        self.assertIn("汉字", parsed["data"]["content"])

    def test_malformed_results_are_not_successful_empty(self):
        for raw in (None, [], {}, {"candidates": []}, payload(items=[None])):
            self.assertEqual(search_result(raw)["status"], "error")
        bad_count = payload(); bad_count['returned'] = 2
        self.assertEqual(search_result(bad_count)["status"], "error")
        self.assertFalse(fetch_result({"content": ""})["ok"])
        abnormal = search_result(payload([{"title": "x", "views": float('nan')}]))
        parsed = json.loads(serialize_result(abnormal))
        self.assertEqual(parsed['status'], 'error')
        self.assertIn('invalid_value', parsed['output_truncation']['reasons'])

    def test_failure_metadata_cannot_be_lost_as_empty_success(self):
        raw = payload(failed=[{"channel": str(n), "error": "出错" * 1000} for n in range(500)], succeeded=[])
        raw['attempted_channels'] = [str(n) for n in range(500)]
        parsed = json.loads(serialize_result(search_result(raw), ResultBudget(max_chars=1024)))
        self.assertEqual(parsed['status'], 'error')
        self.assertFalse(parsed['ok']); self.assertEqual(parsed['failed_channels_count'], 500)
        self.assertEqual(parsed['errors'][0]['code'], 'ALL_CHANNELS_FAILED')
        self.assertTrue(parsed['output_truncation']['truncated'])

    def test_search_reports_actual_channel_outcomes_without_live_calls(self):
        def broken(_config): raise RuntimeError('synthetic')
        with patch.object(research, '_default_providers', return_value={'a': lambda _: [], 'b': broken}):
            raw = research.search('fixture', providers=['a', 'b', 'a', 'missing', 'rss'], rerank=False)
        self.assertEqual(raw['attempted_channels'], ['a', 'b', 'missing'])
        self.assertEqual(raw['succeeded_channels'], ['a'])
        self.assertEqual(search_result(raw)['status'], 'partial')
        with patch.object(research, '_default_providers', return_value={'a': lambda _: None}):
            raw = research.search('fixture', providers=['a'], rerank=False)
        self.assertEqual(search_result(raw)['status'], 'error')

    def test_filtered_to_zero_is_not_genuine_empty(self):
        raw = payload(); raw.update(total=2, pooled=2, truncated=True, truncated_by=['relevance'])
        value = search_result(raw)
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(value['returned'], 0)
        self.assertIn('筛选', ''.join(value['display_notes']))

    def test_duplicate_and_invalid_metadata_are_not_silent_success(self):
        for update in ({'attempted_channels': ['a', 'a', 'b']}, {'truncated': 'false'}, {'rerank_downgraded': []}):
            raw = payload(); raw.update(update)
            self.assertFalse(search_result(raw)['ok'])
        for field in ('ok', 'partial', 'retryable'):
            self.assertFalse(fetch_result({field: 'false', 'content': 'fake'})['ok'])
        raw = payload(); raw.pop('query')
        self.assertFalse(search_result(raw)['ok'])

    def test_oversized_integers_still_produce_explicit_valid_error(self):
        raw = payload([{'title': 'x', 'views': 10 ** 5000}])
        parsed = json.loads(serialize_result(search_result(raw)))
        self.assertEqual(parsed['status'], 'error')
        self.assertEqual(parsed['errors'][0]['code'], 'INVALID_RESULT')
        raw = payload(); raw['total'] = 10 ** 5000
        self.assertEqual(json.loads(serialize_result(search_result(raw)))['status'], 'error')
        self.assertEqual(json.loads(serialize_result(None))['status'], 'error')

    def test_budget_validation(self):
        for kwargs in ({'max_chars': 10}, {'field_chars': 0}, {'max_items': 0}, {'max_chars': True}):
            with self.assertRaises(ValueError): ResultBudget(**kwargs)


class SharedServiceTest(unittest.TestCase):
    def test_exa_x_empty_note_preserves_empty_and_partial_service_semantics(self):
        with patch.object(research, '_default_providers', return_value={'exa': lambda _: []}):
            result = service.search('site:x.com agents', providers=['exa'], rerank=False)
        self.assertEqual(result['status'], 'empty')
        self.assertTrue(result['ok'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['failed_channels'], [])
        self.assertIn(research.EXA_X_EMPTY_NOTE, result['display_notes'])
        with patch.object(research, '_default_providers', return_value={'exa': lambda _: []}):
            result = service.search('site:x.com agents', providers=['exa', 'unknown'], rerank=False)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['failed_channels'][0]['channel'], 'unknown')
        self.assertIn(research.EXA_X_EMPTY_NOTE, result['display_notes'])

    def test_result_contract_allows_only_fixed_coverage_note_and_rejects_bad_shape(self):
        raw = payload()
        raw['attempted_channels'] = raw['succeeded_channels'] = ['exa']
        raw['display_notes'] = [research.EXA_X_EMPTY_NOTE, 'SECRET provider arbitrary note', research.EXA_X_EMPTY_NOTE]
        result = search_result(raw)
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(result['display_notes'], [research.EXA_X_EMPTY_NOTE])
        self.assertNotIn('SECRET', serialize_result(result))
        for malformed in (None, research.EXA_X_EMPTY_NOTE, [12]):
            raw['display_notes'] = malformed
            self.assertEqual(search_result(raw)['status'], 'error')
        raw['display_notes'] = [research.EXA_X_EMPTY_NOTE]
        raw['attempted_channels'] = raw['succeeded_channels'] = ['hacker_news']
        self.assertNotIn(research.EXA_X_EMPTY_NOTE, search_result(raw)['display_notes'])

    def test_shared_service_uses_core_and_adds_request_identity(self):
        raw = payload([{"title": "合成条目", "url": "https://fixture.invalid/item"}])
        with patch.object(research, "search", return_value=raw) as search_core:
            result = service.search("  fixture  ", limit=3, providers=["a"], request_id="synthetic-request")
        search_core.assert_called_once_with("fixture", limit=3, providers=["a"], rerank=True)
        self.assertEqual(result["request_id"], "synthetic-request")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["title"], "合成条目")

    def test_shared_service_sanitizes_errors_and_selects_fetch_for_urls(self):
        with patch.object(research, "search", side_effect=RuntimeError("SECRET-synthetic")):
            failed = service.search("fixture", request_id="error-request")
        self.assertEqual(failed["errors"][0]["code"], "UPSTREAM_ERROR")
        self.assertNotIn("SECRET-synthetic", json.dumps(failed))

        raw_fetch = {"kind": "article", "url": "https://fixture.invalid", "content": "合成正文"}
        with patch.object(research, "fetch_url", return_value=raw_fetch) as fetch_core:
            result = service.query("https://fixture.invalid", request_id="fetch-request")
        fetch_core.assert_called_once_with("https://fixture.invalid")
        self.assertEqual(result["operation"], "fetch")
        self.assertEqual(result["request_id"], "fetch-request")
        self.assertEqual(result["data"]["content"], "合成正文")

    def test_shared_service_sanitizes_provider_and_fetch_failure_text(self):
        raw = payload(failed=[{"channel": "b", "error": "SECRET token /private/example"}], succeeded=["a"])
        raw["rerank_downgraded"] = "rerank_error: SECRET token /private/example"
        with patch.object(research, "search", return_value=raw):
            result = service.search("fixture")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_channels"][0]["error"], "上游请求失败")
        self.assertEqual(result["rerank_downgraded"], "rerank_error")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("/private/", json.dumps(result))

        with patch.object(research, "fetch_url", return_value={
            "kind": "article", "url": "https://fixture.invalid", "ok": False,
            "error": "SECRET token timeout at /private/example", "retryable": True,
        }):
            failed = service.fetch("https://fixture.invalid")
        self.assertEqual(failed["errors"][0]["message"], "上游超时")
        self.assertEqual(failed["data"]["error"], "上游超时")
        self.assertTrue(failed["errors"][0]["retryable"])
        self.assertNotIn("SECRET", json.dumps(failed))


class MCPContractTest(unittest.TestCase):
    def setUp(self):
        class FakeMCP:
            def __init__(self, name): self.name = name
            def tool(self): return lambda function: function
        stub = types.ModuleType('mcp.server.mcpserver'); stub.MCPServer = FakeMCP
        spec = importlib.util.spec_from_file_location('offline_mcp_contract', ROOT / 'research_mcp/mcp_server.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'mcp': types.ModuleType('mcp'), 'mcp.server': types.ModuleType('mcp.server'), 'mcp.server.mcpserver': stub}):
            spec.loader.exec_module(self.module)

    def test_entrypoint_keeps_name_input_and_returns_only_json(self):
        with patch.object(research, 'search', return_value=payload()) as search:
            value = self.module.research_search('fixture', 100, ['a'])
        self.assertEqual(json.loads(value)['status'], 'empty')
        search.assert_called_once_with('fixture', limit=40, providers=['a'], rerank=True)
        with patch.object(research, 'search') as search:
            invalid = json.loads(self.module.research_search('fixture', 'not-an-integer'))
        self.assertEqual(invalid['errors'][0]['code'], 'INVALID_INPUT')
        search.assert_not_called()

    def test_mcp_preserves_conditional_coverage_note_as_valid_json(self):
        with patch.object(research, '_default_providers', return_value={'exa': lambda _: []}), \
             patch('research_mcp.rerank.filter_candidates', return_value=([], 'synthetic-disabled')):
            text = self.module.research_search('site:twitter.com agents', 3, ['exa'])
        result = json.loads(text)
        self.assertEqual(result['status'], 'empty')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['failed_channels'], [])
        self.assertIn(research.EXA_X_EMPTY_NOTE, result['display_notes'])

    def test_failed_fetch_and_exception_are_structured_without_raw_secrets(self):
        with patch.object(research, 'fetch_url', return_value={'ok': False, 'error': 'not found', 'retryable': False}):
            self.assertEqual(json.loads(self.module.research_search('https://fixture.invalid'))['errors'][0]['code'], 'READ_FAILED')
        with patch.object(research, 'search', side_effect=RuntimeError('SECRET-fixture')):
            text = self.module.research_search('fixture')
        self.assertNotIn('SECRET-fixture', text)
        self.assertEqual(json.loads(text)['status'], 'error')
