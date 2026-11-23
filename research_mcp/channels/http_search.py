"""Anonymous HTTP providers. curl uses system trust, never browser session state."""
from dataclasses import dataclass
from functools import partial
from html.parser import HTMLParser
import json
import re
import subprocess
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from .local_browser import PLATFORMS
from .errors import ChannelError

HTTP_PLATFORMS = frozenset({'suning', 'sogou_wechat', 'segmentfault',
                            'youtube', 'douban', 'hupu', 'devto'})
BACKENDS = {name: 'http/' + urlsplit(PLATFORMS[name]['terms']).hostname for name in HTTP_PLATFORMS}
BACKENDS['suning'] = 'http/search.suning.com'
BACKENDS['devto'] = 'http/dev.to/api/articles (exact tag)'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
# These are public category pages, not private/internal APIs. The all-results
# Douban page can be a JS shell; keep the existing book/movie/music scope.
DOUBAN_CATEGORIES = ('1001', '1002', '1003')
EMPTY_TEXT = re.compile(r'没有找到|没有搜到|未找到相关|No results found|検索結果はありません', re.I)


@dataclass(frozen=True)
class Response:
    text: str
    http_status: int
    final_url: str
    curl_exit_code: int

    @property
    def failure(self):
        if self.curl_exit_code:
            return 'timeout' if self.curl_exit_code == 28 else 'connection_failed'
        if self.http_status == 401:
            return 'auth_required'
        if self.http_status == 429:
            return 'quota_exhausted'
        if self.http_status in {403, 405, 451}:
            return 'blocked'
        if self.http_status != 200:
            return 'connection_failed'
        host = urlsplit(self.final_url).hostname or ''
        path = urlsplit(self.final_url).path
        if host.startswith(('accounts.', 'passport.', 'login.', 'consent.')) or re.search(r'/(?:login|signin)(?:/|$)', path):
            return 'auth_required'
        return None


def fetch(url, *, timeout=20):
    """Bounded, TLS-verified anonymous GET, shared by providers and probe.

    -q must be first: ignore user curlrc cookies/credentials/insecure options.
    Return HTTP and transport status independently; never expose stderr.
    """
    try:
        result = subprocess.run([
            'curl', '-q', '--silent', '--show-error', '--location', '--compressed',
            '--max-time', str(timeout), '--max-filesize', '5000000', '--max-redirs', '5',
            '--proto', '=https', '--proto-redir', '=https', '-A', USER_AGENT,
            url, '-w', '\n%{http_code}\n%{url_effective}',
        ], capture_output=True, timeout=timeout + 3)
    except OSError:
        raise ChannelError('startup_failed') from None
    except subprocess.TimeoutExpired:
        raise ChannelError('timeout') from None
    body, status, effective = b'', b'0', b''
    parts = result.stdout.rsplit(b'\n', 2)
    if len(parts) == 3:
        body, status, effective = parts
    try:
        code = int(status)
    except ValueError:
        code = 0
    return Response(body.decode('utf-8', 'replace'), code,
                    effective.decode('utf-8', 'replace'), result.returncode)


def request_urls(platform, query, limit=10):
    q = quote(query, safe='')
    if platform == 'douban':
        return [f'https://www.douban.com/search?cat={cat}&q={q}' for cat in DOUBAN_CATEGORIES]
    if platform == 'devto':
        # /api/articles does NOT implement a full-text q parameter.
        return [f'https://dev.to/api/articles?tag={quote(query.lstrip("#").casefold(), safe="")}&per_page={limit}']
    if platform == 'sogou_wechat':
        return [f'https://weixin.sogou.com/weixin?type=2&query={q}']
    return [PLATFORMS[platform]['search_url'].replace('{query}', q)]


def _canonical(platform, raw):
    """Only emit known public result routes; discard tracking, never follow links."""
    try:
        u = urlsplit(urljoin(PLATFORMS[platform]['terms'], raw))
        if u.scheme not in {'http', 'https'} or u.username or u.password or u.port:
            return None
        params = parse_qs(u.query)
        if platform == 'douban':
            if u.hostname == 'www.douban.com' and u.path == '/link2/':
                targets = params.get('url', [])
                return _canonical(platform, targets[0]) if len(targets) == 1 and urlsplit(targets[0]).hostname in {
                    'book.douban.com', 'movie.douban.com', 'music.douban.com'} else None
            if u.hostname in {'book.douban.com', 'movie.douban.com', 'music.douban.com'} and re.fullmatch(r'/subject/[0-9]+/', u.path):
                return f'https://{u.hostname}{u.path}'
        definition = PLATFORMS[platform]
        if u.hostname not in definition['hosts'] or not re.fullmatch(definition['path'], u.path):
            return None
        url = f'https://{u.hostname}{u.path}'
        key = 'url' if platform == 'sogou_wechat' else definition.get('query_key')
        if key:
            values = params.get(key, [])
            if len(values) != 1 or not values[0]:
                return None
            if definition.get('query_pattern') and not re.fullmatch(definition['query_pattern'], values[0]):
                return None
            url += '?' + key + '=' + quote(values[0], safe='')
        return url
    except (ValueError, TypeError):
        return None


class Results(HTMLParser):
    """Search anchors only; Hupu excludes sidebar links, Douban uses headings."""
    def __init__(self, platform='suning'):
        super().__init__()
        self.rows = {}
        self.current = None
        self.current_text = ''
        self.platform = platform
        self.heading = False
        self.visible = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag == 'h3':
            self.heading = True
        if tag == 'a':
            self.current = None
            self.current_text = ''
            if self.platform in {'sogou_wechat', 'douban'} and not self.heading:
                return
            if self.platform == 'hupu' and 'content-wrap-span' not in a.get('class', '').split():
                return
            self.current = _canonical(self.platform, a.get('href', ''))
            self.current_text = a.get('title', '')

    def handle_data(self, data):
        if not self.hidden:
            self.visible.append(data)
            if self.current:
                self.current_text += data

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'a' and self.current:
            title = ' '.join(self.current_text.split())
            if len(title) > len(self.rows.get(self.current, '')):
                self.rows[self.current] = title
            self.current = None
        if tag == 'h3':
            self.heading = False


def _row(platform, title, url, **extra):
    return {'title': ' '.join(title.split())[:1000], 'url': url, 'source': platform,
            'kind': PLATFORMS[platform]['kind'], 'actual_backend': BACKENDS[platform],
            'published_at': None, 'summary': '', **extra}


def _walk(value, key):
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for child in value.values():
            yield from _walk(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _text(value):
    if not isinstance(value, dict):
        return ''
    if isinstance(value.get('simpleText'), str):
        return value['simpleText']
    return ''.join(r.get('text', '') for r in value.get('runs', []) if isinstance(r, dict) and isinstance(r.get('text'), str))


def _youtube(html):
    assignment = re.search(r'(?:\b(?:var\s+)?ytInitialData\s*=|window\["ytInitialData"\]\s*=)\s*', html)
    if not assignment:
        raise ValueError
    data, _ = json.JSONDecoder().raw_decode(html[assignment.end():])
    primary = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']
    rows = {}
    for video in _walk(primary, 'videoRenderer'):
        if not isinstance(video, dict):
            continue
        video_id = video.get('videoId', '')
        title = _text(video.get('title'))
        if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or not title.strip():
            continue
        url = 'https://www.youtube.com/watch?v=' + video_id
        rows.setdefault(url, _row('youtube', title, url, author=_text(video.get('longBylineText'))[:300]))
    if not rows and not any(EMPTY_TEXT.search(_text(m.get('text'))) for m in _walk(primary, 'messageRenderer') if isinstance(m, dict)):
        raise ValueError
    return list(rows.values())


def _devto(text, query):
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError
    rows = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError
        title, url = entry.get('title'), _canonical('devto', entry.get('url', ''))
        if (not isinstance(title, str) or not title.strip() or not url
                or not isinstance(entry.get('tag_list'), list)
                or (query and query.lstrip('#').casefold() not in entry['tag_list'])):
            raise ValueError
        summary = entry.get('description', '')
        published = entry.get('published_at')
        if not isinstance(summary, str) or (published is not None and not isinstance(published, str)):
            raise ValueError
        rows.setdefault(url, _row('devto', title, url, summary=summary[:1000], published_at=published))
    return list(rows.values())


def parse_results(platform, text, *, query=''):
    """A 200 shell/challenge/schema failure is never an empty search."""
    try:
        if platform == 'youtube':
            return _youtube(text)
        if platform == 'devto':
            return _devto(text, query)
        parser = Results(platform)
        parser.feed(text)
        rows = [_row(platform, t, u) for u, t in parser.rows.items()]
        if not rows and not EMPTY_TEXT.search(' '.join(parser.visible)):
            raise ValueError
        return rows
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ChannelError('parse_error') from None


def search(config, *, platform='suning'):
    query = config.get('web_query', '').strip()
    if not query:
        return []
    limit = min(40, max(1, int(config.get('web_limit', 10))))
    deadline = time.monotonic() + 20
    batches = []
    for url in request_urls(platform, query, limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChannelError('timeout')
        response = fetch(url, timeout=remaining)
        if response.failure:
            raise ChannelError(response.failure)
        batches.append(parse_results(platform, response.text, query=query))
    # Interleave Douban categories so a popular book does not hide film/music.
    rows = {}
    for index in range(max(map(len, batches), default=0)):
        for batch in batches:
            if index < len(batch):
                item = batch[index]
                rows.setdefault(item['url'], item)
    return list(rows.values())[:limit]


# The registry overrides retained browser implementations; no automatic browser
# fallback, so HTTP calls never require a browser opt-in or open a tab.
PROVIDERS = {name: partial(search, platform=name) for name in HTTP_PLATFORMS}
