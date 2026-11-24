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
from urllib.parse import quote

from .free_apis import _request, _get, _dict, _list, _text, _row
from .errors import ChannelError

DEFINITIONS = {
    'mdn': ('MDN first-party search API', 'documentation', 'https://github.com/mdn/yari'),
    'google_news_rss': ('Google News RSS (local personal opt-in)', 'news', 'https://news.google.com/'),
    'aihot': ('AIHOT curated RSS (local personal opt-in)', 'news', 'https://aihot.news/agent'),
    'npm': ('npm official Registry Search API', 'package', 'https://api-docs.npmjs.com/'),
    'crates_io': ('crates.io official API', 'package', 'https://crates.io/data-access'),
    'docker_hub': ('Docker Hub public repository Search API', 'repository', 'https://docs.docker.com/reference/api/hub/latest/'),
    'itunes_apps': ('Apple iTunes official software Search API', 'application', 'https://performance-partners.apple.com/search-api'),
}


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
            # Latest 50 curated AI items (many from X). Personal read-only use; no mirroring.
            text = _request('https://aihot.news/feed.xml', deadline=deadline,
                            headers={'Accept': 'application/rss+xml'}, raw_text=True)
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise ValueError
            root = ET.fromstring(text)
            if root.tag != 'rss' or root.find('channel') is None:
                raise ValueError
            # Space-separated terms match title/summary/category with OR; '*' returns the whole feed.
            terms = [] if query.strip() == '*' else [t.lower() for t in query.split()]
            items = []
            for r in root.findall('./channel/item'):
                title, body, category = r.findtext('title') or '', r.findtext('description') or '', r.findtext('category') or ''
                haystack = f'{title} {body} {category}'.lower()
                if terms and not any(t in haystack for t in terms):
                    continue
                original = re.search(r'href="(https://[^"]+)"[^>]*>\s*阅读原文', body)
                summary = re.split(r'</p>', body, maxsplit=1)[0]
                items.append(_row(title, original.group(1) if original else r.findtext('link'),
                                  (f'[{category}] ' if category else '') + summary, r.findtext('pubDate')))
                if len(items) >= limit:
                    break
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
