"""The probe must preserve network errors independently of login requirements."""
import unittest
from unittest.mock import patch

from research_mcp.channels.http_search import Response
from scripts.probe_anonymous_http import probe, markdown


class AnonymousProbeTests(unittest.TestCase):
    def test_tls_is_not_platform_block_or_login_evidence(self):
        with patch('scripts.probe_anonymous_http.h.fetch', return_value=Response('', 0, '', 60)):
            row = probe('youtube')
        self.assertFalse(row['requires_login'])
        self.assertEqual(row['requests'][0]['observation'], 'tls_error')
        self.assertEqual(row['requests'][0]['curl_exit_code'], 60)
        self.assertIsNone(row['requests'][0]['result_count'])

    def test_blocking_does_not_change_catalog_login_requirement(self):
        with patch('scripts.probe_anonymous_http.h.fetch', return_value=Response('challenge', 403, 'https://pypi.org', 0)):
            row = probe('pypi')
        self.assertFalse(row['requires_login'])
        self.assertEqual(row['requests'][0]['observation'], 'blocked')
        self.assertIn('not inferred', row['login_basis'])

    def test_http_200_js_shell_is_not_empty_search(self):
        with patch('scripts.probe_anonymous_http.h.fetch', return_value=Response('<script>render()</script>', 200, 'https://www.youtube.com', 0)):
            row = probe('youtube')
        self.assertEqual(row['requests'][0]['observation'], 'parse_error')
        self.assertEqual(row['requests'][0]['http_status'], 200)
        self.assertIsNone(row['requests'][0]['result_count'])

    def test_browser_link_hints_are_not_http_migration_acceptance(self):
        with patch('scripts.probe_anonymous_http.h.fetch', return_value=Response('<a href="/post/123">Result</a>', 200, 'https://sspai.com', 0)):
            row = probe('sspai')
        self.assertEqual(row['requests'][0]['observation'], 'result_link_hints')
        self.assertEqual(row['implementation'], 'browser')
        table = markdown({'collected_at': 'test', 'channels': [row]})
        self.assertIn('旧表已撤回', table)
        self.assertIn('不要求登录', table)
