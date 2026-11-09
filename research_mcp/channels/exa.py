"""Exa 全网语义搜索渠道：用标准库直连 Exa 官方 MCP 端点。

支持多个查询词（`exa_queries`）。用 `site:zhihu.com 关键词` 这类站内限定查询，可以覆盖
知乎这类正文被反爬挡住、又没有免 cookie 搜索接口的平台——拿到标题、摘要和链接，
正文能不能读是另一回事。小红书实测索引很弱（只到用户主页），别指望这条路径。

免费 MCP 端点有调用频率限制，撞上返回 429（`You've hit Exa's free MCP rate limit.`）。
限流时退避重试一两次；仍失败就抛出带「限流」字样的错误，并在本进程内记一段时间，
后续调用直接跳过——同一轮对话里连续重试没有意义，只会白等。

想稳定调用就配自备 key：环境变量 `EXA_API_KEY`，会自动拼进
MCP 端点地址；没有 key 时仍用免费端点。

搜索工具返回的是人读文本（Title/URL/Published/Author/Highlights 块），这里按块解析；
上游改了输出格式就抛错降级，不阻塞其他渠道。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse

BLOCK_SPLIT = re.compile(r"\n\s*-{3,}\s*\n")
FIELD_KEYS = ("Title", "URL", "Published", "Author")

FREE_ENDPOINT = "https://mcp.exa.ai/mcp"
TOOL_NAME = "web_search_exa"
RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "resource_exhausted", "too many requests")
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 5.0)
# 一次限流后本进程内跳过的时长：免费额度按小时恢复，短时间内重试没有意义
SKIP_WINDOW_SECONDS = 300.0

_limited_until = 0.0


def exa_api_key() -> str:
    """Read the optional API key only from the environment."""
    return os.environ.get("EXA_API_KEY", "").strip()


def endpoint_selector() -> str:
    """官方端点地址：有自备 key 就用带 key 的端点，否则用免费端点。"""
    key = exa_api_key()
    return f"{FREE_ENDPOINT}?" + urllib.parse.urlencode({"exaApiKey": key}) if key else FREE_ENDPOINT


def _is_rate_limited(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def _rate_limit_skip_active() -> bool:
    return time.monotonic() < _limited_until


def _remember_rate_limit() -> None:
    global _limited_until
    _limited_until = time.monotonic() + SKIP_WINDOW_SECONDS


def _reset_rate_limit_state() -> None:
    """测试用：清掉进程内的限流标记。"""
    global _limited_until
    _limited_until = 0.0


def _rpc(method: str, params: dict, identity: int | None, headers: dict, timeout: int):
    """One Streamable HTTP POST; accept JSON or finite SSE response events."""
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if identity is not None:
        message["id"] = identity
    request = urllib.request.Request(endpoint_selector(), data=json.dumps(message).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.headers.get("Mcp-Session-Id"):
                headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            if identity is None and response.status in (200, 202, 204):
                return {}
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError("Exa 响应超过读取预算")
            text = raw.decode("utf-8")
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                payload = None
                for event in re.split(r"\r?\n\r?\n", text):
                    data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
                    if data:
                        candidate = json.loads(data)
                        if isinstance(candidate, dict) and candidate.get("id") == identity:
                            payload = candidate
                            break
            else:
                payload = json.loads(text)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError(f"Exa HTTP {code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise RuntimeError("Exa 连接或响应解析失败") from None
    if not isinstance(payload, dict) or payload.get("id") != identity:
        raise RuntimeError("Exa JSON-RPC 响应无效")
    if payload.get("error"):
        if _is_rate_limited(json.dumps(payload["error"])):
            raise RuntimeError("Exa 429 限流")
        raise RuntimeError("Exa JSON-RPC 请求失败")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Exa 返回结果格式无效")
    return result


def _call_direct(query: str, limit: int, timeout: int) -> str:
    if _rate_limit_skip_active():
        raise RuntimeError("exa 免费端点刚触发过限流，本次跳过（约 5 分钟后恢复，或配 EXA_API_KEY）")
    for attempt in range(MAX_ATTEMPTS):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": "research-mcp/0.1"}
        try:
            initialized = _rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "research-mcp", "version": "0.1.0"}}, 1, headers, timeout)
            headers["MCP-Protocol-Version"] = initialized.get("protocolVersion", "2024-11-05")
            _rpc("notifications/initialized", {}, None, headers, timeout)
            tools = _rpc("tools/list", {}, 2, headers, timeout)
            if not any(tool.get("name") == TOOL_NAME for tool in tools.get("tools", []) if isinstance(tool, dict)):
                raise RuntimeError("Exa 未提供搜索工具")
            payload = _rpc("tools/call", {"name": TOOL_NAME, "arguments": {
                "query": query, "numResults": limit, "objective": query}}, 3, headers, timeout)
            text = "\n".join(block.get("text", "") for block in payload.get("content", []) if isinstance(block, dict))
            if payload.get("isError"):
                raise RuntimeError("Exa 429 限流" if _is_rate_limited(text) else "Exa 搜索工具调用失败")
            return text
        except RuntimeError as error:
            if not _is_rate_limited(str(error)):
                raise
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    _remember_rate_limit()
    raise RuntimeError("exa 免费端点限流（已重试，随后约 5 分钟不再尝试）")


def _parse(text: str) -> list[dict]:
    items: list[dict] = []
    for block in BLOCK_SPLIT.split(text):
        fields: dict[str, str] = {}
        highlights: list[str] = []
        in_highlights = False
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("Highlights:"):
                in_highlights = True
                rest = stripped[len("Highlights:"):].strip()
                if rest:
                    highlights.append(rest)
                continue
            for key in FIELD_KEYS:
                prefix = f"{key}: "
                if stripped.startswith(prefix):
                    fields[key] = stripped[len(prefix):].strip()
                    in_highlights = False
                    break
            else:
                if in_highlights:
                    highlights.append(line)

        title, url = fields.get("Title", ""), fields.get("URL", "")
        if not title or not url:
            continue
        published, author = fields.get("Published", ""), fields.get("Author", "")
        items.append({
            "kind": "article",
            "title": title,
            "url": url,
            "published_at": None if published in ("", "N/A") else published,
            "source": "exa",
            "summary": " ".join(" ".join(highlights).split())[:300],
            "author": "" if author == "N/A" else author,
        })
    return items


def search(config: dict) -> list[dict]:
    queries = config.get("exa_queries") or ([config["exa_query"]] if config.get("exa_query") else [])
    if not queries:
        return []
    limit = int(config.get("exa_limit", 20))
    timeout = int(config.get("exa_timeout_s", 60))

    items: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        for item in _parse(_call_direct(query, limit, timeout)):
            if not item["url"] or item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items[:limit]
