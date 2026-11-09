"""Synthetic GitHub regression matrix across actual CLI, MCP stdio."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_mcp import research
from research_mcp import github_read
from research_mcp import service
from research_mcp.result_contract import fetch_result, serialize_result

URL = 'https://github.com/example/project'
CASES = [('repository', '', 'normal'), ('issue', '/issues/7', 'normal'),
         ('pull_request', '/pull/7', 'normal'), ('file', '/blob/main/README.md', 'normal'),
         ('pull_request', '/issues/7', 'issue_pr'),
         ('file', '/blob/feature/topic/src/example.py', 'slash_ref'),
         ('pagination', '/issues/7?comment_limit=2', 'pagination'),
         ('pagination', '/pull/7?comment_limit=2', 'pagination'),
         ('continuation', '/issues/7?comment_limit=2&comment_page=2&comment_stream=comments', 'pagination'),
         ('missing', '/issues/7', 'missing'), ('permission', '/pull/7', 'permission'),
         ('auth', '/blob/main/README.md', 'auth'), ('rate', '/issues/7', 'rate'),
         ('secondary', '/pull/7', 'secondary'),
         ('exhausted', '/issues/7', 'exhausted'), ('retry_after', '/pull/7', 'retry_after'), ('unsupported', '/actions', 'normal'),
         ('malformed', '/issues/7', 'malformed')]


def check_data(test, kind, data):
    if kind in ('missing', 'permission', 'auth', 'rate', 'secondary', 'exhausted', 'retry_after', 'unsupported', 'malformed'):
        test.assertFalse(data['ok'])
        test.assertEqual(data['retryable'], kind in ('rate', 'secondary', 'exhausted', 'retry_after'))
        expected = {'missing': '（404）', 'auth': '（401）', 'permission': '（403）',
                    'rate': '限流', 'secondary': '限流', 'exhausted': '限流', 'retry_after': '限流'}
        if kind in expected:
            test.assertIn(expected[kind], data['error'])
        if kind in ('missing', 'permission', 'auth'):
            test.assertNotIn('限流', data['error'])
        test.assertNotIn('SYNTHETIC_SENSITIVE_MARKER', json.dumps(data))
        return
    if kind in ('pagination', 'continuation'):
        test.assertTrue(data['partial'])
        for stream, coverage in data['coverage'].items():
            test.assertEqual(coverage['returned'], len(data['discussion'][stream]))
            test.assertEqual(coverage['truncated'], kind == 'pagination')
            if kind == 'pagination':
                test.assertIn('comment_page=2', coverage['next_url'])
                test.assertIn('comment_stream=' + stream, coverage['next_url'])
            else:
                test.assertIsNone(coverage['next_url'])
                test.assertEqual(coverage['page'], 2)
        return
    test.assertEqual(data['object_type'], kind)
    test.assertIn('Synthetic', data['content'])
    if kind == 'issue':
        test.assertEqual(set(data['discussion']), {'comments'})
    if kind == 'pull_request':
        test.assertEqual(set(data['discussion']), {'comments', 'reviews', 'review_comments'})
        test.assertEqual(data['discussion']['review_comments'][0]['path'], 'src/example.py')
        test.assertIn('## review_comments', data['content'])
    if kind == 'file':
        test.assertTrue(data['file']['ref'])
        test.assertTrue(data['file']['path'])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='research-github-test-')
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name)
        target = self.bin / 'gh'
        target.write_text('#!' + sys.executable + '\n' + (ROOT / 'tests/github_fixture.py').read_text())
        target.chmod(0o700)
        self.env = {**os.environ, 'HOME': self.temp.name, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'RESEARCH_PY': 'unused'}

    def cli(self, suffix, scenario='normal'):
        process = subprocess.run([sys.executable, '-m', 'research_mcp',
                                          'fetch', URL + suffix, '--format', 'json'],
                                         env={**self.env, 'GITHUB_FIXTURE': scenario}, text=True, capture_output=True, timeout=15)
        output = process.stdout
        return json.loads(output)['data']


class GitHubReadTest(Fixture):
    def test_actual_cli_matrix(self):
        for kind, suffix, scenario in CASES:
            with self.subTest(kind=kind, suffix=suffix):
                check_data(self, kind, self.cli(suffix, scenario))

    def test_status_precedes_stderr_and_header_text(self):
        for status, expected in ((404, '404'), (401, '401'), (403, '403'), (429, '限流'), (500, '读取失败')):
            with self.subTest(status=status):
                wire = (f'HTTP/2.0 {status} Synthetic\r\nX-Ratelimit-Limit: 5000\r\n'
                        'X-Ratelimit-Remaining: 42\r\nX-Notice: rate limit\r\n\r\n'
                        '{"message": "Not Found"}')
                process = subprocess.CompletedProcess([], 1, wire, 'gh: API rate limit exceeded (HTTP 429) SYNTHETIC_SENSITIVE_MARKER')
                with patch('research_mcp.github_read.subprocess.run', return_value=process):
                    with self.assertRaises(github_read.GitHubReadError) as caught:
                        github_read.api('synthetic')
                self.assertEqual(caught.exception.status, status)
                self.assertIn(expected, caught.exception.reason)
                self.assertNotIn('SYNTHETIC_SENSITIVE_MARKER', str(caught.exception))
                if status != 429:
                    self.assertNotIn('限流', caught.exception.reason)

    def test_no_status_uses_only_stderr_without_headers(self):
        for stderr, expected in (('gh: Not Found (HTTP 404)', '404'),
                                 ('gh: authentication required', '认证'),
                                 ('gh: forbidden', '权限'),
                                 ('gh: API rate limit exceeded', '限流'),
                                 ('X-Ratelimit-Limit: 5000\nX-Notice: rate limit', '读取失败')):
            with self.subTest(stderr=stderr):
                process = subprocess.CompletedProcess([], 1, 'X-Ratelimit-Limit: 5000\n\nrate limit', stderr)
                with patch('research_mcp.github_read.subprocess.run', return_value=process):
                    with self.assertRaises(github_read.GitHubReadError) as caught:
                        github_read.api('synthetic')
                self.assertIsNone(caught.exception.status)
                self.assertIn(expected, caught.exception.reason)

    def test_mcp_preserves_only_exact_safe_http_diagnoses(self):
        for message in ('GitHub 资源不存在或当前身份不可见（404）', 'GitHub 认证不可用（401）',
                        'GitHub 权限不足（403）'):
            self.assertEqual(service._safe_failure_reason(message), message)
            self.assertNotIn('SYNTHETIC_SENSITIVE_MARKER', service._safe_failure_reason(
                message + ' SYNTHETIC_SENSITIVE_MARKER'))

    def test_original_path_and_unsupported_urls_never_read_repository(self):
        with patch('research_mcp.research._github_repo') as repo, patch.dict(os.environ, self.env):
            result = research.fetch_url(URL + '/issues/7')
            check_data(self, 'issue', result)
            for suffix in ('/actions', '/pull/7/files', '/tree/main', '/issues/not-a-number', '/blob/main'):
                self.assertFalse(research.fetch_url(URL + suffix)['ok'])
            repo.assert_not_called()
        self.assertEqual(research.normalize_url(URL + '/issues/7'), URL)

    def test_page_and_budget_validation_no_provider_calls(self):
        for query in ('comment_limit=0', 'comment_limit=101', 'comment_page=-1',
                      'comment_page=10001', 'comment_limit=x', 'comment_limit=1&comment_limit=2'):
            with self.subTest(query=query), patch('research_mcp.github_read.api') as api:
                self.assertFalse(research.fetch_url(URL + '/issues/7?' + query)['ok'])
                api.assert_not_called()

    def test_blob_binary_directory_explicit_ref_and_missing(self):
        for scenario in ('binary', 'directory', 'missing'):
            self.assertFalse(self.cli('/blob/main/README.md', scenario)['ok'])
        result = self.cli('/blob/feature/topic/src/example.py?github_ref=feature%2Ftopic', 'slash_ref')
        self.assertEqual(result['file'], {'ref': 'feature/topic', 'path': 'src/example.py', 'sha': 'synthetic-sha'})

    def test_failure_categories_are_safe_for_homepage_too(self):
        for scenario in ('permission', 'auth', 'rate', 'secondary'):
            result = self.cli('', scenario)
            self.assertFalse(result['ok'])
            self.assertNotIn('SYNTHETIC_SENSITIVE_MARKER', json.dumps(result))
            self.assertEqual(result['retryable'], scenario in ('rate', 'secondary'))

    def test_coverage_survives_service_and_serialization(self):
        with patch.dict(os.environ, {**self.env, 'GITHUB_FIXTURE': 'pagination'}):
            result = json.loads(serialize_result(service.fetch(URL + '/pull/7?comment_limit=2')))
        self.assertEqual(result['status'], 'partial')
        check_data(self, 'pagination', result['data'])
        self.assertFalse(result['output_truncation']['truncated'])
        with patch.dict(os.environ, {**self.env, 'GITHUB_FIXTURE': 'large'}):
            result = json.loads(serialize_result(service.fetch(URL + '/issues/7?comment_limit=2')))
        self.assertTrue(result['output_truncation']['truncated'])
        self.assertEqual(result['data']['coverage']['comments']['returned'], 2)
        self.assertIn('comment_page=2', result['data']['coverage_note'])

    def test_subprocess_timeout_missing_executable_and_bad_json(self):
        for error, retryable in ((subprocess.TimeoutExpired('gh', 30), True), (FileNotFoundError(), False)):
            with patch('research_mcp.github_read.subprocess.run', side_effect=error):
                result = research.fetch_url(URL + '/issues/7')
            self.assertFalse(result['ok'])
            self.assertEqual(result['retryable'], retryable)

    def test_empty_file_is_a_successful_read(self):
        with patch('research_mcp.github_read.api', return_value=({'type': 'file', 'encoding': 'base64',
                                                    'size': 0, 'content': '', 'sha': 'empty'}, False)):
            result = research.fetch_url(URL + '/blob/main/empty.txt')
        self.assertEqual(result['content'], '')
        self.assertEqual(fetch_result(result)['status'], 'ok')

    def test_file_ref_fallback_stops_on_permission_or_rate_limit(self):
        for status in (401, 403, 429):
            with patch('research_mcp.github_read.api', side_effect=github_read.GitHubReadError(
                    'Synthetic unavailable', status=status)) as api:
                result = research.fetch_url(URL + '/blob/feature/topic/src/example.py')
            self.assertFalse(result['ok'])
            self.assertEqual(api.call_count, 1)

    def test_credential_and_port_urls_fail_without_echo_or_provider_call(self):
        for url in ('https://example:synthetic-value@github.com/example/project/issues/7',
                    'https://github.com:invalid/example/project/issues/7'):
            with patch('research_mcp.github_read.api') as api:
                result = research.fetch_url(url)
            self.assertFalse(result['ok'])
            self.assertNotIn('synthetic-value', json.dumps(result))
            api.assert_not_called()

    def test_each_pr_stream_continues_independently(self):
        result = self.cli('/pull/7?comment_limit=2', 'pagination')
        for stream, coverage in result['coverage'].items():
            continuation = coverage['next_url'].removeprefix(URL)
            result = self.cli(continuation, 'pagination')
            self.assertEqual(set(result['discussion']), {stream})
            self.assertEqual(result['coverage'][stream]['page'], 2)
            self.assertTrue(result['partial'])



@unittest.skipUnless(importlib.util.find_spec('mcp'), 'existing optional MCP SDK required')
class GitHubMCPTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_mcp_stdio_matrix(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        fixture = Fixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for scenario in sorted({scenario for _, _, scenario in CASES}):
            args = StdioServerParameters(command=sys.executable, args=['-m', 'research_mcp.mcp_server'],
                                         env={**fixture.env, 'GITHUB_FIXTURE': scenario})
            async with asyncio.timeout(30), stdio_client(args) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for kind, suffix, chosen in CASES:
                        if chosen != scenario:
                            continue
                        with self.subTest(kind=kind, scenario=scenario):
                            response = await session.call_tool('research_search', {'query': URL + suffix})
                            self.assertFalse(response.model_dump(by_alias=True)['isError'])
                            data = json.loads(response.content[0].text)
                            check_data(self, kind, data['data'])
                            if kind in ('pagination', 'continuation'):
                                self.assertEqual(data['status'], 'partial')
