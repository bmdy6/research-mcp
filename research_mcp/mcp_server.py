"""Catalog/search read-only stdio MCP adapter. Run with research-mcp mcp."""
from __future__ import annotations

import asyncio
import sys


from research_mcp import service as S  # noqa: E402
from research_mcp.result_contract import ResultBudget, serialize_result  # noqa: E402

try:  # mcp >= 2 把 FastMCP 改名成 MCPServer，两个名字都留着兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
    except ImportError:
        print(
            "缺少 mcp 包。安装：pip install 'research-mcp[mcp]'",
            file=sys.stderr,
        )
        raise SystemExit(1)

mcp = FastMCP("research")

MAX_CHARS = 200_000

# 只读工具：不写文件、不改状态，只访问外部公开来源。声明出来，
# 客户端（如 Codex）才会在非交互模式下自动放行，不必逐次审批。
try:
    from mcp.types import ToolAnnotations

    _TOOL_KWARGS = {"annotations": ToolAnnotations(
        title="Search or read sources",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    )}
except ImportError:  # pragma: no cover  旧版 SDK 没有工具注解
    _TOOL_KWARGS = {}

try:
    _tool = mcp.tool(**_TOOL_KWARGS)
except TypeError:  # pragma: no cover  旧版 FastMCP.tool() 不接受 annotations
    _tool = mcp.tool()


@_tool
async def research_search(query: str, limit: int = 12, providers: list[str] | None = None) -> str:
    """一个入口两种用法。

    传关键词：在定向渠道里搜（GitHub 仓库、Hacker News、B站视频、掘金文章、全网网页 Exa），
    返回 v1 JSON 对象（status/candidates/total/returned/delivered/errors/display_notes）。
    先调用 research_list_channels，再按问题选择 providers；不做模型路由或重排。

    传 http(s) 链接：读那条内容——B站视频返回音频转写的文字稿，网页和 PDF 返回 Markdown 正文，
    GitHub 按仓库、Issue、PR、blob 文件分流；讨论分页范围与继续链接见 coverage。

    Args:
        query: 搜索关键词，或要读取的 http(s) 链接。
        limit: 搜索时最多返回条数，1~40，默认 12（传链接时忽略）。
        providers: 省略时使用可配置的免费默认组；建议先用 research_list_channels 查看目录。可选 github_search / hacker_news /
            arxiv / exa / juejin / bilibili / baidu / sogou / bing / duckduckgo。搜单个站（如 linux.do、知乎）用 ["exa"] 配
            `site:域名 关键词` 的查询词（传链接时忽略）。
    """
    try:
        normalized_limit = max(1, min(int(limit), 40))
    except (TypeError, ValueError, OverflowError):
        normalized_limit = limit
    # Keep blocking provider/subprocess work off the stdio event loop. A
    # cancelled await discards the result; an already-running thread finishes
    # under the core's existing timeouts and retains its normal cleanup. The
    # event loop owns the shared executor and bounds its active worker count.
    result = await asyncio.to_thread(
        S.query, query, limit=normalized_limit, providers=providers)
    return serialize_result(result, ResultBudget(max_chars=MAX_CHARS))


@_tool
async def research_list_channels() -> str:
    """列出免费渠道、配置条件和最近检查状态；不发请求，不返回密钥。

    由调用方 agent 按问题挑选 providers；未实现的渠道不能搜索，unchecked 不代表在线可用。
    """
    result = await asyncio.to_thread(S.list_channels)
    return serialize_result(result, ResultBudget(max_chars=MAX_CHARS))


if __name__ == "__main__":
    mcp.run()
