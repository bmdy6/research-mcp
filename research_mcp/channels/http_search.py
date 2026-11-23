"""Anonymous search pages verified to contain server-rendered result links."""
from html.parser import HTMLParser
import re
import subprocess
from urllib.parse import quote, urljoin, urlsplit, parse_qs
from functools import partial
from .local_browser import PLATFORMS
from .errors import ChannelError

HTTP_PLATFORMS = {'suning', 'sogou_wechat', 'segmentfault'}

class Results(HTMLParser):
    def __init__(self, platform="suning"):
        super().__init__(); self.rows = {}; self.current = None; self.platform = platform; self.heading = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'h3': self.heading = True
        if tag == 'a':
            d = PLATFORMS[self.platform]
            u = urlsplit(urljoin(d['terms'], a.get('href','')))
            self.current = None
            if self.platform == 'sogou_wechat' and not self.heading: return
            if u.hostname in d['hosts'] and re.fullmatch(d['path'],u.path):
                url = 'https://'+u.hostname+u.path
                if self.platform == 'sogou_wechat':
                    target = parse_qs(u.query).get('url',[''])[0]
                    if not target: return
                    url += '?url='+quote(target,safe='')
                self.current = url
                self.current_text = a.get('title','')
    def handle_data(self, data):
        if self.current: self.current_text += data
    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            text = ' '.join(self.current_text.split())
            if len(text) > len(self.rows.get(self.current,'')): self.rows[self.current] = text
            self.current = None
        if tag == 'h3': self.heading = False

def search(config, *, platform="suning"):
    q = config.get('web_query','')
    if not q: return []
    routes = {'suning':'https://search.suning.com/{query}/', 'sogou_wechat':'https://weixin.sogou.com/weixin?type=2&query={query}', 'segmentfault':'https://segmentfault.com/search?q={query}'}
    url = routes[platform].replace('{query}',quote(q,safe=''))
    try:
        r = subprocess.run(['curl','--silent','--show-error','--location','--max-time','20',
                            '--max-filesize','5000000','--proto','=https','--proto-redir','=https',
                            '-A','Mozilla/5.0',url,
                            '-w','\n%{http_code}'],capture_output=True,timeout=23)
    except FileNotFoundError:
        raise ChannelError('startup_failed') from None
    if r.returncode: raise ChannelError('timeout' if r.returncode == 28 else 'blocked')
    html, _, status = r.stdout.rpartition(b'\n')
    if status != b'200': raise ChannelError('blocked')
    parser = Results(platform); parser.feed(html.decode('utf-8','replace'))
    items = [{'title':' '.join(t.split())[:1000], 'url':u, 'source':platform, 'kind':PLATFORMS[platform]['kind'],
              'actual_backend':'http/'+urlsplit(url).hostname, 'published_at':None, 'summary':''}
             for u,t in parser.rows.items() if t.strip()]
    if not items: raise ChannelError('parse_error')
    return items[:min(40,max(1,int(config.get('web_limit',10))))]

PROVIDERS = {name: partial(search, platform=name) for name in HTTP_PLATFORMS}
