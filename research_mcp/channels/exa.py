"""Exa 全网语义搜索渠道：用系统 curl 直连 Exa 官方免 key MCP 端点。

支持多个查询词（`exa_queries`）。用 `site:zhihu.com 关键词` 这类站内限定查询，可以覆盖
知乎这类正文被反爬挡住、又没有免 cookie 搜索接口的平台——拿到标题、摘要和链接，
正文能不能读是另一回事。小红书实测索引很弱（只到用户主页），别指望这条路径。

免费 MCP 端点有调用频率限制，撞上返回 429（`You've hit Exa's free MCP rate limit.`）。
限流时立即停止，并在本进程内记一段时间，
后续调用直接跳过——同一轮对话里连续重试没有意义，只会白等。

只调用免 key 端点，不读取 EXA_API_KEY。额度耗尽后停止，不切换付费服务。
系统 curl 使用正常 TLS 校验，避免部分 macOS Python 安装缺少 CA 导致连接失败。

搜索工具返回的是人读文本（Title/URL/Published/Author/Highlights 块），这里按块解析；
上游改了输出格式就抛错降级，不阻塞其他渠道。
"""

from __future__ import annotations

import json
import re
import time
import subprocess
import tempfile
from pathlib import Path

from research_mcp.commands import run as run_command
from research_mcp.channels.errors import ChannelError

BLOCK_SPLIT = re.compile(r"\n\s*-{3,}\s*\n")
FIELD_KEYS = ("Title", "URL", "Published", "Author")

FREE_ENDPOINT = "https://mcp.exa.ai/mcp"
TOOL_NAME = "web_search_exa"
RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "resource_exhausted", "too many requests")
# 一次限流后本进程内跳过的时长；不对上游额度恢复时间作保证。
SKIP_WINDOW_SECONDS = 300.0

_limited_until = 0.0


def endpoint_selector() -> str:
    """Free-only: a paid key in the host environment never changes the endpoint."""
    return FREE_ENDPOINT


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


def _post(message: dict, headers: dict, timeout: int) -> tuple[int, dict, str]:
    """Bounded curl transport with normal certificate verification and cleanup."""
    try:
        with tempfile.TemporaryDirectory(prefix="research-exa-") as directory:
            body_file, header_file = Path(directory) / "body", Path(directory) / "headers"
            command = ["curl", "-q", "-sS", "--max-time", str(timeout),
                       "--max-filesize", "2000000", "--output", str(body_file),
                       "--dump-header", str(header_file), "--write-out", "%{http_code}",
                       "--data-binary", "@-"]
            for name, value in headers.items():
                command.extend(["-H", f"{name}: {value}"])
            command.append(FREE_ENDPOINT)
            result = run_command(command, input=json.dumps(message), capture_output=True,
                                 text=True, timeout=timeout + 2)
            if result.returncode:
                raise ChannelError("timeout" if result.returncode == 28 else
                                   "parse_error" if result.returncode == 63 else "connection_failed")
            status = int(result.stdout.strip())
            response_headers = {}
            with header_file.open("rb") as stream:
                header_bytes = stream.read(65_537)
            if len(header_bytes) > 65_536:
                raise ChannelError("parse_error")
            for line in header_bytes.decode("latin-1").splitlines():
                if line.startswith("HTTP/"):
                    response_headers = {}  # discard proxy/informational response headers
                elif ":" in line:
                    name, value = line.split(":", 1)
                    response_headers[name.lower()] = value.strip()
            with body_file.open("rb") as stream:
                raw = stream.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ChannelError("parse_error")
            return status, response_headers, raw.decode("utf-8")
    except subprocess.TimeoutExpired:
        raise ChannelError("timeout") from None
    except (FileNotFoundError, RuntimeError) as error:
        if isinstance(error, ChannelError):
            raise
        raise ChannelError("startup_failed") from None
    except (OSError, ValueError):
        raise ChannelError("parse_error") from None


def _rpc(method: str, params: dict, identity: int | None, headers: dict, timeout: int):
    """One Streamable HTTP POST; accept JSON or finite SSE response events."""
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if identity is not None:
        message["id"] = identity
    status, response_headers, text = _post(message, headers, timeout)
    if status == 429 or status == 402:
        raise ChannelError("quota_exhausted")
    if status == 401:
        raise ChannelError("auth_required")
    if status == 403:
        raise ChannelError("blocked")
    if not 200 <= status < 300:
        raise ChannelError("connection_failed")
    if response_headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = response_headers["mcp-session-id"]
    if identity is None:
        return {}
    try:
        if "text/event-stream" in response_headers.get("content-type", ""):
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
    except (ValueError, TypeError):
        raise ChannelError("parse_error") from None
    if not isinstance(payload, dict) or payload.get("id") != identity:
        raise ChannelError("parse_error")
    if payload.get("error"):
        raise ChannelError("quota_exhausted" if _is_rate_limited(json.dumps(payload["error"])) else "connection_failed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ChannelError("parse_error")
    return result


def _call_direct(query: str, limit: int, timeout: int) -> str:
    if _rate_limit_skip_active():
        raise ChannelError("quota_exhausted")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "User-Agent": "research-mcp/0.1"}
    try:
        initialized = _rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "research-mcp", "version": "0.1.0"}}, 1, headers, timeout)
        headers["MCP-Protocol-Version"] = initialized.get("protocolVersion", "2024-11-05")
        _rpc("notifications/initialized", {}, None, headers, timeout)
        tools = _rpc("tools/list", {}, 2, headers, timeout)
        if not any(tool.get("name") == TOOL_NAME for tool in tools.get("tools", []) if isinstance(tool, dict)):
            raise ChannelError("parse_error")
        payload = _rpc("tools/call", {"name": TOOL_NAME, "arguments": {
            "query": query, "numResults": limit}}, 3, headers, timeout)
        content = payload.get("content")
        if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
            raise ChannelError("parse_error")
        text = "\n".join(block.get("text", "") for block in content if block.get("type", "text") == "text")
        if payload.get("isError"):
            raise ChannelError("quota_exhausted" if _is_rate_limited(text) else "connection_failed")
        return text
    except RuntimeError as error:
        if ((isinstance(error, ChannelError) and error.status == "quota_exhausted")
                or _is_rate_limited(str(error))):
            _remember_rate_limit()
            raise ChannelError("quota_exhausted") from None
        raise


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
        text = _call_direct(query, limit, timeout)
        parsed = _parse(text)
        if text.strip() and not parsed and not re.fullmatch(r"(?:No (?:search )?results(?: found)?[.!]?|No relevant results found[.!]?)", text.strip(), re.I):
            raise ChannelError("parse_error")
        for rank, item in enumerate(parsed, 1):
            item.update(rank=rank, actual_backend="Exa official keyless MCP")
            if not item["url"] or item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
    return items[:limit]
