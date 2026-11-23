import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from research_mcp.channels import local_browser as b

class ParallelTests(unittest.TestCase):
    def test_distinct_platforms_overlap_and_limit(self):
        active = peak = 0
        guard = threading.Lock()
        def run(*args, **kwargs):
            nonlocal active, peak
            with guard: active += 1; peak = max(peak, active)
            time.sleep(.04)
            with guard: active -= 1
        with tempfile.TemporaryDirectory() as d, patch.object(b.Path,'home',return_value=Path(d)), patch.object(b.subprocess,'run',side_effect=run), patch.dict(os.environ,{'RESEARCH_BROWSER_CONCURRENCY':'2'}):
            with ThreadPoolExecutor(4) as ex:
                list(ex.map(lambda p:b._run(['ego-browser'],p),['jd','taobao','smzdm','xianyu']))
        self.assertEqual(peak,2)

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node required')
    def test_owned_tab_cleanup_even_on_navigation_and_cleanup_failure(self):
        import json
        import subprocess
        script=Path(b.__file__).with_suffix('.mjs').read_text()
        for failure,expected in [('navigation','startup_failed'),('barrier','blocked'),('cleanup','startup_failed')]:
            setup='''
const input={space:15,platform:'jd',query:'test',limit:1};
const failure=FAILURE;
let closed=false;
globalThis.location={hostname:'search.jd.com'};
globalThis.document={body:{innerText:'请完成验证'}};
const own={goto:async()=>{if(failure==='navigation')throw Error('navigation')},waitForFunction:async()=>{},evaluate:async(f,a)=>f(a),close:async()=>{closed=true;if(failure==='cleanup')throw Error('cleanup')}};
const listTaskSpaces=async()=>[{id:15,ownership:'agent'}];
const taskSpace=async()=>({newPage:async()=>own,pages:async()=>{throw Error('must not use user tabs')}});
'''.replace('FAILURE',json.dumps(failure))
            r=subprocess.run(['node','--input-type=module','-e',setup+script+'\nif(!closed)throw Error("leaked tab");'],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stderr)
            payload=json.loads(r.stdout.split(b._MARKER)[-1])
            self.assertEqual(payload['status'],expected)
