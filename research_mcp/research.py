#!/usr/bin/env python3
"""Keyword search and URL reading core."""
from __future__ import annotations

import base64
import json
import re
import shlex
import subprocess
from research_mcp.commands import run as run_command
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable


from research_mcp.channels import CHANNELS  # noqa: E402  渠道层；键名即 search.json 里的 provider 名
from research_mcp.result_contract import EXA_X_EMPTY_NOTE  # noqa: E402  与入口共用固定安全提示
from research_mcp.tools.ldjson import (  # noqa: E402  图表页的内嵌数据兑底
    extract_chart_datasets,
    looks_like_chart_page,
    render_chart_datasets,
)
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}


def normalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit((value or "").strip())
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    if parsed.port and not ((scheme == "https" and parsed.port == 443) or (scheme == "http" and parsed.port == 80)):
        host = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    if host == "github.com":
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            path = "/" + "/".join(parts[:2]).lower()
            if path.endswith(".git"):
                path = path[:-4]
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    )
    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def candidate_key(item: dict) -> str:
    repo = item.get("repo") or {}
    full_name = repo.get("full_name") or item.get("full_name")
    repo_url = item.get("code_url") or (item.get("url") if item.get("kind") == "github" else None)
    if not full_name and repo_url:
        parsed = urllib.parse.urlsplit(normalize_url(repo_url))
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.hostname == "github.com" and len(parts) >= 2:
            full_name = f"{parts[0]}/{parts[1]}"
    if full_name:
        return f"github:{str(full_name).strip().lower()}"
    return f"url:{normalize_url(item.get('url', ''))}"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class FetchError(RuntimeError):
    """抓取失败。带上可直接展示给调用方的原因，不要让它再看到调用栈。"""

    def __init__(self, reason: str, *, code: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.code = code if code == "UNSAFE_ADDRESS" else None


_CURL_ERROR = re.compile(r"curl: \((\d+)\)\s*(.*)", re.S)


def _describe_curl_failure(stderr: str, timeout: int) -> str:
    """把 curl 的报错转成一句人话，用于 fetch 的失败说明。"""
    message = (stderr or "").strip()
    matched = _CURL_ERROR.search(message)
    if matched:
        code, detail = matched.group(1), " ".join(matched.group(2).split())
        http_status = re.search(r"error:\s*(\d{3})", detail)
        if code == "28":
            return f"请求超时（{timeout} 秒内没有响应）"
        if http_status:
            return f"页面取不到（HTTP {http_status.group(1)}）"
        if code == "56":
            return "页面取不到（上游拒绝了连接）"
        if code == "6":
            return "域名解析失败"
        return f"请求失败（curl {code}）"
    return message[:200] or "请求失败"


def _curl_text(url: str, *, method: str = "GET", payload: dict | None = None, headers: dict | None = None, timeout: int = 20) -> str:
    command = ["curl", "-sS", "-L", "--fail", "--max-time", str(timeout), "-H", "User-Agent: research-discover/1.0"]
    for key, value in (headers or {}).items():
        command.extend(["-H", f"{key}: {value}"])
    body = None
    if method != "GET":
        command.extend(["-X", method])
    if payload is not None:
        command.extend(["--data-binary", "@-"])
        body = json.dumps(payload)
    command.append(url)
    try:
        result = run_command(command, input=body, capture_output=True, text=True, timeout=timeout + 5)
    except RuntimeError as error:
        raise FetchError(str(error)) from None
    if result.returncode != 0:
        raise FetchError(_describe_curl_failure(result.stderr, timeout))
    return result.stdout


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None, headers: dict | None = None) -> dict:
    return json.loads(_curl_text(url, method=method, payload=payload, headers=headers))


def _http_text(url: str) -> str:
    return _curl_text(url)


def _safe_http_text(url: str) -> str:
    """Read through the existing bounded DNS and pinned HTTPS transport."""
    from research_mcp.safe_fetch import SafeFetcher, FetchLimits, SafeFetchError
    from research_mcp.safe_dns import SubprocessResolver
    from research_mcp.safe_transport import PinnedIPTransport
    try:
        document = SafeFetcher(resolver=SubprocessResolver(), transport=PinnedIPTransport(),
                               limits=FetchLimits(max_bytes=1_048_576, total_seconds=30)).fetch(url)
        return document.body.decode("utf-8", errors="replace")
    except SafeFetchError as error:
        raise FetchError(str(error), code=error.code) from None


def _validate_fetch_url(url: str) -> None:
    """Reject unsafe target URLs and mixed DNS answers before a reader call."""
    import time
    from research_mcp.safe_fetch import _url, _public_ip, Deadline, SafeFetchError
    from research_mcp.safe_dns import SubprocessResolver
    try:
        _canonical, host, literal = _url(url)
        if literal is None:
            for address in SubprocessResolver()(host, deadline=Deadline(10, time.monotonic)):
                _public_ip(address)
    except SafeFetchError as error:
        raise FetchError(str(error), code=error.code) from None


JINA_PREFIX = "https://r.jina.ai/"
# Jina Reader 读不到时会回一句占位内容（目标站反爬），得认出来才能回落到直接抓取
_JINA_FAILED = re.compile(r"Warning: Target URL returned error|^Title:\s*$", re.M)

# 空壳判定：转成功了但正文没实质内容。SHELL 模式本身没渲染 JS，返回的
# “Loading...” 占位、只剩 Login 的登录墙，都是这种。宁可说“没抓到”，
# 也不能返回空壳还标成功。
_SHELL_HINT = re.compile(r"^\s*(loading|log ?in|sign ?in|please enable javascript|正在加载|登录|跳转中)" + r"[\s.。！!]*$", re.I)
# 多词登录墙（“Sign in to continue”）整行匹配不上，再补一层：行短且以登录类动词开头
_SHELL_VERB = re.compile(r"^\s*(loading|log ?in|sign ?in|sign ?up|please enable javascript|正在加载|登录|跳转中)\b", re.I)
_MIN_MEANINGFUL_CHARS = 200


def _body_after_marker(text: str) -> str | None:
    """取 Jina 分段标记之后的正文；没有标记返回 None。"""
    marker = "Markdown Content:"
    return text.split(marker, 1)[1] if marker in text else None


def _after_jina_header(text: str) -> str:
    """Jina 输出前面有一段 Title/URL Source/Warning/Markdown Content 头，不是正文。"""
    marker = "Markdown Content:"
    return text.split(marker, 1)[1] if marker in text else text


def _looks_like_shell(text: str) -> bool:
    """Jina 转出来的是不是“看着成功、实际没内容”的空壳。"""
    body = _after_jina_header(text).strip()
    if not body:
        return True
    if len(re.sub(r"\s+", "", body)) >= _MIN_MEANINGFUL_CHARS:
        return False
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return True
    # 有句子标点或成句长度的，算真内容，短页面不能被误杀
    if any(re.search(r"[。！？!?；;]", line) or len(line) >= 40 for line in lines):
        return False
    return any(_SHELL_HINT.match(line) for line in lines)


_SPONSOR_HEADING = re.compile(
    r"^(#{1,6})\s*(?:"
    r"(?:(?:gold|silver|bronze|our|current|project)\s+)?sponsors?|sponsorship|"
    r"support\s+(?:us|this\s+project|the\s+project)|"
    r"donate|donations?|funding|backers?|patreon|buy\s+me\s+a\s+coffee|"
    r"connect\s+with\s+me|contact\s+me|socials?"
    r")\b.*$",
    re.I,
)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINKED_IMAGE = re.compile(r"\[\s*!\[[^\]]*\]\([^)]*\)\s*\]\([^)]*\)")
_HTML_LINKED_IMAGE = re.compile(r"<a\b[^>]*>\s*<img\b[^>]*>\s*</a>", re.I)
_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.I)
_HTML_ANCHOR_WRAPPER = re.compile(r"^\s*</?a\b[^>]*>\s*$", re.I)
_HORIZONTAL_RULE = re.compile(r"^\s*(?:---+|___+|\*\*\*+)\s*$")
_SOCIAL_ONLY_LINK = re.compile(
    r"^(?:\s*[·|/•-]?\s*\[(?:discord|telegram|twitter|x|chat|community|deepwiki|trendshift|cloudback|sponsor|patreon)\]\([^)]*\)\s*)+$",
    re.I,
)


def _heading_level(line: str) -> int | None:
    m = re.match(r"^(#{1,6})\s+", line.strip())
    return len(m.group(1)) if m else None


def _looks_like_badge_only_line(line: str) -> bool:
    """只删纯 badge/社交徽章行；带正常正文的图片行保留正文。"""
    stripped = line.strip()
    if not stripped:
        return False
    if _SOCIAL_ONLY_LINK.fullmatch(stripped):
        return True
    had_badge = bool(
        _MARKDOWN_LINKED_IMAGE.search(stripped)
        or _MARKDOWN_IMAGE.search(stripped)
        or _HTML_LINKED_IMAGE.search(stripped)
        or _HTML_IMAGE.search(stripped)
        or "shields.io" in stripped.lower()
        or "badge" in stripped.lower()
    )
    if not had_badge:
        return False
    visible = _MARKDOWN_LINKED_IMAGE.sub("", stripped)
    visible = _MARKDOWN_IMAGE.sub("", visible)
    visible = _HTML_LINKED_IMAGE.sub("", visible)
    visible = _HTML_IMAGE.sub("", visible)
    visible = re.sub(r"<[^>]+>", "", visible)
    visible = re.sub(r"\[[^\]]*\]\([^)]*\)", "", visible)
    visible = re.sub(r"[\s|·•:;,_-]+", "", visible)
    return len(visible) <= 8


def _clean_readable_markdown(text: str) -> str:
    """移除高置信度上下文噪音，不做激进正文重写。"""
    lines = text.splitlines()
    kept: list[str] = []
    skipping_noise_section = False

    for line in lines:
        stripped = line.strip()
        if skipping_noise_section:
            # README 标题层级经常写乱，不能只等“同级标题”；分隔线或任何新的
            # 非噪音标题都视为区块结束。新的噪音标题则继续跳过。
            if _HORIZONTAL_RULE.match(stripped):
                skipping_noise_section = False
                kept.append(line.rstrip())
                continue
            if _heading_level(line) is not None:
                if _SPONSOR_HEADING.match(stripped):
                    continue
                skipping_noise_section = False
                # 当前新标题属于正文，继续走正常保留逻辑。
            else:
                continue

        if _SPONSOR_HEADING.match(stripped):
            skipping_noise_section = True
            continue
        if _HTML_ANCHOR_WRAPPER.match(stripped):
            continue
        if _looks_like_badge_only_line(line):
            continue
        kept.append(line.rstrip())

    # 连续空行最多保留一行；连续区块被删除后，重复分隔线也只留一个。
    compact: list[str] = []
    last_nonempty_was_rule = False
    for line in kept:
        stripped = line.strip()
        if not stripped:
            if compact and not compact[-1].strip():
                continue
            compact.append(line)
            continue
        is_rule = bool(_HORIZONTAL_RULE.match(stripped))
        if is_rule and last_nonempty_was_rule:
            continue
        compact.append(line)
        last_nonempty_was_rule = is_rule
    return "\n".join(compact).strip()


def _is_placeholder_only(text: str) -> bool:
    """直接抓的原始内容是不是只剩占位词（如只剩 Login 的登录墙）。

    空内容也算：拿个空 body 当成功，和空壳是一回事。
    """
    body = _body_after_marker(text)
    # 带 Jina 标记却没有正文，就是空壳（别回落到 Jina 头文本上判）
    if body is not None:
        text = body
    lines = [line.strip() for line in _after_jina_header(text).splitlines() if line.strip()]
    if not lines:
        return True
    # 整行占位词，或短行以登录/加载类词开头（“Sign in to continue”），都算占位
    return all(_SHELL_HINT.match(line) or (_SHELL_VERB.match(line) and len(line) < 60) for line in lines)


def _readable_content(url: str) -> tuple[str, str]:
    """把网页变可读正文，返回 (内容, 来源方式)。

    直接用 curl 抓回来的是带样式和脚本的原始 HTML，模型读不了；先请 Jina Reader
    （https://r.jina.ai/<url>）转成 Markdown 正文，它自己失败时（目标站反爬、
    返回 -403 占位内容）再回落原始抓取。

    Jina 返回空壳时也算失败：
    “没查到”和“没查成”是两个结论，混在一起没法用。

    评测站/榜单把数值挂在图表上，Jina 拿到的正文只剩 “higher is better” 这类图注，
    内容其实在页面内嵌的 JSON-LD 里；这种情况补抽数据集（via=ldjson）。
    """
    _validate_fetch_url(url)
    jina_failed = ""
    shell_only = False
    jina_text = ""
    try:
        text = _safe_http_text(JINA_PREFIX + url)
        if text.strip() and not _JINA_FAILED.search(text[:300]):
            if not _looks_like_shell(text):
                cleaned = _clean_readable_markdown(text)
                if not looks_like_chart_page(cleaned):
                    return cleaned, "jina"
                jina_text = cleaned
            else:
                shell_only = True
        jina_failed = "正文是空壳（页面靠 JS 渲染，服务端拿不到内容）"
    except FetchError as error:
        jina_failed = error.reason
    except Exception:
        jina_failed = "Jina Reader 转写失败"

    # 图表页：正文在图注之外还有内容，先把内嵌数据集补上。
    # 补不到时退回原正文并标注不完整，不要直接报失败：
    # 图表页判定是启发式（正文里图注出现多次），正常评测/定价文章也有被误判的可能，
    # 一条能读的文章不应该因为没抽到数据而变成读不了。
    if jina_text:
        datasets: list[tuple[str, list[dict]]] = []
        try:
            raw = _safe_http_text(url)
        except FetchError:
            raw = ""
        if raw:
            datasets = extract_chart_datasets(raw)
        if datasets:
            supplement = render_chart_datasets(datasets)
            return (
                f"{jina_text.rstrip()}\n\n---\n\n"
                f"## 页面内嵌数据（图表数值，从页面结构化载荷抽取）\n\n{supplement}",
                "ldjson",
            )
        return (
            f"{jina_text.rstrip()}\n\n---\n\n"
            "⚠️ 以上正文只剩图表说明，页面里的内嵌数据没抽到；需要具体数值请换来源或用浏览器打开。",
            "chart_page_incomplete",
        )

    # 只有拿到空壳（而非 Jina 报错）说明静态抓取拿不到内容；此时原始 HTML 也没用，
    # 因为 JS 根本没跑。直接说清需要浏览器，不拿空壳或埋在一堆标签里的原文充数。
    if shell_only:
        raise FetchError(f"{jina_failed}；这条需要浏览器渲染或登录，换来源")
    try:
        raw = _safe_http_text(url)
    except FetchError as error:
        raise FetchError(error.reason, code=error.code) from error
    # Jina 报错时仍回落原始内容（via=raw 会标注来源），只有连它都只剩占位词才算失败
    if _is_placeholder_only(raw):
        raise FetchError(f"{jina_failed}；直接抓取也没拿到内容")
    return _clean_readable_markdown(raw), "raw"


def _run_json(command: list[str]) -> list[dict]:
    result = run_command(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300] or "command failed")
    return json.loads(result.stdout or "[]")


def _run_text(command: list[str]) -> str:
    result = run_command(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300] or "command failed")
    return result.stdout


def _github_search(config: dict) -> list[dict]:
    queries = config.get("github_queries") or [config.get("github_query", "agentic-ai")]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(config.get("max_age_days", 90)))).date().isoformat()
    fields = "name,fullName,description,stargazersCount,forksCount,pushedAt,createdAt,updatedAt,license,homepage,isArchived,isFork,url,language"
    found = {}
    for query in queries:
        rows = _run_json(["gh", "search", "repos", str(query), "--match", "readme", "--updated", f">{cutoff}", "--archived=false", "--include-forks=false", "--sort", "updated", "--order", "desc", "--limit", str(config.get("github_limit", 30)), "--json", fields])
        for row in rows:
            full_name = (row.get("fullName") or "").lower()
            if full_name:
                found.setdefault(full_name, row)
    return [_github_item(row, "github_search") for row in found.values()]


def _github_item(row: dict, source: str) -> dict:
    full_name = row.get("fullName") or row.get("full_name") or ""
    return {
        "kind": "github",
        "title": full_name or row.get("name", ""),
        "url": normalize_url(row.get("url") or f"https://github.com/{full_name}"),
        "published_at": row.get("createdAt") or row.get("pushedAt"),
        "source": source,
        "summary": row.get("description") or "",
        "repo": {"full_name": full_name.lower(), "stars": row.get("stargazersCount", 0), "language": row.get("language") or "", "has_code": True},
    }


def _hn_search(config: dict) -> list[dict]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=int(config.get("max_age_days", 90)))).timestamp())
    query = urllib.parse.quote(config.get("hn_query", "AI agent"))
    data = _http_json(f"https://hn.algolia.com/api/v1/search?query={query}&tags=story&numericFilters=created_at_i%3E{cutoff}&hitsPerPage={int(config.get('hn_limit', 30))}")
    return [{"kind": "article", "title": h.get("title", ""), "url": normalize_url(h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}"), "published_at": h.get("created_at"), "source": "hacker_news", "summary": h.get("_highlightResult", {}).get("title", {}).get("value", ""), "hn_score": h.get("points", 0)} for h in data.get("hits", []) if h.get("title")]


def _arxiv_search(config: dict) -> list[dict]:
    query = urllib.parse.quote(config.get("arxiv_query", "AI agent"))
    xml = _http_text(f"https://export.arxiv.org/api/query?search_query=all:{query}&sortBy=submittedDate&sortOrder=descending&max_results={int(config.get('arxiv_limit', 20))}")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    items = []
    for entry in root.findall("a:entry", ns):
        summary = " ".join((entry.findtext("a:summary", "", ns) or "").split())
        link = entry.findtext("a:id", "", ns).strip()
        code = re.search(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", summary)
        if not code:
            continue
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        published = entry.findtext("a:published", "", ns)
        items.append({"kind": "article", "title": title, "url": normalize_url(link), "published_at": published, "source": "arxiv", "summary": summary, "code_url": normalize_url(code.group(0))})
    return items


def _default_providers() -> dict[str, Callable[[dict], list[dict]]]:
    providers = {
        "github_search": _github_search,
        "hacker_news": _hn_search,
        "arxiv": _arxiv_search,
    }
    providers.update(CHANNELS)  # B站 / Exa 等渠道层实现，键名即 search.json 里的 provider 名
    return providers


def _describe_gh_failure(stderr: str, full_name: str) -> str:
    """把 gh 的报错转成一句人话（与 curl 失败同一口径）。"""
    message = " ".join((stderr or "").split())
    from research_mcp.github_read import failure_reason
    return failure_reason(message)


def _github_repo(full_name: str) -> dict:
    result = run_command(["gh", "api", "--hostname", "github.com", f"repos/{full_name}"], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise FetchError(_describe_gh_failure(result.stderr, full_name))
    return json.loads(result.stdout)


def _github_readme(full_name: str) -> str:
    result = run_command(["gh", "api", "--hostname", "github.com", f"repos/{full_name}/readme"], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise FetchError(_describe_gh_failure(result.stderr, full_name))
    payload = json.loads(result.stdout)
    raw = base64.b64decode(payload.get("content", "")).decode("utf-8", errors="replace")
    return _clean_readable_markdown(raw)


def fetch_url(url: str) -> dict:
    """读一条链接的内容。

    失败返回 `{"kind": "article", "ok": False, "error": 一句原因, "retryable": 是否值得重试}`
    而不是抛异常：调用方拿到的永远是一个能判断的结果，不用解读调用栈。
    """
    # Candidate normalization intentionally collapses GitHub URLs for dedup.
    # Reading must dispatch on the original path before that transformation.
    original = urllib.parse.urlsplit(url.strip())
    if original.hostname == "github.com":
        from research_mcp.github_read import read, GitHubReadError
        original_parts = [p for p in original.path.split("/") if p]
        safe_url = urllib.parse.urlunsplit(("https", "github.com", original.path, original.query, ""))
        try:
            if (len(original_parts) != 2 or original.username or original.password
                    or original.port not in (None, 443)):
                return read(url.strip())
        except (GitHubReadError, ValueError) as error:
            reason = error.reason if isinstance(error, GitHubReadError) else "不支持的 GitHub URL"
            return _failed_fetch("github", safe_url, FetchError(reason))
    try:
        if original.hostname != "github.com":
            _validate_fetch_url(url.strip())
    except FetchError as error:
        return _failed_fetch("article", url, error)
    normalized = normalize_url(url)
    parsed = urllib.parse.urlsplit(normalized)
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.hostname == "github.com" and len(parts) >= 2:
        full_name = f"{parts[0].lower()}/{parts[1].lower()}"
        # GitHub 走 gh CLI，不走 _readable_content；失败同样要给可判断的结果，不抛栈
        try:
            return {"kind": "github", "url": normalized, "repo": {"full_name": full_name}, "metadata": _github_repo(full_name), "object_type": "repository", "coverage_note": "仓库元数据和 README；未读取 Issue、PR 或其他文件", "content": _github_readme(full_name)}
        except (FetchError, json.JSONDecodeError, subprocess.SubprocessError, OSError, RuntimeError) as error:
            reason = error.reason if isinstance(error, FetchError) else str(error) if isinstance(error, RuntimeError) else "GitHub 返回的内容解析失败"
            return _failed_fetch("github", normalized, FetchError(reason))
    host = (parsed.hostname or "").lower()
    if host.endswith("bilibili.com") or host == "b23.tv":
        from research_mcp.tools.bili_transcript import transcript

        result = transcript(normalized)
        if result.get("ok"):
            return {"kind": "bilibili", "url": normalized, **result}
        # 转写不可用（缺 Groq key / 无音轨 / 超时）就读网页，至少拿到标题和简介
        try:
            content, via = _readable_content(normalized)
        except FetchError as error:
            return _failed_fetch("bilibili", normalized, error, transcript_error=result.get("error"), hint=result.get("hint"))
        return {"kind": "bilibili", "url": normalized, "via": via, "content": content, "transcript_error": result.get("error"), "hint": result.get("hint")}
    try:
        content, via = _readable_content(normalized)
    except FetchError as error:
        return _failed_fetch("article", normalized, error)
    return {"kind": "article", "url": normalized, "content": content, "via": via}


def _failed_fetch(kind: str, url: str, error: FetchError, **extra) -> dict:
    # 超时/限流重试可能有用；404 这类重试没意义，让调用方自己决定
    retryable = any(word in error.reason for word in ("超时", "限流", "429", "频率"))
    if error.code:
        extra["error_code"] = error.code
    return {"kind": kind, "url": url, "ok": False, "error": error.reason, "retryable": retryable, **extra}


# 榜单/订阅型渠道：输出是「当前最新」而不是「命中查询词」，临时搜索里带上只会灌无关结果。
# 新增关键词渠道默认会被 search 调用；只有这种与查询无关的渠道才需要登记到这里。
QUERY_AGNOSTIC_CHANNELS = frozenset({"github_trending", "rss"})


def _has_positive_x_site_filter(query: str) -> bool:
    """Recognize explicit domain filters, not mentions, quotes, or exclusions.

    This intentionally handles only unambiguous site tokens, not the full
    upstream query language. A missing note never proves index coverage.
    """
    try:
        tokens = shlex.split(query, posix=False)
    except ValueError:
        return False
    # Grouped exclusions are ambiguous without a full query parser. Omit the
    # note rather than interpreting a nested negative clause as positive.
    if any(re.match(r"\(*NOT(?:$|\()|\(*-\(", token, flags=re.IGNORECASE) for token in tokens):
        return False
    for token in tokens:
        token = token.strip("()")
        if re.fullmatch(r"site:(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)(?:/[^\s]*)?",
                        token, flags=re.IGNORECASE):
            return True
    return False


def _collect_channel(
    name: str,
    provider: Callable[[dict], list[dict]],
    config: dict,
) -> tuple[str, list[dict], str | None]:
    """调用单个渠道，把异常转成可展示的失败原因（并行调用时不能抛穿）。"""
    try:
        items = provider(config)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("渠道返回的候选格式无效")
        return name, items, None
    except Exception as error:
        return name, [], str(error)[:120] or type(error).__name__


def search(query: str, *, limit: int = 10, providers: list[str] | None = None, rerank: bool = True,
           max_age_days: int = 3650) -> dict:
    """按查询词打各个关键词渠道。

    providers 只传渠道名子集时只打这些渠道（例如只搜某个站就用 `["exa"]` 配
    `site:` 查询词）；不传则打全部关键词渠道。渠道名不存在或不可用只跳过，不报错。
    rerank 为 True 时，先用 Jev 判断哪些条目值得保留再返回；无 key 时正常跳过，调用失败时降级返回原始候选。

    渠道之间互不依赖、也不共享状态，所以并发调（只读工具的天然红利）。串行时
    总耗时是各渠道之和（实测 6 个渠道 7.5 秒），并发后接近最慢那个（约 2.5 秒）。

    返回值里的 `total` / `returned` / `truncated` 说明结果有没有被截：
    截断时调用方才能判断该不该补搜，而不是默认「看到的就是全部」。
    """
    # 临时搜索用本次查询词；B站按播放量取（每日发现场景才按时间+门槛）
    if type(max_age_days) is not int or not 1 <= max_age_days <= 3650:
        raise ValueError("max_age_days must be an integer from 1 to 3650")
    config = {"github_query": query, "hn_query": query, "arxiv_query": query, "exa_query": query,
              "juejin_queries": [query], "bilibili_queries": [query], "bilibili_order": "click",
              "max_age_days": max_age_days}
    available = _default_providers()
    wanted = providers if providers is not None else list(available)
    rows = []
    failed: list[tuple[str, str]] = []
    runnable: list[tuple[str, Callable[[dict], list[dict]]]] = []
    attempted = []
    for name in dict.fromkeys(wanted):
        if providers is None and name == "arxiv":
            continue
        if name in QUERY_AGNOSTIC_CHANNELS:
            continue
        attempted.append(name)
        provider = available.get(name)
        if provider is None:
            failed.append((name, "没有这个渠道"))
            continue
        runnable.append((name, provider))

    # 单渠道时不值得起线程池；多渠道并发，结果按 wanted 顺序拼回，保证输出稳定
    if len(runnable) <= 1:
        collected = [_collect_channel(name, provider, config) for name, provider in runnable]
    else:
        with ThreadPoolExecutor(max_workers=len(runnable)) as executor:
            futures = [executor.submit(_collect_channel, name, provider, config) for name, provider in runnable]
            collected = [future.result() for future in futures]
    for name, items, error in collected:
        if error is None:
            rows.extend(items)
        else:
            # 渠道失败必须能看见：少打一个渠道和“全网都没有”是两回事
            failed.append((name, error))
    # Filter the approved time window before interleaving/truncation so stale
    # high-ranked results cannot consume the candidate budget.
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    filtered_by = {}
    eligible_rows = []
    for item in rows:
        published_at = item.get("published_at")
        published = _parse_time(published_at) if isinstance(published_at, str) else None
        if published is not None and not cutoff <= published <= now:
            filtered_by["outside_time_range"] = filtered_by.get("outside_time_range", 0) + 1
        else:
            eligible_rows.append(item)
    rows = eligible_rows

    # 各渠道交错排列，避免 GitHub 一个源占满候选池
    buckets: dict[str, list[dict]] = {}
    keys: set[str] = set()
    for item in rows:
        key = candidate_key(item)
        if not key or key in keys:
            continue
        keys.add(key)
        buckets.setdefault(item.get("source", "unknown"), []).append(item)

    # 去重后的候选总数：截断信息要基于它，而不是被截完的 pool
    total_candidates = sum(len(bucket) for bucket in buckets.values())
    pool_size = max(limit * 2, 20)
    pool: list[dict] = []
    while len(pool) < pool_size and any(buckets.values()):
        for bucket in buckets.values():
            if bucket and len(pool) < pool_size:
                pool.append(bucket.pop(0))
    pooled = len(pool)

    rerank_ran = False
    if rerank:
        try:
            from research_mcp.rerank import filter_candidates
            result, downgrade = filter_candidates(query, pool, limit=limit)
            rerank_ran = downgrade is None
        except Exception as error:
            result, downgrade = pool[:limit], f"rerank_error: {str(error)[:80]}"
    else:
        result, downgrade = pool[:limit], None

    # 截断要分清是哪种：
    # - `limit`：渠道给的多，但按 limit 只截了前几条进筛选池（调大 limit 能拿更多）
    # - `relevance`：进了池子但被 Jev 相关性过滤淘汰（调大 limit 找不回来）
    # 混成一个“截断”会让调用方误以为调大 limit 就行。
    #
    # 只能保守判 relevance：filter_candidates 正常分支最后也是 kept[:limit]，
    # 所以 len(result) == limit 时，“被相关性筛掉”和“恰好被 limit 截掉”区分不开。
    # 宁可不提，也不能把能靠调大 limit 拿回来的说成拿不回来。
    truncated_by: list[str] = []
    if pooled < total_candidates:
        truncated_by.append("limit")
    if rerank_ran and len(result) < pooled and len(result) < limit:
        truncated_by.append("relevance")

    payload: dict = {
        "query": query,
        "attempted_channels": attempted,
        "succeeded_channels": [name for name, _, error in collected if error is None],
        "candidates": result,
        "total": total_candidates,
        "pooled": pooled,
        "returned": len(result),
        "truncated": bool(truncated_by),
        "truncated_by": truncated_by,
    }
    if downgrade == "skipped_no_key":
        payload["rerank"] = "skipped_no_key"
    elif downgrade:
        payload["rerank_downgraded"] = downgrade
    if failed:
        payload["failed_channels"] = [{"channel": name, "error": reason} for name, reason in failed]
    if filtered_by:
        payload["filtered_by"] = filtered_by
    if (_has_positive_x_site_filter(query)
            and any(name == "exa" and error is None and not items for name, items, error in collected)):
        payload["display_notes"] = [EXA_X_EMPTY_NOTE]
    return payload
