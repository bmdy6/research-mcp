"""Shared, read-only research service operations for local transports.

Transport adapters may validate narrower policies before calling these
functions. This module owns the common result envelope and replaces raw
provider failure text with short safe categories.
"""
from __future__ import annotations

import re
import uuid


from research_mcp.commands import HINTS
from research_mcp import research as R  # noqa: E402
from research_mcp.result_contract import error_result, fetch_result, search_result  # noqa: E402
from research_mcp.safe_fetch import FAKE_IP_MESSAGE, SafeFetcher, SafeFetchError  # noqa: E402


MAX_SEARCH_LIMIT = 40
# Public adapters must never inherit private CLI defaults, gh or rerank identity.
PUBLIC_SEARCH_PROVIDERS = frozenset({"hacker_news", "juejin"})
PUBLIC_MAX_QUERY_CHARS = 500
PUBLIC_MAX_SEARCH_LIMIT = 20
PUBLIC_SEARCH_AGE_DAYS = frozenset({7, 30, 90})
_ACCESS_CHALLENGE_SCRIPT = re.compile(
    r'''<script\b[^>]*\s+src\s*=\s*["']https://lf-waf-js\.byted-static\.com/obj/waf-jschallenge/[^"']*["']''',
    re.IGNORECASE,
)


def _safe_failure_reason(value: object) -> str:
    """Map provider text to a small safe category; never echo raw exceptions."""
    if isinstance(value, str):
        for hint in HINTS.values():
            if hint in value:
                return hint
    # Exact core-owned messages are safe and retain the HTTP diagnosis for MCP.
    if isinstance(value, str) and value in (
        str(SafeFetchError("UNSAFE_ADDRESS")), FAKE_IP_MESSAGE,
        "GitHub 资源不存在或当前身份不可见（404）",
        "GitHub 文件或 ref 不存在或当前身份不可见（404）",
        "GitHub 认证不可用（401）", "GitHub 权限不足（403）",
    ):
        return value
    text = value.casefold() if isinstance(value, str) else ""
    if "429" in text or "rate limit" in text or "限流" in text:
        return "上游限流"
    if "timeout" in text or "timed out" in text or "超时" in text:
        return "上游超时"
    if "401" in text or "权限" in text or "认证" in text:
        return "上游权限不足或认证不可用"
    if "不支持" in text:
        return "不支持的 URL 类型或读取参数"
    if "403" in text or "forbidden" in text or "拒绝" in text:
        return "上游拒绝请求"
    if "404" in text or "not found" in text or "不存在" in text:
        return "资源不存在"
    return "上游请求失败"


def _sanitize(result: dict) -> dict:
    if result.get("operation") == "search":
        for failure in result.get("failed_channels", []):
            if isinstance(failure, dict):
                failure["error"] = _safe_failure_reason(failure.get("error"))
        downgraded = result.get("rerank_downgraded")
        if isinstance(downgraded, str) and downgraded.startswith("rerank_error:"):
            result["rerank_downgraded"] = "rerank_error"
    elif result.get("operation") == "fetch":
        data = result.get("data")
        if isinstance(data, dict):
            if data.get("ok") is False:
                data["error"] = _safe_failure_reason(data.get("error"))
            if data.get("transcript_error"):
                data["transcript_error"] = "视频转写不可用"
        for error in result.get("errors", []):
            if isinstance(error, dict) and error.get("code") == "READ_FAILED":
                error["message"] = _safe_failure_reason(
                    (data or {}).get("error") if isinstance(data, dict) else error.get("message")
                )
    return result


def _identified(result: dict, request_id: str | None = None) -> dict:
    if not isinstance(result, dict):
        result = error_result("unknown", "INVALID_RESULT", "结果格式无效")
    result.setdefault("api_version", "1")
    result["request_id"] = request_id or uuid.uuid4().hex
    return result


def search(query: object, *, limit: object = 12, providers: object = None, rerank: bool = True,
           request_id: str | None = None, max_age_days: object = None) -> dict:
    """Search using the existing research core and return its structured v1 envelope."""
    if not isinstance(query, str) or not query.strip():
        return _identified(error_result("search", "INVALID_INPUT", "查询不能为空"), request_id)
    if type(limit) is not int or not 1 <= limit <= MAX_SEARCH_LIMIT:
        return _identified(error_result("search", "INVALID_INPUT", "limit 必须是 1 到 40 的整数"), request_id)
    if providers is not None and (
        not isinstance(providers, list)
        or any(not isinstance(name, str) or not name.strip() for name in providers)
    ):
        return _identified(error_result("search", "INVALID_INPUT", "providers 必须是渠道名数组"), request_id)
    if type(rerank) is not bool:
        return _identified(error_result("search", "INVALID_INPUT", "rerank 必须是布尔值"), request_id)
    if max_age_days is not None and (type(max_age_days) is not int or not 1 <= max_age_days <= 3650):
        return _identified(error_result("search", "INVALID_INPUT", "max_age_days 必须是 1 到 3650 的整数"), request_id)
    try:
        if max_age_days is None:
            raw = R.search(query.strip(), limit=limit, providers=providers, rerank=rerank)
        else:
            raw = R.search(query.strip(), limit=limit, providers=providers, rerank=rerank,
                           max_age_days=max_age_days)
        result = search_result(raw)
        if not rerank and "rerank_downgraded" not in result:
            result["rerank_downgraded"] = "disabled_by_transport_policy"
    except Exception:
        result = error_result("search", "UPSTREAM_ERROR", "上游调用失败，未取得完整结果")
    return _identified(_sanitize(result), request_id)


def fetch(url: object, *, request_id: str | None = None) -> dict:
    """Read one URL with the existing core and return the structured v1 envelope."""
    if not isinstance(url, str) or not url.strip() or not url.strip().startswith(("http://", "https://")):
        return _identified(error_result("fetch", "INVALID_INPUT", "仅接受 http(s) URL"), request_id)
    try:
        result = fetch_result(R.fetch_url(url.strip()))
    except Exception:
        result = error_result("fetch", "UPSTREAM_ERROR", "上游读取失败，未取得完整结果")
    return _identified(_sanitize(result), request_id)


def search_public(query: object, *, limit: object = 12, providers: object = None,
                  request_id: str | None = None, max_age_days: object = None) -> dict:
    """Anonymous-provider boundary, independent of a caller's claimed identity.

    Deliberately no default providers, rerank option, caller identity or cache.
    Private CLI/MCP continue using search()/fetch() and their existing policy.
    This is provider-scope isolation, not public authentication or cost control.
    """
    if (not isinstance(providers, list) or not providers
            or any(not isinstance(name, str) or name not in PUBLIC_SEARCH_PROVIDERS for name in providers)
            or len(set(providers)) != len(providers)):
        return _identified(error_result("search", "SCOPE_DENIED", "需要显式指定允许的匿名渠道"), request_id)
    if (not isinstance(query, str) or not query.strip() or len(query.strip()) > PUBLIC_MAX_QUERY_CHARS
            or type(limit) is not int or not 1 <= limit <= PUBLIC_MAX_SEARCH_LIMIT
            or (max_age_days is not None and
                (type(max_age_days) is not int or max_age_days not in PUBLIC_SEARCH_AGE_DAYS))):
        return _identified(error_result("search", "INVALID_INPUT", "查询或数量超出公开读取边界"), request_id)
    if query.strip().lower().startswith(("http://", "https://")):
        return _identified(error_result("search", "CAPABILITY_DISABLED", "此搜索入口不执行 URL 读取"), request_id)
    if max_age_days is None:
        return search(query, limit=limit, providers=list(providers), rerank=False, request_id=request_id)
    return search(query, limit=limit, providers=list(providers), rerank=False,
                  request_id=request_id, max_age_days=max_age_days)


def fetch_public(url: object, *, fetcher: SafeFetcher | None = None,
                 request_id: str | None = None) -> dict:
    """Explicit safe-fetch dependency only; never call the personal fetch core.

    No HTTP route enables this operation yet. Raw text/HTML remains untrusted
    source content; a consumer must not render it as executable HTML.
    """
    if not isinstance(fetcher, SafeFetcher):
        return _identified(error_result("fetch", "CAPABILITY_DISABLED", "公开读取需要显式安全传输"), request_id)
    try:
        document = fetcher.fetch(url)
        content = document.body.decode("utf-8")
        if "\x00" in content:
            raise SafeFetchError("UNSUPPORTED_CONTENT")
        # This observed HTTP-200 WAF response has no article body to review.
        if document.media_type == "text/html" and _ACCESS_CHALLENGE_SCRIPT.search(content):
            return _identified(error_result("fetch", "ACCESS_CHALLENGE",
                                            "上游返回访问校验页，正文未读取"), request_id)
        result = fetch_result({"kind": "article", "url": document.url, "content": content,
                               "via": "public_https_raw", "ok": True})
    except UnicodeDecodeError:
        result = error_result("fetch", "UNSUPPORTED_CONTENT", "公开读取仅支持 UTF-8 文本")
    except SafeFetchError as error:
        result = error_result("fetch", error.code, str(error))
    except Exception:
        result = error_result("fetch", "TRANSPORT_FAILED", "公开读取传输失败")
    return _identified(result, request_id)


def query(query_text: object, *, limit: object = 12, providers: object = None,
          request_id: str | None = None, rerank: bool = True) -> dict:
    """MCP-compatible operation selector: URL input reads; other text searches."""
    if isinstance(query_text, str) and query_text.strip().startswith(("http://", "https://")):
        return fetch(query_text, request_id=request_id)
    return search(query_text, limit=limit, providers=providers, rerank=rerank, request_id=request_id)
