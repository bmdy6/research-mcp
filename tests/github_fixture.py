#!/usr/bin/env python3
"""Synthetic gh executable for real CLI/MCP transport tests. Never uses network."""
import base64
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

args = sys.argv[1:]
endpoint = args[-1]
parsed = urlsplit(endpoint)
path = parsed.path
query = parse_qs(parsed.query)
scenario = os.environ.get('GITHUB_FIXTURE', 'normal')
status = {'missing': 404, 'permission': 403, 'auth': 401, 'rate': 429, 'secondary': 403, 'exhausted': 403,
          'retry_after': 403}.get(scenario)
if status:
    reason = 'secondary rate limit' if scenario == 'secondary' else 'synthetic failure'
    if '--include' in args:
        headers = [f'HTTP/2.0 {status} Synthetic', 'Content-Type: application/json; charset=utf-8',
                   'X-Ratelimit-Limit: 5000',
                   'x-RateLimit-Remaining: ' + ('0' if scenario == 'exhausted' else '4999'),
                   'X-Ratelimit-Reset: 1893456000', 'X-Github-Request-Id: SYNTHETIC_SENSITIVE_MARKER']
        if scenario == 'retry_after':
            headers.append('Retry-After: 60')
        print('\r\n'.join(headers) + '\r\n\r\n' + json.dumps({'message': reason}), end='')
    print(f'gh: {reason} (HTTP {status}) SYNTHETIC_SENSITIVE_MARKER', file=sys.stderr)
    sys.exit(1)
headers = ['HTTP/2.0 200 OK']
if '/contents/' in path:
    ref = query.get('ref', [''])[0]
    if scenario == 'slash_ref' and ref != 'feature/topic':
        if '--include' in args:
            print('HTTP/2.0 404 Not Found\nX-Ratelimit-Limit: 5000\nX-Ratelimit-Remaining: 4999\n\n{"message": "Not Found"}')
        print('gh: Not Found (HTTP 404)', file=sys.stderr)
        sys.exit(1)
    payload = {'type': 'file', 'encoding': 'base64', 'size': 18, 'sha': 'synthetic-sha',
               'content': base64.b64encode(b'Synthetic file.\n').decode()}
    if scenario == 'binary':
        payload['content'] = base64.b64encode(b'\x00binary').decode()
    if scenario == 'directory':
        payload = []
elif path.endswith('/readme'):
    payload = {'content': base64.b64encode(b'# Synthetic README').decode()}
elif any(path.endswith('/' + stream) for stream in ('comments', 'reviews')):
    limit = int(query.get('per_page', ['50'])[0])
    page = int(query.get('page', ['1'])[0])
    count = limit if scenario in ('pagination', 'large') and page == 1 else 1
    if scenario in ('pagination', 'large') and page == 1:
        headers.append('Link: <https://api.github.com/synthetic?page=2>; rel="next"')
    payload = [{'id': page * 100 + i, 'body': 'Synthetic discussion' * (1000 if scenario == 'large' else 1),
                'html_url': 'https://github.com/example/project/issues/7#issuecomment-1',
                'user': {'login': 'example'}, 'state': 'COMMENTED', 'path': 'src/example.py',
                'line': 7, 'diff_hunk': '@@ synthetic @@', 'in_reply_to_id': 1} for i in range(count)]
elif '/pulls/' in path or '/issues/' in path:
    payload = {'title': 'Synthetic object', 'body': 'Synthetic object body', 'comments': 51, 'number': 7}
    if scenario == 'issue_pr' and '/issues/' in path:
        payload['pull_request'] = {}
else:
    payload = {'name': 'project', 'description': 'Synthetic repo', 'default_branch': 'main'}
if '--include' in args:
    print('\n'.join(headers) + '\n')
print('{invalid' if scenario == 'malformed' else json.dumps(payload))
