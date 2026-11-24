"""Command-line adapter for the shared search and read service."""
from __future__ import annotations

import argparse
import json

from . import service


def main() -> int:
    parser = argparse.ArgumentParser(prog="research-mcp", description="搜索关键词或读取原文")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--providers", help="渠道名，逗号分隔")
    search.add_argument("--max-age-days", type=int, help="按已知日期筛选最近 1–3650 天；未知日期保留，默认不限制年代")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("url")
    for command in (search, fetch):
        command.add_argument("--format", choices=("json", "text"), default="text")
    channels = sub.add_parser("channels", help="列出渠道能力与最近状态（不发网络请求）")
    channels.add_argument("--format", choices=("json", "text"), default="json")
    sub.add_parser("login-status", help="检查登录状态，不自动登录")
    sub.add_parser("mcp")
    args = parser.parse_args()
    if args.command == "mcp":
        from .mcp_server import mcp
        mcp.run()
        return 0
    if args.command == "login-status":
        from .login_status import login_status
        result = login_status()
    elif args.command == "channels":
        result = service.list_channels()
    elif args.command == "search":
        providers = [p.strip() for p in args.providers.split(",") if p.strip()] if args.providers else None
        age = {} if args.max_age_days is None else {"max_age_days": args.max_age_days}
        result = service.search(args.query, limit=args.limit, providers=providers, **age)
    else:
        result = service.fetch(args.url)
    # Preserve the existing human-readable JSON text format.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1
