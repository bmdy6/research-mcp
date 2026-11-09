"""Fixed actionable diagnostics for optional external executables."""
from __future__ import annotations

import subprocess

HINTS = {
    "gh": "gh 不可用或认证失效；安装：https://cli.github.com；登录：gh auth login",
    "bili": "bili 不可用；安装：uv tool install 'bilibili-cli[audio]'；登录：bili login",
    "curl": "curl 不可用；用系统包管理器安装 curl",
}


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run without changing output; replace missing/login errors with fixed hints."""
    name = command[0]
    try:
        result = subprocess.run(command, **kwargs)
    except FileNotFoundError:
        raise RuntimeError(HINTS.get(name, "外部命令不可用")) from None
    text = (result.stderr or "").lower()
    if result.returncode and name in HINTS and any(
        marker in text for marker in ("auth login", "not logged in", "authentication", "未登录", "请先登录")
    ):
        raise RuntimeError(HINTS[name])
    return result
