"""Offline DOM/link contracts for the round-six local browser increment."""
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from research_mcp import service
from research_mcp.channels import local_browser as browser
import test_local_browser


class RoundSixBrowserTests(unittest.TestCase):
    call = test_local_browser.LocalBrowserTests.call

    @unittest.skipUnless(shutil.which('node'), 'Node required for DOM fixtures')
    def test_result_links_and_host_boundaries(self):
        examples = {
            'lobsters': ('https://lobste.rs/s/abc123/python_test?utm=secret','https://lobste.rs/s/abc123/python_test'),
            'sspai': ('https://sspai.com/post/123?utm=secret','https://sspai.com/post/123'),
            'mafengwo': ('http://www.mafengwo.cn/sales/123.html?utm=secret','https://www.mafengwo.cn/sales/123.html'),
            'suning': ('https://product.suning.com/0001/123.html?utm=secret','https://product.suning.com/0001/123.html'),
            'gitee': ('https://gitee.com/example/repo?utm=secret','https://gitee.com/example/repo'),
            'devto': ('https://dev.to/example/python-intro?utm=secret','https://dev.to/example/python-intro'),
            'segmentfault': ('https://segmentfault.com/a/123?utm=secret','https://segmentfault.com/a/123'),
            'pypi': ('https://pypi.org/project/example/?utm=secret','https://pypi.org/project/example/'),
            'boss': ('https://www.zhipin.com/job_detail/abc_123.html?securityId=secret', 'https://www.zhipin.com/job_detail/abc_123.html'),
            'liepin': ('https://www.liepin.com/job/123.shtml?pgRef=secret', 'https://www.liepin.com/job/123.shtml'),
            'hupu': ('https://bbs.hupu.com/123.html?tracking=secret', 'https://bbs.hupu.com/123.html'),
            'kr36': ('https://36kr.com/p/123?tracking=secret', 'https://36kr.com/p/123'),
            'amazon': ('https://www.amazon.com/-/zh/book/dp/B012345678/ref=secret?tag=secret', 'https://www.amazon.com/dp/B012345678'),
            'douban': ('https://www.douban.com/doubanapp/dispatch?uri=/book/123&tracking=secret', 'https://www.douban.com/doubanapp/dispatch?uri=%2Fbook%2F123'),
            'youtube': ('https://www.youtube.com/watch?v=abcDE_12345&pp=secret', 'https://www.youtube.com/watch?v=abcDE_12345'),
            'x': ('https://x.com/example/status/123?tracking=secret', 'https://x.com/example/status/123'),
            'medium': ('https://medium.com/@example/article-abcdef123456?source=secret', 'https://medium.com/@example/article-abcdef123456'),
        }
        script = browser.Path(browser.__file__).with_suffix('.mjs').read_text()
        for name, (raw, canonical) in examples.items():
            definition = browser.PLATFORMS[name]
            setup = '''
const input=INPUT;
globalThis.location={hostname:new URL(input.definition.search_url).hostname};
const child={innerText:'Search result title text'};
const card={innerText:'Search result title text',href:RAW,querySelector:()=>child,closest:()=>({querySelector:()=>child})};
globalThis.document={body:{innerText:''},querySelectorAll:()=>[{...card,innerText:'Details'},card,card,{...card,href:'https://evil.invalid/login'},{...card,href:'https://'+input.definition.hosts[0]+'/login'}]};
const page={label:'p5',goto:async url=>{if(!url.includes(encodeURIComponent(input.query)))throw Error('query lost')},waitForFunction:async()=>{},evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces=async()=>[{id:12,ownership:'agent'}];
const taskSpace=async()=>({pages:async()=>[page]});
'''.replace('INPUT', json.dumps(dict(space=12, page='p5', platform=name, query='人工智能 & python', limit=2, definition=definition))).replace('RAW', json.dumps(raw))
            with self.subTest(platform=name):
                run = subprocess.run(['node', '--input-type=module', '-e', setup+script], capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                payload = json.loads(run.stdout.split(browser._MARKER)[-1])
                self.assertEqual(payload['status'], 'ok')
                self.assertEqual([r['url'] for r in payload['items']], [canonical])
                self.assertEqual(payload['items'][0]['title'], 'Search result title text')
                if name == 'suning':
                    challenge = setup.replace("body:{innerText:''}", "body:{innerText:'亲，请拖动下方滑块完成验证'}")
                    blocked = subprocess.run(['node','--input-type=module','-e',challenge+script],capture_output=True,text=True,timeout=10)
                    self.assertEqual(json.loads(blocked.stdout.split(browser._MARKER)[-1])['status'],'blocked')
                accepted = self.call(payload, provider=name)
                self.assertEqual(accepted['status'], 'ok')
                self.assertNotIn('secret', json.dumps(accepted))
                for bad in (raw, canonical+'#private', canonical.replace('https://', 'https://user:password@')):
                    rejected = self.call({'status':'ok','items':[{'title':'Result', 'url':bad}]}, provider=name)
                    self.assertEqual(rejected['failed_channels'][0]['status'], 'parse_error')
                with patch.object(browser, '_run') as runtime:
                    self.assertFalse(service.search_public('python', providers=[name])['ok'])
                runtime.assert_not_called()

    def test_query_parameter_validation(self):
        for name, url in [('youtube','https://www.youtube.com/watch?v=bad'),
                          ('douban','https://www.douban.com/doubanapp/dispatch?uri=%2Fuser%2F123')]:
            result = self.call({'status':'ok','items':[{'title':'Result','url':url}]}, provider=name)
            self.assertEqual(result['failed_channels'][0]['status'], 'parse_error')

    @unittest.skipUnless(shutil.which('node'), 'Node required for DOM fixtures')
    def test_huxiu_read_only_popups_are_closed_and_bounded(self):
        script = browser.Path(browser.__file__).with_suffix('.mjs').read_text()
        setup = '''
const input=INPUT;
let closes=0,clicks=0;
globalThis.document={querySelectorAll:()=>[{innerText:'Article title'},{innerText:'Second article'}]};
const detail={waitForURL:async()=>{},url:async()=> 'https://www.huxiu.com/article/123.html?utm=private',close:async()=>{closes++}};
const page={label:'p5',waitForEvent:async()=>detail,goto:async()=>{},fill:async(s,q)=>{if(q!==input.query)throw Error('query changed')},click:async s=>{if(s.includes('nth=')){clicks++;return {popups:[{label:'p6'}]}}return {}},waitForSelector:async()=>{},waitForFunction:async()=>{},evaluate:async(fn,arg)=>fn(arg)};
const listTaskSpaces=async()=>[{id:12,ownership:'agent'}];
const taskSpace=async()=>({pages:async()=>[page],page:()=>detail});
'''.replace('INPUT',json.dumps(dict(space=12,page='p5',platform='huxiu',query='人工智能 & python',limit=40,definition=browser.PLATFORMS['huxiu'])))
        run = subprocess.run(['node','--input-type=module','-e',setup+script+'\nconsole.log(JSON.stringify({closes,clicks}));'],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        payload=json.loads(run.stdout.split(browser._MARKER)[-1].splitlines()[0])
        self.assertEqual(payload['status'],'ok')
        self.assertEqual(payload['items'][0]['url'],'https://www.huxiu.com/article/123.html')
        self.assertEqual(self.call(payload,provider='huxiu')['status'],'ok')
        with patch.object(browser,'_run') as runtime:
            self.assertFalse(service.search_public('python',providers=['huxiu'])['ok'])
        runtime.assert_not_called()
        self.assertEqual(json.loads(run.stdout.splitlines()[-1]),dict(closes=2,clicks=2))

        failed_setup = setup.replace("waitForURL:async()=>{}", "waitForURL:async()=>{throw Error('timeout')}")
        failed = subprocess.run(['node','--input-type=module','-e',failed_setup+script+'\nconsole.log(JSON.stringify({closes,clicks}));'],capture_output=True,text=True,timeout=10)
        payload=json.loads(failed.stdout.split(browser._MARKER)[-1].splitlines()[0])
        self.assertEqual(payload['status'],'timeout')
        self.assertEqual(json.loads(failed.stdout.splitlines()[-1]),dict(closes=1,clicks=1))
