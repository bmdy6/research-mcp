"""Opt-in local Ego Lite search; never create a browser or export session state."""
from __future__ import annotations

from functools import partial
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit

from .errors import ChannelError

# Only platforms whose result DOM was actually read belong here.
PLATFORMS = {
    'jd': {'login': True, 'kind': 'product', 'hosts': ('item.jd.com',), 'path': r'/[0-9]+\.html', 'terms': 'https://www.jd.com'},
    'taobao': {'login': True, 'kind': 'product', 'hosts': ('item.taobao.com', 'detail.tmall.com'), 'path': r'/item\.htm', 'terms': 'https://www.taobao.com'},
    'tmall': {'login': True, 'kind': 'product', 'hosts': ('detail.tmall.com',), 'path': r'/item\.htm', 'terms': 'https://www.tmall.com'},
    'smzdm': {'login': False, 'kind': 'article', 'hosts': ('www.smzdm.com', 'post.smzdm.com'), 'path': r'/p/[a-zA-Z0-9]+/', 'terms': 'https://www.smzdm.com'},
    'zhihu': {'login': True, 'kind': 'article', 'hosts': ('www.zhihu.com', 'zhuanlan.zhihu.com'), 'path': r'/(?:question/[0-9]+(?:/answer/[0-9]+)?|p/[0-9]+)', 'terms': 'https://www.zhihu.com'},
    'xiaohongshu': {'login': True, 'kind': 'article', 'hosts': ('www.xiaohongshu.com',), 'path': r'/explore/[a-f0-9]{24}', 'terms': 'https://www.xiaohongshu.com'},
    'douyin': {'login': True, 'kind': 'video', 'hosts': ('www.douyin.com',), 'path': r'/(?:video|note)/[0-9]+', 'terms': 'https://www.douyin.com'},
    'sogou_wechat': {'login': False, 'kind': 'article', 'hosts': ('weixin.sogou.com',), 'path': r'/link', 'terms': 'https://weixin.sogou.com'},
}
PRICE_NOTE = '页面标价，未含券/国补'
_MARKER = 'RESEARCH_BROWSER_JSON:'
_LOCK = Lock()
_INTERVAL = 5.0


def _run(command: list[str]) -> subprocess.CompletedProcess:
    """Serialize even across CLI processes; >=5s after each completed attempt.

    The file contains only a monotonic timestamp. The lock covers the complete
    browser operation so concurrent providers cannot navigate the same tab.
    """
    try:
        import fcntl
    except ImportError:
        raise ChannelError('startup_failed') from None
    directory = Path.home() / '.cache' / 'research-mcp'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _LOCK, (directory / 'local-browser.lock').open('a+') as state:
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
    page = os.environ.get('RESEARCH_BROWSER_PAGE', '')
    if (os.environ.get('RESEARCH_LOCAL_BROWSER') != '1' or not space.isdecimal()
            or not page.startswith('p') or not page[1:].isdecimal()):
        raise ChannelError('auth_required')
    script = Path(__file__).with_suffix('.mjs').read_text(encoding='utf-8')
    arguments = {'space': int(space), 'page': page, 'platform': platform,
                 'query': query, 'limit': min(40, max(1, int(config.get('web_limit', 10))))}
    # Argument vector, never a shell. JSON remains data, including quotes/newlines.
    script = 'const input = ' + json.dumps(arguments, ensure_ascii=True) + ';\n' + script
    try:
        result = _run([executable, 'nodejs', '-e', script])
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
            expected_query = ('id' if platform in {'taobao', 'tmall'} else
                              'url' if platform == 'sogou_wechat' else None)
            if (parsed.scheme != 'https' or parsed.hostname not in definition['hosts']
                    or parsed.username or parsed.password or parsed.port or parsed.fragment
                    or not re.fullmatch(definition['path'], parsed.path)
                    or (expected_query is None and parsed.query)
                    or (expected_query and (set(params) != {expected_query}
                        or len(params[expected_query]) != 1 or not params[expected_query][0]))
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
            items.append(item)
        return items
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ChannelError('parse_error') from None


PROVIDERS = {name: partial(search, platform=name) for name in PLATFORMS}
