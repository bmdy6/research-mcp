"""Inspect visible account markers; never read credentials, cookies or storage."""
import json
import os
from pathlib import Path
import shutil
from research_mcp.channels.local_browser import PLATFORMS, _run

# Positive markers are intentionally narrow. Missing markers mean unknown.
MARKERS = {
    'maimai': 'a[href^="/profile/detail?dstu="]',
    'dianping': 'a[href*="/member/"]',
    'tieba': 'a[href*="/home/main?id="]',
    'jd': 'a[href*="logout"]', 'taobao': '.site-nav-user', 'tmall': '.site-nav-user',
    'weibo': 'a[href*="logout"]', 'xianyu': '[class*="user-name"]',
    'zhihu': '.AppHeader-profile', 'xiaohongshu': 'li.user a[href*="/user/profile/"]',
    'douyin': '[data-e2e="user-avatar"]', 'boss': '.nav-figure-con',
    'liepin': '.user-info', 'x': '[data-testid="SideNav_AccountSwitcher_Button"]',
}

def login_status():
    space = os.environ.get('RESEARCH_BROWSER_SPACE_ID','')
    exe = shutil.which('ego-browser')
    if os.environ.get('RESEARCH_LOCAL_BROWSER') != '1' or not space.isdecimal() or not exe:
        return {'ok':False,'operation':'login-status','error':'需要启用本机浏览器并指定受控空间'}
    rows = []
    definitions = {name: (d['terms'],MARKERS.get(name,'')) for name,d in PLATFORMS.items() if d['login']}
    definitions['maimai'] = ('https://maimai.cn/web/search_center?type=feed&query=AI',MARKERS['maimai'])
    definitions['dianping'] = ('https://www.dianping.com/search/keyword/1/0_%E5%92%96%E5%95%A1',MARKERS['dianping'])
    definitions.update(vip=('https://category.vip.com/suggest.php?keyword=AI', ''), nga=('https://bbs.nga.cn/thread.php?key=AI',''))
    # Each platform uses its own limiter, also shared with searches.
    for name,(url,yes) in definitions.items():
        args = {'space':int(space),'platforms':[{'channel':name,'url':url,'yes':yes}]}
        script = 'const input = '+json.dumps(args)+';\n'+Path(__file__).with_suffix('.mjs').read_text()
        try:
            r = _run([exe,'nodejs','-e',script],name)
            lines=[x.split('RESEARCH_LOGIN_JSON:',1)[1] for x in (r.stdout+'\n'+r.stderr).splitlines() if x.startswith('RESEARCH_LOGIN_JSON:')]
            data=json.loads(lines[-1]); row=data[0]
            if row['status'] not in {'logged_in','expired','blocked','unknown','restricted'}: raise ValueError
            rows.append(row)
        except Exception:
            rows.append({'channel':name,'status':'unknown'})
    return {'ok':True,'operation':'login-status','platforms':rows,
            'expired':[r['channel'] for r in rows if r['status']=='expired'],
            'needs_attention':[r['channel'] for r in rows if r['status']!='logged_in']}
