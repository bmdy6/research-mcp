"""Offline browser contract tests: never launch a browser or access credentials."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from research_mcp import service
from research_mcp.channels import local_browser as browser
from research_mcp.channels.errors import ChannelError


class LocalBrowserTests(unittest.TestCase):
    def call(self, payload, provider='jd'):
        output = subprocess.CompletedProcess([], 0, 'notice\nRESEARCH_BROWSER_JSON:' + json.dumps(payload), '')
        with patch.dict(os.environ, {'RESEARCH_LOCAL_BROWSER': '1', 'RESEARCH_BROWSER_SPACE_ID': '12',
                                     'RESEARCH_BROWSER_PAGE': 'p5'}), \
                patch.object(browser.shutil, 'which', return_value='/bin/ego-browser'), \
                patch.object(browser, '_run', return_value=output):
            return service.search('轻薄本 14英寸', providers=[provider], limit=2)

    def test_price_survives_public_result_contract(self):
        result = self.call({'status': 'ok', 'items': [{'title': '轻薄本', 'url': 'https://item.jd.com/123.html',
                           'price': '6399', 'author': '品牌官方店'}]})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['candidates'][0]['price'], '6399')
        self.assertEqual(result['candidates'][0]['price_note'], '页面标价，未含券/国补')
        self.assertEqual(result['candidates'][0]['actual_backend'], 'ego-browser/local_browser')

    def test_barriers_and_malformed_output_are_not_empty_success(self):
        for status in ('blocked', 'auth_required', 'startup_failed', 'parse_error', 'timeout'):
            result = self.call({'status': status})
            self.assertEqual(result['failed_channels'][0]['status'], status)
        for payload in ({}, {'status': 'ok', 'items': 'bad'}, {'status': 'ok', 'items': []},
                        {'status': 'ok', 'items': [{'title': 'bad', 'url': 'https://evil.invalid', 'price': '1'}]}):
            self.assertEqual(self.call(payload)['failed_channels'][0]['status'], 'parse_error')

    def test_missing_cli_and_opt_in_fail_safely(self):
        with patch.object(browser.shutil, 'which', return_value=None):
            result = service.search('电脑', providers=['jd'])
        self.assertEqual(result['failed_channels'][0]['status'], 'startup_failed')
        with patch.object(browser.shutil, 'which', return_value='/bin/ego-browser'), \
                patch.dict(os.environ, {}, clear=True):
            result = service.search('电脑', providers=['jd'])
        self.assertEqual(result['failed_channels'][0]['status'], 'auth_required')

    def test_local_providers_cannot_be_used_by_public_adapter(self):
        with patch.object(browser, '_run') as run:
            result = service.search_public('电脑', providers=['jd'])
        self.assertFalse(result['ok'])
        run.assert_not_called()

    def test_timeout_and_sensitive_output_are_sanitized(self):
        for failure, status in [(subprocess.TimeoutExpired('ego-browser', 30), 'timeout'),
                                (FileNotFoundError('SECRET'), 'startup_failed')]:
            with patch.dict(os.environ, {'RESEARCH_LOCAL_BROWSER': '1', 'RESEARCH_BROWSER_SPACE_ID': '12',
                                         'RESEARCH_BROWSER_PAGE': 'p5'}), \
                    patch.object(browser.shutil, 'which', return_value='/bin/ego-browser'), \
                    patch.object(browser, '_run', side_effect=failure):
                result = service.search('电脑', providers=['jd'])
            self.assertEqual(result['failed_channels'][0]['status'], status)
            self.assertNotIn('SECRET', json.dumps(result))

    def test_real_ego_stderr_console_and_prompt(self):
        with patch.dict(os.environ, {'RESEARCH_LOCAL_BROWSER': '1', 'RESEARCH_BROWSER_SPACE_ID': '12',
                                     'RESEARCH_BROWSER_PAGE': 'p5'}), \
                patch.object(browser.shutil, 'which', return_value='/bin/ego-browser'):
            for output in ('RESEARCH_BROWSER_JSON:{"status":"auth_required","items":[]}',
                           'A browser permission prompt for local network access has appeared.'):
                with patch.object(browser, '_run', return_value=subprocess.CompletedProcess([], 0, '', output)):
                    result = service.search('电脑', providers=['jd'])
                self.assertEqual(result['failed_channels'][0]['status'], 'auth_required')

    def test_confirmed_empty(self):
        self.assertEqual(self.call({'status': 'empty', 'items': []})['status'], 'empty')

    def test_catalog_and_defaults(self):
        from research_mcp.channel_catalog import list_channels
        from research_mcp.research import default_search_providers
        with patch.object(browser.shutil, 'which', return_value=None):
            row = next(r for r in list_channels() if r['id'] == 'jd')
        self.assertEqual(row['cost_class'], 'local_browser')
        self.assertTrue(row['requires_login'])
        self.assertEqual(row['status'], 'startup_failed')
        self.assertNotIn('jd', default_search_providers())

    def test_gap_between_processes_and_timeout_updates_timestamp(self):
        import tempfile
        from pathlib import Path
        try:
            import fcntl
        except ImportError:
            self.skipTest('local browser currently requires POSIX locks')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(browser.Path, 'home', return_value=Path(directory)), \
                patch.object(browser.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.object(browser.time, 'monotonic', side_effect=[100, 101, 102, 107]), \
                patch.object(browser.time, 'sleep') as sleep:
            browser._run(['ego-browser', 'nodejs', '-e', ''])
            browser._run(['ego-browser', 'nodejs', '-e', ''])
            sleep.assert_called_once_with(4.0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.kwargs['timeout'], 45)

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node needed for DOM fixture')
    def test_packaged_javascript_reads_unsubsidized_price_and_stops_for_barriers(self):
        # Execute the actual packaged JS against a tiny DOM fixture, no network.
        script = browser.Path(browser.__file__).with_suffix('.mjs').read_text()
        fixture = r'''
const input = {space:12,page:'p5',query:'轻薄本',limit:2};
let navigations = 0;
globalThis.location = {hostname:'search.jd.com'};
const card = {querySelector(selector) {
 if (selector.includes('goods_title')) return {getAttribute:()=> '轻薄本'};
 if (selector.includes('_container_')) return {
   innerText: '5354.15 国补领后价 ¥6399',
   querySelector: s=> ({innerText:s.includes('_gray_') ? '¥6399' : '¥5354.15'})};
 if (selector.includes('_name_')) return {innerText:'官方店'};
}};
globalThis.document = {body:{innerText:BARRIER}, querySelectorAll:()=>[
 {href:'https://chat.jd.com/index.action?pid=123',closest:()=>card}]};
const page = {label:'p5',goto:async()=>{navigations++},waitForFunction:async()=>{},
 evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces = async()=>[{id:12,ownership:'agent'}];
const taskSpace = async()=>({pages:async()=>[page]});
'''
        for barrier, status in [('', 'ok'), ('请完成验证', 'blocked')]:
            result = subprocess.run(['node', '--input-type=module', '-e',
                                     fixture.replace('BARRIER', json.dumps(barrier)) + script],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip().split(browser._MARKER)[-1])
            self.assertEqual(payload['status'], status)
            if status == 'ok':
                self.assertEqual(payload['items'][0]['price'], '6399')
                self.assertEqual(payload['items'][0]['url'], 'https://item.jd.com/123.html')
