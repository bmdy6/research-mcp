"""掘金渠道：官方搜索 API，免登录、免 key。

实测（2026-09-14）：默认排序下 20 条里有 3~4 条在 90 天内，比 CSDN 的搜索结果新得多。
"""

from __future__ import annotations

import json
import subprocess
from research_mcp.commands import run as run_command
import urllib.parse

from ._util import clean_html, iso_from_epoch

SEARCH_API = "https://api.juejin.cn/search_api/v1/search"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _get(url: str, timeout: int) -> str:
    result = run_command(
        ["curl", "-sS", "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip()[:200] or f"curl 失败：{result.returncode}")
    return result.stdout


def _to_item(entry: dict) -> dict | None:
    model = entry.get("result_model") or {}
    info = model.get("article_info") or {}
    article_id = info.get("article_id")
    if not article_id:
        return None
    return {
        "kind": "article",
        "title": clean_html(info.get("title")),
        "url": f"https://juejin.cn/post/{article_id}",
        "published_at": iso_from_epoch(info.get("ctime")),
        "source": "juejin",
        "summary": clean_html(info.get("brief_content"))[:200],
        "author": (model.get("author_user_info") or {}).get("user_name", ""),
        "views": int(info.get("view_count") or 0),
    }


def search(config: dict) -> list[dict]:
    queries = config.get("juejin_queries") or ([config["juejin_query"]] if config.get("juejin_query") else [])
    if not queries:
        return []
    limit = int(config.get("juejin_limit", 60))
    timeout = int(config.get("juejin_timeout_s", 20))

    items: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        url = SEARCH_API + "?" + urllib.parse.urlencode(
            {"query": query, "id_type": 0, "cursor": 0, "limit": 20}
        )
        payload = json.loads(_get(url, timeout))
        if payload.get("err_no") != 0:
            raise RuntimeError(f"掘金搜索 err_no={payload.get('err_no')} {payload.get('err_msg', '')}".strip())
        for entry in payload.get("data") or []:
            item = _to_item(entry)
            if not item or not item["title"] or item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
    # 最新的排前面：截断时优先留新鲜内容（每日发现只关心近期的）
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:limit]
