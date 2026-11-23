import json
import os
import subprocess
import unittest
from unittest.mock import patch
from research_mcp import login_status as module
from research_mcp.channels.http_search import search, Results

class LoginTests(unittest.TestCase):
    def test_opt_in_required(self):
        with patch.dict(os.environ,{},clear=True): self.assertFalse(module.login_status()['ok'])
    def test_unknown_does_not_mean_expired_and_errors_are_sanitized(self):
        def run(command, platform):
            if platform == 'jd': raise RuntimeError('SECRET')
            status = 'expired' if platform == 'vip' else 'unknown'
            return subprocess.CompletedProcess([],0,'RESEARCH_LOGIN_JSON:'+json.dumps([{'channel':platform,'status':status}]),'')
        with patch.dict(os.environ,{'RESEARCH_LOCAL_BROWSER':'1','RESEARCH_BROWSER_SPACE_ID':'15'}), patch.object(module.shutil,'which',return_value='/bin/ego-browser'), patch.object(module,'_run',side_effect=run): r=module.login_status()
        self.assertEqual(r['expired'],['vip']);self.assertNotIn('SECRET',json.dumps(r))
    def test_http_only_accepts_result_hosts_and_no_browser(self):
        html=b'<a href="//product.suning.com/001/123.html?utm=x">Laptop</a><a href="https://evil.com/001/123.html">bad</a>\n200'
        with patch('research_mcp.channels.http_search.subprocess.run',return_value=subprocess.CompletedProcess([],0,html,b'')) as run:
            rows=search({'web_query':'a & b','web_limit':2})
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['url'],'https://product.suning.com/001/123.html')
        self.assertNotIn('--cookie',run.call_args.args[0]);self.assertEqual(run.call_args.args[0][0],'curl')
    def test_anonymous_article_parsers_keep_only_canonical_links(self):
        examples=[('sogou_wechat','<h3><a href="/link?url=public-value&token=private">Article<em> title</em></a></h3>', 'https://weixin.sogou.com/link?url=public-value'),('segmentfault','<a href="/a/123?track=private">Article</a>','https://segmentfault.com/a/123')]
        for name,html,url in examples:
            p=Results(name);p.feed(html)
            self.assertEqual(list(p.rows),[url]);self.assertNotIn('private',str(p.rows))
