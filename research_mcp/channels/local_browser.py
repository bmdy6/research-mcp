"""Opt-in local Ego Lite search; never create a browser or export session state."""
from __future__ import annotations

from functools import partial
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock, BoundedSemaphore
import time
from urllib.parse import parse_qs, urlsplit

from .errors import ChannelError

# Only platforms whose result DOM was actually read belong here.
PLATFORMS = {
    'xianyu': {'login': True, 'kind': 'product', 'hosts': ('www.goofish.com',), 'path': r'/item', 'terms': 'https://www.goofish.com'},
    'weibo': {'login': True, 'kind': 'article', 'hosts': ('weibo.com',), 'path': r'/[0-9]+/[a-zA-Z0-9]+', 'terms': 'https://weibo.com'},
    'jd': {'login': True, 'kind': 'product', 'hosts': ('item.jd.com',), 'path': r'/[0-9]+\.html', 'terms': 'https://www.jd.com'},
    'taobao': {'login': True, 'kind': 'product', 'hosts': ('item.taobao.com', 'detail.tmall.com'), 'path': r'/item\.htm', 'terms': 'https://www.taobao.com'},
    'tmall': {'login': True, 'kind': 'product', 'hosts': ('detail.tmall.com',), 'path': r'/item\.htm', 'terms': 'https://www.tmall.com'},
    'smzdm': {'login': False, 'kind': 'article', 'hosts': ('www.smzdm.com', 'post.smzdm.com'), 'path': r'/p/[a-zA-Z0-9]+/', 'terms': 'https://www.smzdm.com'},
    'zhihu': {'login': True, 'kind': 'article', 'hosts': ('www.zhihu.com', 'zhuanlan.zhihu.com'), 'path': r'/(?:question/[0-9]+(?:/answer/[0-9]+)?|p/[0-9]+)', 'terms': 'https://www.zhihu.com'},
    'xiaohongshu': {'login': True, 'kind': 'article', 'hosts': ('www.xiaohongshu.com',), 'path': r'/explore/[a-f0-9]{24}', 'terms': 'https://www.xiaohongshu.com'},
    'douyin': {'login': True, 'kind': 'video', 'hosts': ('www.douyin.com',), 'path': r'/(?:video|note)/[0-9]+', 'terms': 'https://www.douyin.com'},
    'sogou_wechat': {'login': False, 'kind': 'article', 'hosts': ('weixin.sogou.com',), 'path': r'/link', 'terms': 'https://weixin.sogou.com'},
}
# Fixed public result routes, shared with the packaged DOM extractor.
PLATFORMS.update({'boss': {'login': True,
          'kind': 'job',
          'hosts': ['www.zhipin.com'],
          'path': '/job_detail/[A-Za-z0-9_-]+\\.html',
          'terms': 'https://www.zhipin.com',
          'search_url': 'https://www.zhipin.com/web/geek/job?query={query}',
          'selector': 'a.job-name[href*="/job_detail/"]'},
 'liepin': {'login': True,
            'kind': 'job',
            'hosts': ['www.liepin.com'],
            'path': '/job/[0-9]+\\.shtml',
            'terms': 'https://www.liepin.com',
            'search_url': 'https://www.liepin.com/zhaopin/?key={query}',
            'selector': 'a[href*="/job/"]'},
 'hupu': {'login': False,
          'kind': 'discussion',
          'hosts': ['bbs.hupu.com'],
          'path': '/[0-9]+\\.html',
          'terms': 'https://bbs.hupu.com',
          'search_url': 'https://bbs.hupu.com/search?q={query}',
          'selector': 'a.content-wrap-span[href$=".html"]'},
 'kr36': {'login': False,
          'kind': 'article',
          'hosts': ['36kr.com', 'www.36kr.com'],
          'path': '/p/[0-9]+',
          'terms': 'https://36kr.com',
          'search_url': 'https://36kr.com/search/articles/{query}',
          'selector': 'a.article-item-title'},
 'amazon': {'login': False,
            'kind': 'product',
            'hosts': ['www.amazon.com'],
            'path': '/dp/[A-Z0-9]{10}',
            'terms': 'https://www.amazon.com',
            'search_url': 'https://www.amazon.com/s?k={query}',
            'selector': '[data-component-type="s-search-result"] a:has(h2)',
            'dom_path': '/(?:[^/]+/)*dp/[A-Z0-9]{10}(?:/ref=[^/]+)?'},
 'douban': {'login': False,
            'kind': 'article',
            'hosts': ['www.douban.com'],
            'path': '/doubanapp/dispatch',
            'terms': 'https://www.douban.com',
            'search_url': 'https://www.douban.com/search?q={query}',
            'selector': 'a.DouWeb-SR-subject-info-name',
            'query_key': 'uri',
            'query_pattern': '/(?:movie|book|music)/[0-9]+'},
 'youtube': {'login': False,
             'kind': 'video',
             'hosts': ['www.youtube.com'],
             'path': '/watch',
             'terms': 'https://www.youtube.com',
             'search_url': 'https://www.youtube.com/results?search_query={query}',
             'selector': 'a.ytLockupMetadataViewModelTitle, a#video-title',
             'query_key': 'v',
             'query_pattern': '[A-Za-z0-9_-]{11}'},
 'x': {'login': True,
       'kind': 'discussion',
       'hosts': ['x.com'],
       'path': '/[A-Za-z0-9_]+/status/[0-9]+',
       'terms': 'https://x.com',
       'search_url': 'https://x.com/search?f=live&q={query}',
       'selector': 'article a[href*="/status/"]:has(time)'},
 'medium': {'login': False,
            'kind': 'article',
            'hosts': ['medium.com'],
            'path': '/(?:@[A-Za-z0-9_.-]+|[A-Za-z0-9_-]+)/[^/]+-[a-f0-9]{12}',
            'terms': 'https://medium.com',
            'search_url': 'https://medium.com/search?q={query}',
            'selector': 'main a:has(h2)',
            'title_selector': 'h2'},
 'devto': {'login': False,
           'kind': 'article',
           'hosts': ['dev.to'],
           'path': '/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+',
           'terms': 'https://dev.to',
           'search_url': 'https://dev.to/search?q={query}',
           'selector': 'a.crayons-story__hidden-navigation-link'},
 'segmentfault': {'login': False,
                  'kind': 'article',
                  'hosts': ['segmentfault.com'],
                  'path': '/(?:a|q)/[0-9]+',
                  'terms': 'https://segmentfault.com',
                  'search_url': 'https://segmentfault.com/search?q={query}',
                  'selector': 'a.link-dark[href*="/a/"], a.link-dark[href*="/q/"]'},
 'pypi': {'login': False,
          'kind': 'package',
          'hosts': ['pypi.org'],
          'path': '/project/[A-Za-z0-9_.-]+/',
          'terms': 'https://pypi.org',
          'search_url': 'https://pypi.org/search/?q={query}',
          'selector': 'a.package-snippet',
          'title_selector': '.package-snippet__name'},
 'gitee': {'login': False,
           'kind': 'repo',
           'hosts': ['gitee.com'],
           'path': '/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',
           'terms': 'https://gitee.com',
           'search_url': 'https://so.gitee.com/?q={query}',
           'selector': 'a.title[href^="https://gitee.com/"]'},
 'mafengwo': {'login': False,
              'kind': 'product',
              'hosts': ['www.mafengwo.cn'],
              'path': '/sales/[0-9]+\\.html',
              'terms': 'https://www.mafengwo.cn',
              'search_url': 'https://www.mafengwo.cn/search/q.php?q={query}',
              'selector': '.seg-desc a._j_search_link[href*="/sales/"]'},
 'sspai': {'login': False,
           'kind': 'article',
           'hosts': ['sspai.com'],
           'path': '/post/[0-9]+',
           'terms': 'https://sspai.com',
           'search_url': 'https://sspai.com/search?q={query}',
           'selector': 'a.module__list__slot[href*="/post/"]',
           'title_selector': '.post__title'},
 'suning': {'login': False,
            'kind': 'product',
            'hosts': ['product.suning.com'],
            'path': '/[0-9]+/[0-9]+\\.html',
            'terms': 'https://www.suning.com',
            'search_url': 'https://search.suning.com/{query}/',
            'selector': '.title-selling-point a[href*="product.suning.com/"]'},
 'huxiu': {'login': False,
           'kind': 'article',
           'hosts': ['www.huxiu.com'],
           'path': '/article/[0-9]+\\.html',
           'terms': 'https://www.huxiu.com',
           'search_url': 'https://www.huxiu.com/',
           'selector': '.result-article__title'},
 'lobsters': {'login': False,
              'kind': 'discussion',
              'hosts': ['lobste.rs'],
              'path': '/s/[a-z0-9]+/[a-z0-9_-]+',
              'terms': 'https://lobste.rs',
              'search_url': 'https://lobste.rs/search?what=stories&order=relevance&q={query}',
              'selector': '.story .comments_label a',
              'title_container': 'li.story',
              'title_selector': 'h2.link a'}})
PLATFORMS.update({
 'maimai': {'login': True, 'kind': 'discussion', 'hosts': ['maimai.cn'],
            'path': r'/community/feed-detail/[0-9]+', 'terms': 'https://maimai.cn',
            'search_url': 'https://maimai.cn/web/search_center?type=feed&query={query}',
            'selector': 'li.list-group-item p'},
 'dianping': {'login': True, 'kind': 'product', 'hosts': ['www.dianping.com'],
              'path': r'/shop/[A-Za-z0-9]+', 'terms': 'https://www.dianping.com',
              'search_url': 'https://www.dianping.com/search/keyword/1/0_{query}',
              'selector': '#shop-all-list .tit a:has(h4)'},
 'tieba': {'login': True, 'kind': 'discussion', 'hosts': ['tieba.baidu.com'],
           'path': r'/p/[0-9]+', 'terms': 'https://tieba.baidu.com',
           'search_url': 'https://tieba.baidu.com/f/search/res?ie=utf-8&qw={query}',
           'selector': '.thread-content-box a.item-link-bg'},
})
PRICE_NOTE = '页面标价，未含券/国补'
_MARKER = 'RESEARCH_BROWSER_JSON:'
_LOCK = Lock()
_SEMAPHORES = {}
_INTERVAL = 5.0


def _run(command: list[str], platform: str = 'legacy') -> subprocess.CompletedProcess:
    """Per-platform serialization across CLI processes; >=5s after completion.

    The file contains only a monotonic timestamp. The lock covers the complete
    browser operation; distinct platforms use independent new tabs.
    """
    try:
        import fcntl
    except ImportError:
        raise ChannelError('startup_failed') from None
    directory = Path.home() / '.cache' / 'research-mcp'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        concurrency = max(1, min(16, int(os.environ.get('RESEARCH_BROWSER_CONCURRENCY', '4'))))
    except ValueError:
        raise ChannelError('startup_failed') from None
    with _LOCK:
        semaphore = _SEMAPHORES.setdefault(concurrency, BoundedSemaphore(concurrency))
    # Tmall and Taobao share one upstream platform and throttle.
    throttle = 'taobao' if platform == 'tmall' else platform
    with semaphore, (directory / f'local-browser-{throttle}.lock').open('a+') as state:
        fcntl.flock(state, fcntl.LOCK_EX)
        state.seek(0)
        try:
            previous = float(state.read() or '0')
        except ValueError:
            previous = 0
        delay = previous + _INTERVAL - time.monotonic()
        if 0 < delay <= _INTERVAL:
            time.sleep(delay)
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=45)
        finally:
            state.seek(0)
            state.truncate()
            state.write(str(time.monotonic()))
            state.flush()


def search(config: dict, *, platform: str) -> list[dict]:
    query = config.get('web_query', '')
    if not query:
        return []
    executable = shutil.which('ego-browser')
    if not executable:
        raise ChannelError('startup_failed')
    space = os.environ.get('RESEARCH_BROWSER_SPACE_ID', '')
    if (os.environ.get('RESEARCH_LOCAL_BROWSER') != '1' or not space.isdecimal()):
        raise ChannelError('auth_required')
    script = Path(__file__).with_suffix('.mjs').read_text(encoding='utf-8')
    arguments = {'definition': PLATFORMS[platform], 'space': int(space), 'platform': platform,
                 'query': query, 'limit': min(40, max(1, int(config.get('web_limit', 10))))}
    # Argument vector, never a shell. JSON remains data, including quotes/newlines.
    script = 'const input = ' + json.dumps(arguments, ensure_ascii=True) + ';\n' + script
    try:
        result = _run([executable, 'nodejs', '-e', script], platform)
    except OSError:
        raise ChannelError('startup_failed') from None
    # Ego Lite 2.2.1 sends script console output to stderr; support both streams.
    output = result.stdout + '\n' + result.stderr
    lines = [line[len(_MARKER):] for line in output.splitlines() if line.startswith(_MARKER)]
    if not lines:
        # Browser-owned prompts transfer control and often exit successfully.
        if 'user now controls' in output.lower() or 'permission prompt' in output.lower():
            raise ChannelError('auth_required')
        raise ChannelError('startup_failed' if result.returncode else 'parse_error')
    try:
        payload = json.loads(lines[-1])
        status = payload['status']
        if status == 'empty' and payload.get('items') == []:
            return []
        if status != 'ok':
            raise ChannelError(status if status in {'blocked', 'auth_required', 'startup_failed',
                                                   'timeout', 'parse_error'} else 'parse_error')
        rows = payload['items']
        if not isinstance(rows, list) or not rows:
            raise ValueError
        items = []
        for row in rows[:arguments['limit']]:
            if not isinstance(row, dict):
                raise ValueError
            url = row.get('url', '')
            parsed = urlsplit(url)
            definition = PLATFORMS[platform]
            params = parse_qs(parsed.query, keep_blank_values=True)
            expected_query = ('id' if platform in {'taobao', 'tmall', 'xianyu'} else
                              'url' if platform == 'sogou_wechat' else definition.get('query_key'))
            if (parsed.scheme != 'https' or parsed.hostname not in definition['hosts']
                    or parsed.username or parsed.password or parsed.port or parsed.fragment
                    or not re.fullmatch(definition['path'], parsed.path)
                    or (expected_query is None and parsed.query)
                    or (expected_query and (set(params) != {expected_query}
                        or len(params[expected_query]) != 1 or not params[expected_query][0]))
                    or (expected_query and definition.get('query_pattern') and not re.fullmatch(
                        definition['query_pattern'], params[expected_query][0]))
                    or (expected_query == 'id' and not params['id'][0].isdecimal())):
                raise ValueError
            if not isinstance(row.get('title'), str) or not row['title'].strip():
                raise ValueError
            price = row.get('price')
            if ((platform == 'jd' or price is not None) and
                    (not isinstance(price, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', price))):
                raise ValueError
            item = {'kind': definition['kind'], 'title': row['title'][:1000],
                          'url': url, 'source': platform, 'published_at': None,
                          'author': str(row.get('author', ''))[:300],
                          'summary': str(row.get('summary', ''))[:1000],
                          'actual_backend': 'ego-browser/local_browser'}
            if price is not None:
                item.update(price=price[:80], price_note=PRICE_NOTE if platform == 'jd' else
                            str(row.get('price_note', '页面展示价，优惠条件请回原页核对'))[:160])
            if platform == 'xianyu':
                for key in ('want_count', 'region'):
                    if isinstance(row.get(key), str) and row[key]:
                        item[key] = row[key][:100]
            items.append(item)
        return items
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ChannelError('parse_error') from None


PROVIDERS = {name: partial(search, platform=name) for name in PLATFORMS}
