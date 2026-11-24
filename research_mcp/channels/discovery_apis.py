"""Keyless official package and application search, using the bounded API transport.

Contracts: https://api-docs.npmjs.com/ , https://crates.io/data-access ,
https://docs.docker.com/reference/api/hub/latest/ ,
https://performance-partners.apple.com/search-api . No install/download actions.
"""
from functools import partial
import math
import os
import re
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .free_apis import _request, _get, _dict, _list, _text, _row
from .errors import ChannelError

DEFINITIONS = {
    'mdn': ('MDN first-party search API', 'documentation', 'https://github.com/mdn/yari'),
    'google_news_rss': ('Google News RSS (local personal opt-in)', 'news', 'https://news.google.com/'),
    'aihot': ('AIHOT anonymous Agent API v1 (local personal opt-in)', 'news', 'https://aihot.news/agent'),
    'npm': ('npm official Registry Search API', 'package', 'https://api-docs.npmjs.com/'),
    'crates_io': ('crates.io official API', 'package', 'https://crates.io/data-access'),
    'docker_hub': ('Docker Hub public repository Search API', 'repository', 'https://docs.docker.com/reference/api/hub/latest/'),
    'itunes_apps': ('Apple iTunes official software Search API', 'application', 'https://performance-partners.apple.com/search-api'),
}

AIHOT_API = 'https://aihot.news/api/v1/agent/'
AIHOT_CATEGORIES = {'ai-models', 'ai-products', 'industry', 'paper', 'tip', 'opinion'}
_AIHOT_HEAD = re.compile(r'^(?:\d+\.|第 (\d+) 名：)\s*\[(.+)\]\((https://aihot\.news/items/[A-Za-z0-9]+)\)\s*$')
_AIHOT_TIME = re.compile(r'(?:发布于|最新进展) (\d{2})-(\d{2}) (\d{2}):(\d{2})')
_BEIJING = timezone(timedelta(hours=8))


def _aihot_url(query: str, limit: int) -> str:
    """Query forms: '*' 24h featured; 'all' or 'all:<category>' 24h all items; 'hot' ranking; else keyword search (7 days)."""
    q = query.strip()
    size = str(max(1, min(30, limit)))
    if q == 'hot':
        return AIHOT_API + 'hot'
    if q == '*' or q == 'all' or q.startswith('all:'):
        params = {'window': '24h', 'limit': size}
        if q != '*':
            params['mode'] = 'all'
        category = q[4:] if q.startswith('all:') else ''
        if category:
            if category not in AIHOT_CATEGORIES:
                raise ValueError
            params['category'] = category
        return AIHOT_API + 'latest?' + urlencode(params)
    return AIHOT_API + 'search?' + urlencode({'q': q})


def _aihot_time(month: str, day: str, hour: str, minute: str) -> str:
    now = datetime.now(_BEIJING)
    stamp = datetime(now.year, int(month), int(day), int(hour), int(minute), tzinfo=_BEIJING)
    if stamp > now + timedelta(days=1):  # December items read in January
        stamp = stamp.replace(year=now.year - 1)
    return stamp.astimezone(timezone.utc).isoformat()


def _aihot_items(text: str) -> list[dict]:
    """Parse AIHOT's Markdown list. Original link preferred; hot rank and source count kept in summary."""
    if '［AIHOT 不可信外部资料开始］' not in text:
        raise ValueError
    blocks, current = [], None
    for line in text.splitlines():
        head = _AIHOT_HEAD.match(line.strip())
        if head:
            current = {'rank': head.group(1), 'title': head.group(2), 'link': head.group(3), 'lines': []}
            blocks.append(current)
        elif current is not None and line.startswith('   '):
            current['lines'].append(line.strip())
    rows = []
    for b in blocks:
        meta = b['lines'][0] if b['lines'] else ''
        fields = {k: v for k, _, v in (l.partition('：') for l in b['lines'][1:]) if v}
        when = _AIHOT_TIME.search(meta)
        category = meta.rsplit(' · ', 1)[-1] if ' · 发布于 ' in meta else ''
        if b['rank']:
            summary = f"[热点第 {b['rank']} 名] " + meta
        else:
            summary = (f'[{category}] ' if category else '') + fields.get('摘要', '')
            if meta:
                summary = meta.split(' · ', 1)[0] + '：' + summary
        original = fields.get('原文', '')
        rows.append(_row(b['title'], original if original.startswith('https://') else b['link'],
                         summary, _aihot_time(*when.groups()) if when else None))
    return rows


def search(config: dict, *, provider: str) -> list[dict]:
    if not config.get('web_query'):
        return []
    query = _text(config['web_query'])
    try:
        limit = max(1, min(40, int(config.get('web_limit', 10))))
        timeout = float(config.get('web_timeout_s', 20))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
        deadline = time.monotonic() + min(timeout, 60)
        if provider == 'google_news_rss':
            if os.environ.get('RESEARCH_GOOGLE_NEWS_RSS') != '1':
                raise ChannelError('auth_required')
            # Local CLI/stdio only; never added to public adapter allowlist.
            text = _request('https://news.google.com/rss/search?' + urlencode({
                'q': query, 'hl': 'zh-CN', 'gl': 'CN', 'ceid': 'CN:zh-Hans'}),
                deadline=deadline, headers={'Accept': 'application/rss+xml'}, raw_text=True)
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise ValueError
            root = ET.fromstring(text)
            if root.tag != 'rss' or root.find('channel') is None:
                raise ValueError
            items = [_row(r.findtext('title'), r.findtext('link'), r.findtext('description') or '', r.findtext('pubDate'))
                     for r in root.findall('./channel/item')[:limit]]
        elif provider == 'aihot':
            if os.environ.get('RESEARCH_AIHOT') != '1':
                raise ChannelError('auth_required')
            text = _request(_aihot_url(query, limit), deadline=deadline,
                            headers={'Accept': 'text/markdown'}, raw_text=True)
            items = _aihot_items(text)[:limit]
        elif provider == 'mdn':
            data = _dict(_get('https://developer.mozilla.org/api/v1/search', {'q': query, 'locale': 'en-US'}, deadline))
            items = []
            for r in map(_dict, _list(data.get('documents'))):
                path = _text(r.get('mdn_url'))
                if not re.fullmatch(r'/[a-zA-Z-]+/docs/[^?#]+', path) or '..' in path:
                    raise ValueError
                items.append(_row(r.get('title'), 'https://developer.mozilla.org' + path, r.get('summary', '')))
        elif provider == 'npm':
            data = _dict(_get('https://registry.npmjs.org/-/v1/search', {'text': query, 'size': limit}, deadline))
            rows = [_dict(_dict(r).get('package')) for r in _list(data.get('objects'))]
            items = [_row(r.get('name'), 'https://www.npmjs.com/package/' + quote(_text(r.get('name')), safe=''),
                          r.get('description', ''), r.get('date')) for r in rows]
        elif provider == 'crates_io':
            data = _dict(_get('https://crates.io/api/v1/crates', {'q': query, 'per_page': limit}, deadline))
            items = [_row(r.get('name'), 'https://crates.io/crates/' + quote(_text(r.get('name')), safe=''),
                          r.get('description', ''), r.get('updated_at')) for r in map(_dict, _list(data.get('crates')))]
        elif provider == 'docker_hub':
            data = _dict(_get('https://hub.docker.com/v2/search/repositories/', {'query': query, 'page_size': limit}, deadline))
            items = []
            for r in map(_dict, _list(data.get('results'))):
                name = _text(r.get('repo_name'))
                # Single-component official images use /_/; others use /r/.
                path = '_/' if '/' not in name else 'r/'
                items.append(_row(name, 'https://hub.docker.com/' + path + quote(name, safe='/'), r.get('short_description', '')))
        elif provider == 'itunes_apps':
            data = _dict(_get('https://itunes.apple.com/search', {'term': query, 'entity': 'software', 'country': 'cn', 'limit': limit}, deadline))
            items = [_row(r.get('trackName'), r.get('trackViewUrl'), r.get('description', ''), r.get('releaseDate'))
                     for r in map(_dict, _list(data.get('results')))]
        else:
            raise ChannelError('unsupported')
        seen, result = set(), []
        for r in items:
            if r['url'] in seen:
                continue
            seen.add(r['url'])
            result.append({**r, 'kind': DEFINITIONS[provider][1], 'source': provider,
                           'rank': len(result) + 1, 'actual_backend': DEFINITIONS[provider][0]})
        return result[:limit]
    except (ValueError, TypeError, KeyError, OverflowError, ET.ParseError):
        raise ChannelError('parse_error') from None


PROVIDERS = {name: partial(search, provider=name) for name in DEFINITIONS}
