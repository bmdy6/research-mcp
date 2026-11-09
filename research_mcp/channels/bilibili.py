"""B站渠道：站内搜索 API（curl 直连），不可用时回落 `bili` 命令。

排序取舍：B 站只有两个坏选择——按播放量（order=click）排拿到的是多年前的老视频，
会被调研的时效过滤掉；按发布时间（order=pubdate）排拿到的是刚发布、播放量常只有几百的
内容（2026-09-14 实测：pubdate 下 90 天内的候选播放量多在 200~4000）。每日发现默认用
综合排序（order=totalrank），排序和播放量门槛都在 search.json 里可调
（`bilibili_order` / `bilibili_min_play`）；按需搜索（research.py search）走 click。

匿名请求先取 buvid3 访客标识再带上：不带时连续请求会命中风控返回 HTML 页
（2026-09-14 实测 3 次挂 2 次，带上后连续 4 次全部正常）。
"""

from __future__ import annotations

import json
import subprocess
from research_mcp.commands import run as run_command
import urllib.parse

from ._util import clean_html, iso_from_epoch

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
SPI_API = "https://api.bilibili.com/x/frontend/finger/spi"


def _curl(url: str, *, timeout: int, cookie: str | None = None) -> str:
    command = [
        "curl", "-sS", "--max-time", str(timeout),
        "-H", f"User-Agent: {UA}",
        "-H", "Referer: https://www.bilibili.com/",
    ]
    if cookie:
        command += ["-H", f"Cookie: {cookie}"]
    command.append(url)
    result = run_command(command, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip()[:200] or f"curl 失败：{result.returncode}")
    return result.stdout


def _buvid3(timeout: int) -> str:
    """匿名访客标识；拿不到就返回空串（仍能搜，只是更容易被风控拦）。"""
    try:
        payload = json.loads(_curl(SPI_API, timeout=timeout))
        return (payload.get("data") or {}).get("b_3", "") or ""
    except Exception:
        return ""


def _to_item(row: dict) -> dict | None:
    bvid = row.get("bvid") or ""
    if not bvid:
        return None  # 接口和 bili 命令都会混进空条目
    published = row.get("pubdate")
    return {
        "kind": "article",
        "title": clean_html(row.get("title")),
        "url": f"https://www.bilibili.com/video/{bvid}",
        "published_at": iso_from_epoch(published),
        "source": "bilibili",
        "summary": clean_html(row.get("description"))[:200],
        "author": row.get("author", ""),
        "play": int(row.get("play") or 0),
    }


def _search_api(query: str, *, order: str, page: int, timeout: int, cookie: str | None) -> list[dict]:
    url = SEARCH_API + "?" + urllib.parse.urlencode(
        {"search_type": "video", "keyword": query, "order": order, "page": page, "page_size": 20}
    )
    raw = _curl(url, timeout=timeout, cookie=cookie)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("B站返回非 JSON（风控页或接口变更）") from exc
    if payload.get("code") != 0:
        raise RuntimeError(f"B站搜索 code={payload.get('code')} {payload.get('message', '')}".strip())
    return (payload.get("data") or {}).get("result") or []


def _search_cli(query: str, limit: int, timeout: int) -> list[dict]:
    """备选后端：bili 命令（不控制排序，只在 API 不可用时用）。"""
    result = run_command(
        ["bili", "search", query, "--type", "video", "-n", str(limit), "--json"],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "bili 命令失败").strip()[:200])
    payload = json.loads(result.stdout)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def search(config: dict) -> list[dict]:
    queries = config.get("bilibili_queries") or ([config["bilibili_query"]] if config.get("bilibili_query") else [])
    if not queries:
        return []
    order = config.get("bilibili_order", "totalrank")
    min_play = int(config.get("bilibili_min_play", 0))
    limit = int(config.get("bilibili_limit", 30))
    timeout = int(config.get("bilibili_timeout_s", 20))
    pages = max(1, int(config.get("bilibili_pages", 1)))

    cookie = None
    buvid = _buvid3(timeout)
    if buvid:
        cookie = f"buvid3={buvid}"

    rows: list[dict] = []
    api_error: Exception | None = None
    try:
        for query in queries:
            for page in range(1, pages + 1):
                rows.extend(_search_api(query, order=order, page=page, timeout=timeout, cookie=cookie))
    except Exception as exc:
        api_error = exc
        rows = []

    if not rows:  # API 挂了就试 bili 命令，两条路都挂才算渠道不可用
        try:
            rows = _search_cli(queries[0], limit, timeout)
        except Exception as cli_error:
            if api_error is not None:
                raise RuntimeError(f"API：{api_error}；bili 命令：{cli_error}") from cli_error
            raise

    items: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        item = _to_item(row)
        if not item or item["url"] in seen or item["play"] < min_play:
            continue
        seen.add(item["url"])
        items.append(item)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:limit]
