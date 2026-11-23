import unittest
from unittest.mock import patch
from research_mcp import research
from research_mcp.channels import bilibili, open_websearch

ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1234.5678</id><title>General paper</title><summary>No implementation link</summary><published>2026-11-20T00:00:00Z</published></entry><entry><id>https://arxiv.org/abs/2345.6789</id><title>Code paper</title><summary>https://github.com/org/repo</summary><published>2026-11-20T00:00:00Z</published></entry></feed>'''

class ExtendedSearchTests(unittest.TestCase):
    def test_arxiv_all_papers_default_and_filter_opt_in(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(research, '_http_text', return_value=ATOM):
            self.assertEqual(len(research._arxiv_search({})), 2)
            self.assertEqual(len(research._arxiv_search({'arxiv_require_github': True})), 1)

    def test_arxiv_filter_env(self):
        with patch.dict('os.environ', {'ARXIV_REQUIRE_GITHUB': '1'}), patch.object(research, '_http_text', return_value=ATOM):
            self.assertEqual(len(research._arxiv_search({})), 1)
            self.assertEqual(len(research._arxiv_search({'arxiv_require_github': False})), 2)

    def test_bilibili_relevance_order_survives_newer_irrelevant_video(self):
        rows = [{'bvid':'BVfirst','title':'Python tutorial','pubdate':1600000000},
                {'bvid':'BVsecond','title':'Latest vlog','pubdate':1800000000}]
        with patch.object(bilibili, '_search_api', return_value=rows) as api, patch.object(bilibili,'_buvid3',return_value=''):
            items = bilibili.search({'bilibili_queries':['Python tutorial']})
            self.assertEqual([i['title'] for i in items], ['Python tutorial','Latest vlog'])
            self.assertEqual(api.call_args.kwargs['order'], 'totalrank')

    def test_core_requests_relevance_in_both_languages(self):
        for q in ('Python tutorial','机械键盘'):
            with patch.object(research, '_default_providers', return_value={'bilibili':lambda cfg: self.assertEqual(cfg['bilibili_order'],'totalrank') or []}):
                research.search(q,providers=['bilibili'])

    def test_csdn_uses_same_pinned_upstream(self):
        self.assertIn('csdn', open_websearch.ENGINES)
        with patch.object(open_websearch, '_execute', return_value={'status':'ok','data':{'engines':['csdn'],'partialFailures':[],'results':[{'engine':'csdn','title':'Python','url':'https://blog.csdn.net/u/article/details/1','description':'test'}]}}) as call:
            items = open_websearch.search({'web_query':'Python','web_limit':3},engine='csdn')
        self.assertEqual(items[0]['source'],'csdn')
        call.assert_called_once_with('Python','csdn',3)


class VaultLauncherTests(unittest.TestCase):
    def test_only_exact_free_keys_and_duplicate_rejection(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location('free_launcher', Path(__file__).resolve().parents[1]/'scripts/run_free_search.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.keys_from_vault({'x':{'TAVILY_API_KEY':{'value':'fixture'},'OTHER_KEY':{'value':'ignored'}}}), {'TAVILY_API_KEY':'fixture'})
        with self.assertRaises(ValueError):
            module.keys_from_vault({'a':{'TAVILY_API_KEY':{'value':'one'}},'b':{'TAVILY_API_KEY':{'value':'two'}}})
