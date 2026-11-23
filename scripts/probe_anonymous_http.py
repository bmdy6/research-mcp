#!/usr/bin/env python3
"""Reproducible anonymous probe; never infer login requirements from blocking.

python3 scripts/probe_anonymous_http.py --output docs/acceptance/anonymous-http-2026-11-23.json \
    --markdown /tmp/anonymous-http-table.md

The old one-off urllib probe was not checked in. This replaces it with the
production curl transport. Only timing, fixed statuses and public links persist.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_mcp.channels import http_search as h
from research_mcp.channels.errors import ChannelError
from research_mcp.channels.local_browser import PLATFORMS

# The first ten definitions predate the declarative search_url field.
LEGACY_ROUTES = {
    'xianyu': 'https://www.goofish.com/search?q={query}',
    'weibo': 'https://s.weibo.com/weibo?q={query}',
    'jd': 'https://search.jd.com/Search?keyword={query}',
    'taobao': 'https://s.taobao.com/search?q={query}',
    'tmall': 'https://s.taobao.com/search?tab=mall&q={query}',
    'smzdm': 'https://search.smzdm.com/?s={query}',
    'zhihu': 'https://www.zhihu.com/search?type=content&q={query}',
    'xiaohongshu': 'https://www.xiaohongshu.com/search_result?keyword={query}',
    'douyin': 'https://www.douyin.com/search/{query}',
}
TLS_EXIT_CODES = {35, 51, 58, 60, 77, 83, 90, 91}


def probe(platform, *, query=None, timeout=18):
    definition = PLATFORMS[platform]
    query = query or {'hupu': 'NBA', 'douban': '三体', 'suning': '轻薄本',
                     'jd': '轻薄本', 'taobao': '轻薄本', 'tmall': '轻薄本',
                     'xianyu': '轻薄本', 'mafengwo': '旅游', 'dianping': '咖啡'}.get(platform, 'python')
    http = platform in h.HTTP_PLATFORMS
    urls = (h.request_urls(platform, query, 3) if http else
            [definition.get('search_url', LEGACY_ROUTES.get(platform, '')).replace('{query}', quote(query, safe=''))])
    start = time.monotonic()
    row = {'channel': platform, 'query': query, 'requires_login': definition['login'],
           'login_basis': 'catalog requirement; not inferred from anonymous HTTP',
           'implementation': 'HTTP/API' if http else 'browser', 'requests': []}
    for url in urls:
        request = {'http_status': None, 'curl_exit_code': None, 'result_count': None}
        request_start = time.monotonic()
        try:
            response = h.fetch(url, timeout=timeout)
            request.update(http_status=response.http_status, curl_exit_code=response.curl_exit_code,
                           final_host=urlsplit(response.final_url).hostname)
            if response.curl_exit_code in TLS_EXIT_CODES:
                request['observation'] = 'tls_error'
            elif not response.curl_exit_code and response.http_status not in {200, 401, 403, 405, 429, 451}:
                request['observation'] = 'http_non_200'
            elif response.failure:
                request['observation'] = response.failure
            elif http:
                items = h.parse_results(platform, response.text, query=query)
                request.update(observation='parsed_results' if items else 'explicit_empty', result_count=len(items),
                               public_links=[i['url'] for i in items[:2]])
            else:
                # URL hints are not a browser/DOM adapter acceptance test.
                parser = h.Results(platform)
                parser.feed(response.text)
                request.update(observation='result_link_hints' if parser.rows else 'unverified_html_or_js',
                               result_count=len(parser.rows))
        except ChannelError as error:
            request['observation'] = error.status
        request['seconds'] = round(time.monotonic() - request_start, 3)
        row['requests'].append(request)
        if request['observation'] not in {'parsed_results', 'explicit_empty', 'result_link_hints', 'unverified_html_or_js'}:
            break
    row['seconds'] = round(time.monotonic() - start, 3)
    return row


def markdown(report):
    lines = ['## 登录需求和匿名 HTTP 探测', '',
        '旧表已撤回：上一轮 Python urllib 的本机 CA 错误被误当作平台结果，原 `URLError` 表不能支持渠道可用性或登录结论。', '',
        '以下为系统 curl 重测。复现命令：`python3 scripts/probe_anonymous_http.py --output docs/acceptance/anonymous-http-2026-11-23.json --markdown /tmp/anonymous-http-table.md`。', '',
        '统一复用生产 HTTP 传输：普通浏览器 UA、校验 TLS、禁用 curlrc、不带 Cookie/Authorization，单请求最多 18 秒、5 MB、5 次 HTTPS 重定向；每站不重试。豆瓣分别请求公开书籍/电影/音乐分类页。表中耗时包含该渠道所有请求，HTTP 状态按请求顺序列出。', '',
        '登录要求来自既有目录：浏览器“需登录”是保守使用要求，未作无痕浏览器复核。HTTP 拦截、JS 空壳、TLS/网络错误均不能证明必须登录或平台不可用。`result_link_hints` 仅为 URL 线索，未据此迁移浏览器渠道；虎嗅首页探测不等于完成其搜索。', '',
        f"采集时间（UTC）：{report['collected_at']}。原始状态见 [JSON](acceptance/anonymous-http-2026-11-23.json)。", '',
        '| 渠道 | 登录要求 | 当前实现 | HTTP 状态 | 耗时 | curl / 解析观察 |',
        '|---|---|---|---|---:|---|']
    for row in report['channels']:
        login = '需登录（目录保守要求）' if row['requires_login'] else '不要求登录'
        statuses = '/'.join(str(r['http_status'] or '—') for r in row['requests'])
        observations = '; '.join(f"{r['observation']} (curl={r['curl_exit_code']}, results={r['result_count']})" for r in row['requests'])
        lines.append(f"| {row['channel']} | {login} | {row['implementation']} | {statuses} | {row['seconds']:.3f}s | {observations} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--channels', help='Comma-separated catalog platform IDs; default all 31')
    args = parser.parse_args()
    names = args.channels.split(',') if args.channels else list(PLATFORMS)
    if not names or any(n not in PLATFORMS for n in names):
        parser.error('Unknown channel ID')
    # Independent platforms only; Taobao/Tmall share an origin, probe serially.
    names = list(dict.fromkeys(names))
    independent = [n for n in names if n != 'tmall']
    with ThreadPoolExecutor(max_workers=4) as executor:
        found = dict(zip(independent, executor.map(probe, independent)))
    if 'tmall' in names:
        found['tmall'] = probe('tmall')
    report = {'collected_at': datetime.now(timezone.utc).isoformat(), 'transport': 'system curl',
              'tls_verification_enabled': True, 'anonymous': True, 'user_agent': h.USER_AGENT,
              'channels': [found[n] for n in names]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.markdown:
        args.markdown.write_text(markdown(report), encoding='utf-8')
    print(json.dumps({'channels': len(names), 'observations': {
        row['channel']: [r['observation'] for r in row['requests']] for row in report['channels']}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
