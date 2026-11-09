"""GET-only GitHub object reads using the caller's existing gh authentication.

Discussion budgets are per stream, one REST page per request. Continuations are
research input URLs, never untrusted API URLs from response headers.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import subprocess
from research_mcp.commands import run as run_command
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit, urlunsplit


class GitHubReadError(Exception):
    def __init__(self, reason, *, status=None):
        super().__init__(reason)
        self.reason, self.status = reason, status


def failure_reason(message: str, *, status=None, headers=None, body='') -> str:
    """Classify the parsed response; stderr is only a no-status fallback."""
    rate_limited = 'GitHub 接口被限流，稍后重试'
    if status == 404:
        return 'GitHub 资源不存在或当前身份不可见（404）'
    if status == 401:
        return 'GitHub 认证不可用（401）'
    if status == 429:
        return rate_limited
    if status == 403:
        headers = headers or {}
        if (headers.get('x-ratelimit-remaining') == '0' or 'retry-after' in headers
                or re.search(r'\brate[ _-]*limit\b|\babuse detection\b', body, re.I)):
            return rate_limited
        return 'GitHub 权限不足（403）'
    if status is not None:
        return 'GitHub 接口读取失败'
    # Defensive filtering also keeps header-shaped stderr lines out of matching.
    message = '\n'.join(line for line in message.splitlines()
                        if not (re.fullmatch(r'[A-Za-z0-9-]+', line.partition(':')[0])
                                and ':' in line and not line.lower().startswith('gh:')))
    if re.search(r'HTTP 429|\brate[ _-]*limit\b|secondary rate|abuse', message, re.I):
        return rate_limited
    if re.search(r'HTTP 401|authentication', message, re.I):
        return 'GitHub 认证不可用（401）'
    if re.search(r'HTTP 403|forbidden|not accessible', message, re.I):
        return 'GitHub 权限不足（403）'
    if re.search(r'HTTP 404|Not Found', message, re.I):
        return 'GitHub 资源不存在或当前身份不可见（404）'
    return 'GitHub 接口读取失败'


def api(endpoint: str):
    """Return JSON and has-next; never propagate raw stderr or response headers."""
    try:
        result = run_command(
            ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '--include', endpoint],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise GitHubReadError('GitHub 接口读取超时') from None
    except RuntimeError as error:
        raise GitHubReadError(str(error)) from None
    except OSError:
        raise GitHubReadError('GitHub CLI 不可用') from None
    wire = result.stdout.replace('\r\n', '\n')
    headers, separator, body = wire.partition('\n\n')
    status_line = re.match(r'HTTP/[\d.]+\s+(\d{3})(?:\s|$)', headers)
    status = int(status_line[1]) if status_line else None
    if result.returncode or (status is not None and status >= 400):
        fields = {}
        if status_line and separator:
            for line in headers.splitlines()[1:]:
                key, colon, value = line.partition(':')
                if colon:
                    fields[key.strip().lower()] = value.strip()
        raise GitHubReadError(failure_reason(result.stderr, status=status, headers=fields,
                                             body=body if separator else ''), status=status)
    if not separator or status is None:
        raise GitHubReadError('GitHub 返回的内容解析失败')
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise GitHubReadError('GitHub 返回的内容解析失败') from None
    has_next = any(line.lower().startswith('link:') and 'rel="next"' in line for line in headers.splitlines())
    return payload, has_next


def _integer(params, key, default, maximum):
    try:
        values = params.get(key, [str(default)])
        if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
            raise ValueError
        value = int(values[0])
        if not 1 <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise GitHubReadError(f'{key} 必须是 1 到 {maximum} 的整数') from None


def _object(payload):
    if not isinstance(payload, dict):
        raise GitHubReadError('GitHub 返回的对象格式无效')
    return payload


def _comment(row):
    row = _object(row)
    user = row.get('user') or {}
    return {key: value for key, value in {
        'id': row.get('id'), 'url': row.get('html_url'), 'author': user.get('login'),
        'body': row.get('body') or '', 'created_at': row.get('created_at') or row.get('submitted_at'),
        'state': row.get('state'), 'path': row.get('path'), 'line': row.get('line'),
        'original_line': row.get('original_line'), 'diff_hunk': row.get('diff_hunk'),
        'in_reply_to_id': row.get('in_reply_to_id'),
        'pull_request_review_id': row.get('pull_request_review_id'),
    }.items() if value is not None}


def read(url: str) -> dict:
    parsed = urlsplit(url)
    parts = [unquote(p) for p in parsed.path.rstrip('/').split('/')[1:]]
    if (parsed.username or parsed.password or parsed.port not in (None, 443)
            or len(parts) < 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) for p in parts[:2])):
        raise GitHubReadError('不支持的 GitHub URL')
    full_name = '/'.join(parts[:2]).lower().removesuffix('.git')
    base = 'repos/' + full_name
    canonical = 'https://github.com/' + full_name
    params = parse_qs(parsed.query, keep_blank_values=True)
    result = {'kind': 'github', 'url': urlunsplit(('https', 'github.com', parsed.path, parsed.query, '')),
              'repo': {'full_name': full_name}, 'ok': True}
    if len(parts) >= 5 and parts[2] == 'blob':
        return {**result, **_file(base, parts[3:], params)}
    if len(parts) != 4 or parts[2] not in ('issues', 'pull') or not re.fullmatch(r'[1-9][0-9]*', parts[3]):
        raise GitHubReadError('不支持的 GitHub URL 类型；支持仓库首页、Issue、PR 和 blob 文件')
    number = parts[3]
    limit = _integer(params, 'comment_limit', 50, 100)
    page = _integer(params, 'comment_page', 1, 10000)
    selected = params.get('comment_stream', [None])
    if len(selected) != 1:
        raise GitHubReadError('comment_stream 只能指定一次')
    selected = selected[0]
    resource = 'pulls' if parts[2] == 'pull' else 'issues'
    metadata, _ = api(f'{base}/{resource}/{number}')
    metadata = _object(metadata)
    # GitHub REST issue routes also resolve PR numbers. Do not label a PR as an issue.
    is_pr = resource == 'pulls' or 'pull_request' in metadata
    if is_pr and resource == 'issues':
        metadata, _ = api(f'{base}/pulls/{number}')
        metadata = _object(metadata)
    object_type = 'pull_request' if is_pr else 'issue'
    streams = {'comments': f'{base}/issues/{number}/comments'}
    if is_pr:
        streams.update(reviews=f'{base}/pulls/{number}/reviews',
                       review_comments=f'{base}/pulls/{number}/comments')
    if selected is not None and selected not in streams:
        raise GitHubReadError('不支持的 comment_stream')
    discussion, coverage = {}, {}
    text = [f'# {metadata.get("title") or object_type} #{number}', metadata.get('body') or '（无正文）']
    for stream, endpoint in streams.items():
        if selected is not None and selected != stream:
            continue
        rows, has_next = api(endpoint + '?' + urlencode({'per_page': limit, 'page': page}))
        if not isinstance(rows, list) or len(rows) > limit:
            raise GitHubReadError('GitHub 返回的评论格式无效')
        discussion[stream] = [_comment(row) for row in rows]
        next_url = None
        if has_next:
            next_url = f'{canonical}/{"pull" if is_pr else "issues"}/{number}?' + urlencode({
                'comment_limit': limit, 'comment_page': page + 1, 'comment_stream': stream})
        coverage[stream] = {'returned': len(rows), 'page': page, 'budget': limit,
                            'truncated': has_next, 'next_url': next_url}
        text.append(f'## {stream}（本页 {len(rows)} 条；页码 {page}；预算 {limit}）')
        for row in discussion[stream]:
            location = f' {row["path"]}:{row.get("line") or row.get("original_line") or "?"}' if row.get('path') else ''
            text.append(f'### {row.get("author", "?")}{location} {row.get("state", "")}\n'
                        f'{row.get("url", "")}\n{row["body"]}')
            if row.get('diff_hunk'):
                text.append(row['diff_hunk'])
        text.append(f'继续读取：{next_url}' if next_url else '本页之后无更多记录。')
    truncated = any(item['truncated'] for item in coverage.values())
    partial = truncated or page > 1 or selected is not None
    scope = '；'.join(f'{name}: 本页 {item["returned"]} 条，page={page}，'
                     + ('后续未读' if item['truncated'] else '本页之后无更多')
                     + (f'，继续 {item["next_url"]}' if item['next_url'] else '')
                     for name, item in coverage.items())
    if page > 1 or selected is not None:
        scope += '；仅指定页/讨论类型，不代表完整讨论'
    # Whitelisted metadata only; no full API response saved or surfaced.
    return {**result, 'object_type': object_type, 'title': metadata.get('title'),
            'metadata': {k: metadata[k] for k in ('number', 'state', 'html_url', 'comments', 'review_comments') if k in metadata},
            'discussion': discussion, 'coverage': coverage, 'coverage_note': scope,
            'truncated': truncated, 'partial': partial, 'content': '\n\n'.join(text)}


def _file(base, tail, params):
    if len(tail) > 20 or any(not p or p in ('.', '..') for p in tail):
        raise GitHubReadError('GitHub 文件路径无效或超过解析预算')
    explicit = params.get('github_ref')
    if explicit is not None:
        if len(explicit) != 1 or not explicit[0] or not '/'.join(tail).startswith(explicit[0] + '/'):
            raise GitHubReadError('github_ref 必须与 blob URL 的 ref 前缀一致')
        candidates = [(explicit[0], '/'.join(tail)[len(explicit[0]) + 1:])]
    else:
        # Longest ref first supports branch names containing slashes. Only a
        # genuine 404 advances the split; auth/rate/timeout never triggers fallback.
        candidates = [('/'.join(tail[:n]), '/'.join(tail[n:])) for n in range(len(tail) - 1, 0, -1)]
    payload = None
    for ref, path in candidates:
        try:
            payload, _ = api(base + '/contents/' + quote(path, safe='/') + '?' + urlencode({'ref': ref}))
            break
        except GitHubReadError as error:
            if error.status != 404:
                raise
    if payload is None:
        raise GitHubReadError('GitHub 文件或 ref 不存在或当前身份不可见（404）')
    payload = _object(payload)
    if payload.get('type') != 'file' or payload.get('encoding') != 'base64' or payload.get('size', 0) > 1_000_000:
        raise GitHubReadError('仅支持不超过 1 MB 的 UTF-8 文本文件；目录、符号链接和大文件未读取')
    try:
        raw = base64.b64decode(''.join(payload.get('content', '').split()), validate=True)
        content = raw.decode('utf-8')
        if b'\x00' in raw or len(raw) > 1_000_000:
            raise ValueError
    except (ValueError, binascii.Error, UnicodeError):
        raise GitHubReadError('GitHub 文件不是支持的 UTF-8 文本') from None
    return {'object_type': 'file', 'file': {'ref': ref, 'path': path, 'sha': payload.get('sha')},
            'content': content, 'coverage_note': f'UTF-8 文件全文；ref={ref}；path={path}；未读取讨论',
            'truncated': False}
