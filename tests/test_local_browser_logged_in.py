"""Offline fixtures for the logged-in DOM; no account data or browser requests."""
import json
import shutil
import subprocess
import unittest

from research_mcp.channels import local_browser as browser
import test_local_browser


class LoggedInBrowserTests(unittest.TestCase):
    call = test_local_browser.LocalBrowserTests.call

    def test_new_provider_contract_and_public_isolation(self):
        for platform, url in [('xianyu', 'https://www.goofish.com/item?id=123'),
                              ('weibo', 'https://weibo.com/123/AbC123')]:
            row = {'title': '公开结果', 'url': url, 'summary': '摘要'}
            if platform == 'xianyu':
                row.update(price='36.50', price_note='页面标价，非成交价', want_count='1.2万', region='四川')
            result = self.call({'status': 'ok', 'items': [row]}, provider=platform)
            self.assertEqual(result['status'], 'ok')
            if platform == 'xianyu':
                item = result['candidates'][0]
                for key in ('price', 'price_note', 'want_count', 'region'):
                    self.assertEqual(item[key], row[key])
            with unittest.mock.patch.object(browser, '_run') as run:
                from research_mcp import service
                self.assertFalse(service.search_public('电脑', providers=[platform])['ok'])
                run.assert_not_called()
            for bad_url in (url+'&tracking=secret' if '?' in url else url+'?token=secret',
                            url.replace('https://', 'https://user:password@')):
                self.assertEqual(self.call({'status':'ok','items':[dict(row,url=bad_url)]},
                                          provider=platform)['failed_channels'][0]['status'], 'parse_error')

    def test_catalog_defaults_and_missing_optional_fields(self):
        from research_mcp.channel_catalog import list_channels
        from research_mcp.research import default_search_providers
        rows = {row['id']: row for row in list_channels()}
        for platform in ('xianyu', 'weibo'):
            self.assertEqual(rows[platform]['cost_class'], 'local_browser')
            self.assertTrue(rows[platform]['requires_login'])
            self.assertNotIn(platform, default_search_providers())
        result = self.call({'status':'ok', 'items':[{'title':'键盘',
                           'url':'https://www.goofish.com/item?id=123', 'price':'59'}]},
                           provider='xianyu')
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('want_count', result['candidates'][0])
        self.assertNotIn('region', result['candidates'][0])

    @unittest.skipUnless(shutil.which('node'), 'Node needed for DOM fixture')
    def test_actual_javascript_logged_in_cards(self):
        script = browser.Path(browser.__file__).with_suffix('.mjs').read_text()
        fixture = r'''
const input = INPUT;
const node=(text='',attrs={},children={})=>({innerText:text,...attrs,
 getAttribute:k=>attrs[k]||null,querySelector:s=>children[s]||null});
const item=node('',{href:'https://www.goofish.com/item?id=123&categoryId=private'}, {
 '[class*="row1-wrap-title"]':node('',{title:'机械键盘'}),
 '[class*="price-wrap"]':node('¥\n36\n.50'),
 '[class*="price-desc"]':node('1.2万人想要'),
 '[class*="seller-text"]':node('四川')});
const post=(ad=false)=>node(ad?'广告\n推荐':'普通微博内容',{}, {
 '.from a':node('今天 10:22',{href:'https://weibo.com/123/AbC123?refer_flag=tracking'}),
 '.name':node('公开作者'),
 '[node-type="feed_list_content_full"]':node('完整公开正文：讨论广告技术'),
 '.card-top':node(ad?'广告':'热门')});
const cards=MODE==='ads'?[post(true)]:[post(true),post(),post()];
globalThis.location={hostname:input.platform==='weibo'?'s.weibo.com':'www.goofish.com'};
globalThis.document={body:{innerText:BODY},querySelectorAll:s=>input.platform==='xianyu'?[item,item]:cards};
const page={label:'p5',goto:async u=>{if(!u.includes(encodeURIComponent(input.query)))throw Error('encoding')},
 waitForFunction:async()=>{},evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces=async()=>[{id:12,ownership:'agent'}];
const taskSpace=async()=>({pages:async()=>[page]});
'''
        for platform in ('xianyu', 'weibo'):
            for body, mode, status in [('', '', 'ok'), ('请先登录', '', 'auth_required'),
                                        ('安全验证', '', 'blocked'), ('', 'ads', 'parse_error')]:
                if platform=='xianyu' and mode=='ads':
                    continue
                setup=fixture.replace('INPUT',json.dumps(dict(space=12,page='p5',platform=platform,
                                                              query='键盘 & laptop',limit=3)))
                setup=setup.replace('BODY',json.dumps(body)).replace('MODE',json.dumps(mode))
                result=subprocess.run(['node','--input-type=module','-e',setup+script],
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                payload=json.loads(result.stdout.strip().split(browser._MARKER)[-1])
                self.assertEqual(payload['status'],status)
                if status=='ok':
                    self.assertEqual(len(payload['items']),1)
                    item=payload['items'][0]
                    self.assertNotIn('tracking',item['url'])
                    if platform=='xianyu':
                        self.assertEqual(item['price'],'36.50')
                        self.assertEqual(item['want_count'],'1.2万')
                        self.assertEqual(item['region'],'四川')
                    else:
                        self.assertEqual(item['summary'],'完整公开正文：讨论广告技术')
                        self.assertEqual(item['author'],'公开作者')
