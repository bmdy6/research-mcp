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
                patch.object(browser, '_run', return_value=output), \
                patch.dict(__import__('research_mcp.research',fromlist=['CHANNELS']).CHANNELS, {provider:browser.PROVIDERS[provider]}):
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

    def test_community_and_shopping_channels_preserve_fields(self):
        examples = {
            'taobao': 'https://item.taobao.com/item.htm?id=123',
            'tmall': 'https://detail.tmall.com/item.htm?id=123',
            'smzdm': 'https://www.smzdm.com/p/123/',
            'zhihu': 'https://www.zhihu.com/question/123/answer/456',
            'xiaohongshu': 'https://www.xiaohongshu.com/explore/abcdef123456789012345678',
            'douyin': 'https://www.douyin.com/video/123456',
            'sogou_wechat': 'https://weixin.sogou.com/link?url=public-redirect',
        }
        for name, url in examples.items():
            with self.subTest(platform=name):
                result = self.call({'status': 'ok', 'items': [{'title': '轻薄本推荐', 'url': url,
                                    'summary': '搜索结果摘要', 'author': '公开作者'}]}, provider=name)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['candidates'][0]['url'], url)
                self.assertEqual(result['candidates'][0]['summary'], '搜索结果摘要')
                self.assertNotIn('price', result['candidates'][0])
                with patch.object(browser, '_run') as run:
                    self.assertFalse(service.search_public('电脑', providers=[name])['ok'])
                run.assert_not_called()

    def test_browser_urls_reject_tracking_credentials_and_wrong_paths(self):
        for url in ('https://item.jd.com/123.html?token=secret',
                    'https://user:password@item.jd.com/123.html',
                    'https://item.jd.com:8443/123.html',
                    'https://item.jd.com/login', 'https://item.jd.com.evil/123.html'):
            self.assertEqual(self.call({'status':'ok', 'items':[{'title':'电脑', 'url':url,
                             'price':'6399'}]})['failed_channels'][0]['status'], 'parse_error')

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
    def test_packaged_javascript_platform_links_and_price_labels(self):
        script = browser.Path(browser.__file__).with_suffix('.mjs').read_text()
        # Minimal public result cards, including duplicate links and tracking data.
        # No browser, account, network, site scripts or signed media involved.
        fixture = r'''
const input = INPUT;
const platform = input.platform;
function node(text='', attrs={}, children={}) {
 return {innerText:text, ...attrs, getAttribute:k=>attrs[k],
   querySelector:s=>children[s] || null, closest:()=>node('card')};
}
let card, nodes;
if (platform==='taobao' || platform==='tmall') {
 card=node('', {href:'https://detail.tmall.com/item.htm?id=123&tracking=private'}, {
  '[class*="title--"][title]':node('',{title:'真实标题'}),
  '[class*="innerPriceWrapper"]':node('3195\n.16'),
  '[class*="priceWrapper"]':node('¥3195.16 国补后'),
  '[class*="shopNameText"]':node('公开店铺')});
} else if (platform==='smzdm') {
 card=node('真实标题',{href:'http://www.smzdm.com/p/123/?tracking=private'});
} else if (platform==='zhihu') {
 card=node('真实标题',{href:'https://www.zhihu.com/question/123/answer/456?tracking=private'});
} else if (platform==='xiaohongshu') {
 card=node('',{'data-note-id':'abcdef123456789012345678'}, {'.title':node('真实标题'),'.name':node('作者')});
} else if (platform==='douyin') {
 card=node('图文\n真实标题',{id:'waterfall_item_123456'}, {'.BjLsdJMi':node('真实标题'),'.WldPmwm5':node('作者')});
} else {
 card=node('真实标题',{href:'https://weixin.sogou.com/link?url=public-redirect&tracking=private'});
}
globalThis.location={hostname:HOST};
globalThis.document={body:{innerText:BODY},querySelectorAll:()=>[card,card]};
const page={close:async()=>{},label:'p5',goto:async(url)=>{if(!url.includes(encodeURIComponent(input.query)))throw Error('query not encoded')},
 waitForFunction:async()=>{},evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces=async()=>[{id:12,ownership:OWNER}];
const taskSpace=async()=>({newPage:async()=>page, pages:async()=>[page]});
'''
        cases = [('taobao','s.taobao.com','https://detail.tmall.com/item.htm?id=123'),
                 ('tmall','s.taobao.com','https://detail.tmall.com/item.htm?id=123'),
                 ('smzdm','search.smzdm.com','https://www.smzdm.com/p/123/'),
                 ('zhihu','www.zhihu.com','https://www.zhihu.com/question/123/answer/456'),
                 ('xiaohongshu','www.xiaohongshu.com','https://www.xiaohongshu.com/explore/abcdef123456789012345678'),
                 ('douyin','www.douyin.com','https://www.douyin.com/note/123456'),
                 ('sogou_wechat','weixin.sogou.com','https://weixin.sogou.com/link?url=public-redirect')]
        for platform, host, url in cases:
            for owner, body, status in [('agent','','ok'), ('agent','请完成验证','blocked'),
                                        ('agent','请先登录','auth_required'), ('user','','auth_required')]:
                with self.subTest(platform=platform, owner=owner, body=body):
                    setup = fixture.replace('INPUT',json.dumps({'space':12,'page':'p5','platform':platform,
                                             'query':'电脑 & laptop','limit':2}))
                    setup = setup.replace('HOST',json.dumps(host)).replace('BODY',json.dumps(body)).replace('OWNER',json.dumps(owner))
                    # Avoid colliding with the real script's platform binding.
                    setup = setup.replace('const platform = input.platform;', 'const fixturePlatform = input.platform;').replace("platform===", "fixturePlatform===")
                    result = subprocess.run(['node','--input-type=module','-e',setup+script],
                                            capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    payload=json.loads(result.stdout.strip().split(browser._MARKER)[-1])
                    self.assertEqual(payload['status'],status)
                    if status=='ok':
                        self.assertEqual(len(payload['items']),1)
                        self.assertEqual(payload['items'][0]['url'],url)
                        self.assertEqual(payload['items'][0]['title'],'真实标题')
                        if platform in ('taobao','tmall'):
                            self.assertEqual(payload['items'][0]['price'],'3195.16')
                            self.assertIn('国补后',payload['items'][0]['price_note'])

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
const page = {close:async()=>{},label:'p5',goto:async()=>{navigations++},waitForFunction:async()=>{},
 evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces = async()=>[{id:12,ownership:'agent'}];
const taskSpace = async()=>({newPage:async()=>page, pages:async()=>[page]});
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
