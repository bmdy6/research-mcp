"""Offline contracts for anonymous curl providers and the reproducible probe."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from research_mcp import service
from research_mcp.channel_catalog import list_channels
from research_mcp.channels import http_search as h
from research_mcp.channels.errors import ChannelError


def wire(body, status=200, url='https://example.com'):
    if not isinstance(body, str):
        body = json.dumps(body)
    return subprocess.CompletedProcess([], 0, f'{body}\n{status}\n{url}'.encode(), b'')


def youtube(primary):
    return '<script>var ytInitialData = ' + json.dumps({'contents': {
        'twoColumnSearchResultsRenderer': {'primaryContents': primary,
            'secondaryContents': {'videoRenderer': {'videoId': 'BAD12345678', 'title': {'simpleText': 'sidebar'}}}}}}) + ';</script>'


class HttpSearchTests(unittest.TestCase):
    def test_four_service_providers_work_without_browser_or_opt_in(self):
        examples = {
            'youtube': youtube({'videoRenderer': {'videoId': 'abcDE_12345', 'title': {'runs': [{'text': 'Python 教程'}]}}}),
            'douban': '<h3><a href="https://www.douban.com/link2/?url=https%3A%2F%2Fbook.douban.com%2Fsubject%2F123%2F&amp;token=secret">Python 教程</a></h3>',
            'hupu': '<a class="content-wrap-span" href="/123.html?token=secret">Python 教程</a>',
            'devto': [{'title': 'Python 教程', 'url': 'https://dev.to/example/python?token=secret', 'tag_list': ['python'], 'description': 'summary'}],
        }
        for name, body in examples.items():
            with self.subTest(name=name), patch.dict(os.environ, {}, clear=True), \
                    patch.object(h.subprocess, 'run', return_value=wire(body)) as run, \
                    patch('research_mcp.channels.local_browser._run', side_effect=AssertionError('browser used')):
                result = service.search('python', providers=[name], limit=1)
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(len(result['candidates']), 1)
            self.assertNotIn('secret', json.dumps(result))
            self.assertTrue(result['candidates'][0]['actual_backend'].startswith('http/'))
            command = run.call_args.args[0]
            self.assertEqual(command[:2], ['curl', '-q'])
            for forbidden in ('-k', '--insecure', '--cookie', '-b', '--netrc', '--user'):
                self.assertNotIn(forbidden, command)
        with patch('shutil.which', return_value=None):
            rows = {r['id']: r for r in list_channels()}
        for name in examples:
            self.assertEqual(rows[name]['cost_class'], 'keyless')
            self.assertFalse(rows[name]['requires_login'])
            self.assertNotEqual(rows[name]['actual_backend'], 'ego-browser/local_browser')

    def test_youtube_uses_only_primary_video_renderers_and_deduplicates(self):
        video = {'videoRenderer': {'videoId': 'abcDE_12345', 'title': {'runs': [{'text': '中文 '}, {'text': 'Python'}]},
                                   'longBylineText': {'runs': [{'text': 'Teacher'}]}}}
        rows = h.parse_results('youtube', youtube([video, video,
            {'videoRenderer': {'videoId': 'bad', 'title': {'simpleText': 'bad ID'}}}]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '中文 Python')
        self.assertEqual(rows[0]['url'], 'https://www.youtube.com/watch?v=abcDE_12345')
        self.assertEqual(rows[0]['author'], 'Teacher')
        assignment = youtube([video]).replace('var ytInitialData =', 'window["ytInitialData"] =')
        self.assertEqual(h.parse_results('youtube', assignment), rows)

    def test_youtube_empty_requires_explicit_primary_message(self):
        self.assertEqual(h.parse_results('youtube', youtube({'messageRenderer': {'text': {'simpleText': 'No results found'}}})), [])
        for html in ('<html>200 but JS shell</html>', '<script>var ytInitialData = {};</script>', youtube({}),
                     '<script>var ytInitialData = invalid;</script>'):
            with self.assertRaises(ChannelError) as err:
                h.parse_results('youtube', html)
            self.assertEqual(err.exception.status, 'parse_error')

    def test_douban_result_scope_redirect_allowlist_and_tracking(self):
        html = '''<a href="https://book.douban.com/subject/999/">sidebar</a>
<h3><a href="https://www.douban.com/link2/?url=https%3A%2F%2Fbook.douban.com%2Fsubject%2F123%2F%3Ftoken%3Dsecret">书<em>名</em></a></h3>
<h3><a href="https://movie.douban.com/subject/456/?token=secret">Movie</a></h3>
<h3><a href="/doubanapp/dispatch?uri=%2Fmusic%2F789&amp;token=secret">Music</a></h3>
<h3><a href="/link2/?url=https%3A%2F%2Fevil.invalid%2Fsubject%2F123%2F">bad</a></h3>
<h3><a href="https://user:password@book.douban.com/subject/123/">bad</a></h3>
<h3><a href="https://book.douban.com:8443/subject/123/">bad</a></h3>'''
        rows = h.parse_results('douban', html)
        self.assertEqual([r['url'] for r in rows], ['https://book.douban.com/subject/123/',
            'https://movie.douban.com/subject/456/', 'https://www.douban.com/doubanapp/dispatch?uri=%2Fmusic%2F789'])
        self.assertEqual(rows[0]['title'], '书名')

    def test_douban_search_keeps_all_three_categories(self):
        urls = []
        def run(cmd, **kwargs):
            url = cmd[-3]
            urls.append(url)
            return wire('<h3><a href="https://book.douban.com/subject/123/">book</a></h3>')
        with patch.object(h.subprocess, 'run', side_effect=run):
            rows = h.search({'web_query': '三体 & Dune', 'web_limit': 2}, platform='douban')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(urls), 3)
        self.assertTrue(all('q=%E4%B8%89%E4%BD%93%20%26%20Dune' in u for u in urls))
        self.assertEqual({u.split('cat=')[1].split('&')[0] for u in urls}, {'1001', '1002', '1003'})

    def test_hupu_filters_sidebar_forum_and_unsafe_urls(self):
        html = '''<a href="/999.html">sidebar</a><a class="content-wrap-span" href="/123.html?track=secret">篮球 <em>NBA</em></a>
<a class="content-wrap-span" href="/123.html">重复</a><a class="content-wrap-span" href="/177">论坛</a>
<a class="content-wrap-span" href="https://evil.invalid/234.html">bad</a>'''
        rows = h.parse_results('hupu', html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], 'https://bbs.hupu.com/123.html')
        self.assertEqual(rows[0]['title'], '篮球 NBA')

    def test_devto_sends_exact_tag_not_ignored_fulltext_query(self):
        body = [{'title': '中文资料', 'url': 'https://dev.to/example/post', 'tag_list': ['中文']}]
        with patch.object(h.subprocess, 'run', return_value=wire(body)) as run:
            rows = h.search({'web_query': '中文', 'web_limit': 2}, platform='devto')
        self.assertEqual(len(rows), 1)
        self.assertIn('tag=%E4%B8%AD%E6%96%87', run.call_args.args[0][-3])
        self.assertNotIn('q=', run.call_args.args[0][-3])
        self.assertEqual(h.parse_results('devto', '[]', query='unknown'), [])
        for body in ({'error': 'bad'}, [None], [{'title': 'unrelated', 'url': 'https://dev.to/u/p', 'tag_list': ['other']}],
                     [{'title': 'bad', 'url': 'https://evil.invalid/u/p', 'tag_list': ['python']}]):
            with self.assertRaises(ChannelError):
                h.parse_results('devto', json.dumps(body), query='python')

    def test_transport_failure_categories_do_not_turn_tls_into_blocking(self):
        for code, expected in ((60, 'connection_failed'), (6, 'connection_failed'), (28, 'timeout')):
            with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, b'', b'SECRET')):
                with self.assertRaises(ChannelError) as err:
                    h.search({'web_query': 'python'}, platform='youtube')
            self.assertEqual(err.exception.status, expected)
            self.assertNotIn('SECRET', str(err.exception))
        for status, expected in ((403, 'blocked'), (405, 'blocked'), (401, 'auth_required'), (429, 'quota_exhausted'), (500, 'connection_failed')):
            with patch.object(h.subprocess, 'run', return_value=wire('bad', status)):
                with self.assertRaises(ChannelError) as err:
                    h.search({'web_query': 'python'}, platform='youtube')
            self.assertEqual(err.exception.status, expected)

    def test_parser_never_calls_shell_pages_empty_success(self):
        for name in h.HTTP_PLATFORMS - {'devto', 'youtube'}:
            with self.assertRaises(ChannelError):
                h.parse_results(name, '<html><script>render()</script></html>')
        for name in ('douban', 'hupu'):
            self.assertEqual(h.parse_results(name, '<p>没有找到相关结果</p>'), [])

    def test_douban_categories_share_one_deadline(self):
        body = '<h3><a href="https://book.douban.com/subject/123/">book</a></h3>'
        with patch.object(h.time, 'monotonic', side_effect=[100, 100.1, 121]), \
                patch.object(h.subprocess, 'run', return_value=wire(body)) as run:
            with self.assertRaises(ChannelError) as err:
                h.search({'web_query': 'Dune'}, platform='douban')
        self.assertEqual(err.exception.status, 'timeout')
        self.assertEqual(run.call_count, 1)

    def test_timeout_missing_curl_and_empty_query(self):
        for error, expected in ((FileNotFoundError('SECRET'), 'startup_failed'),
                                (subprocess.TimeoutExpired('curl', 20), 'timeout')):
            with patch.object(h.subprocess, 'run', side_effect=error):
                with self.assertRaises(ChannelError) as err:
                    h.search({'web_query': 'python'}, platform='youtube')
            self.assertEqual(err.exception.status, expected)
        with patch.object(h.subprocess, 'run') as run:
            self.assertEqual(h.search({}, platform='youtube'), [])
        run.assert_not_called()
