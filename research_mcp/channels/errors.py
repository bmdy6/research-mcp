"""Fixed public channel failure categories; never expose upstream exception text."""
from __future__ import annotations

import subprocess

from research_mcp.commands import CommandUnavailableError, HINTS

MESSAGES = {
    "ok": "查询成功", "empty": "查询成功但没有结果",
    "blocked": "上游拒绝请求或要求访问验证",
    "auth_required": "缺少免费凭据或需要登录",
    "quota_exhausted": "免费额度或频率限制已触发；停止请求",
    "parse_error": "上游响应格式无效，不能视为空结果",
    "startup_failed": "渠道依赖不可用或版本不匹配",
    "timeout": "上游超时", "connection_failed": "上游连接失败",
    "unsupported": "渠道尚未实现或名称不受支持",
}
FAILURE_STATES = frozenset(MESSAGES) - {"ok", "empty"}


class ChannelError(RuntimeError):
    def __init__(self, status: str):
        if status not in FAILURE_STATES:
            raise ValueError("Unknown channel status")
        self.status = status
        super().__init__(MESSAGES[status])


def failure_status(error: object) -> str:
    if isinstance(error, ChannelError):
        return error.status
    if isinstance(error, (FileNotFoundError, ImportError, CommandUnavailableError)):
        return "startup_failed"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(error, (ValueError, TypeError)):
        return "parse_error"
    # Some existing HTTP helpers wrap the missing-curl exception as FetchError.
    if str(error) == HINTS["curl"]:
        return "startup_failed"
    text = str(error).casefold()
    if any(x in text for x in ("429", "rate limit", "rate_limit", "resource_exhausted", "限流", "quota")):
        return "quota_exhausted"
    if any(x in text for x in ("401", "not logged in", "auth login", "未登录", "认证", "请先登录")):
        return "auth_required"
    if any(x in text for x in ("403", "forbidden", "captcha", "challenge", "verification", "anti-bot", "验证码", "拒绝")):
        return "blocked"
    if any(x in text for x in ("timeout", "timed out", "超时")):
        return "timeout"
    return "connection_failed"
